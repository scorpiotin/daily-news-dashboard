# -*- coding: utf-8 -*-
"""
每日行业新闻智能看板 — 数据抓取与聚合脚本

用法:
    python fetch_news.py              # 抓取并生成当日数据（覆盖）
    python fetch_news.py --merge      # 增量合并：保留当天已有条目，追加新抓到的
    python fetch_news.py --days 3     # 同时回补最近 3 天（按条目发布时间归档）

输出:
    data/index.js            -> window.__INDEX__  = [ {date, count, updatedAt, emptyDomains} ... ]
    data/day-YYYY-MM-DD.js   -> window.__DAY__    = { date, updatedAt, sections: [...] }

设计原则:
    - 只用标准库，零第三方依赖
    - 单源失败不影响整体；失败源写入日志
    - 每个领域最多 5 条，宁缺毋滥；0 条则该领域标记为「今日无重大更新」
    - 所有条目保留可点击的原文链接，标题保留原文，摘要仅做截断概括
"""

from __future__ import annotations

import argparse
import html
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from urllib.parse import quote, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

MAX_PER_DOMAIN = 5
RECENT_HOURS = 48          # 只收最近 48 小时内的条目
SUMMARY_MAX = 110          # 摘要最大字数

# --------------------------------------------------------------------------
# 领域定义（顺序即前端展示顺序）
# --------------------------------------------------------------------------
DOMAINS = [
    ("world",        "时政要闻",   "国内外大事、政策动向、外交与治理"),
    ("finance",      "财经金融",   "宏观经济、股市汇市、机构与监管"),
    ("tech",         "科技与AI",   "大模型、芯片、互联网与前沿研究"),
    ("film",         "电影行业",   "影视档期、票房、制片与发行"),
    ("game",         "游戏行业",   "新游、版号、厂商与电竞"),
    ("sports",       "体育行业",   "赛事结果、转会、商业与赛事运营"),
    ("aerospace",    "航空航天",   "航天发射、民航、卫星与深空探测"),
    ("petrochem",    "石油化工",   "原油地缘、炼化、化工品与装置动态"),
    ("energy",       "能源电力",   "电力、新能源、储能与电网"),
    ("health",       "医疗健康",   "药品器械、临床、医保与公共卫生"),
    ("edu",          "教育",       "政策、升学、高校与职业教育"),
    ("auto",         "交通汽车",   "整车、新能源车、供应链与出行"),
    ("retail",       "消费零售",   "消费趋势、电商、品牌与渠道"),
    ("realestate",   "房地产",     "楼市政策、土拍、房企与租赁"),
    ("law",          "法律政策",   "立法、司法解释、监管处罚与合规"),
    ("climate",      "天气与气候", "自然灾害、极端天气、气温变化与气候政策"),
    ("public_safety","公共安全",   "治安刑案、生产安全事故、火灾爆炸与应急处置"),
    ("douyin",       "抖音热搜",   "抖音实时热榜，全网正在讨论什么"),
]
DOMAIN_IDS = [d[0] for d in DOMAINS]

# 每领域入选上限（石化放宽，因为地缘与化工两条线都算）
MAX_BY_DOMAIN = {d: 10 for d in DOMAIN_IDS}
MAX_BY_DOMAIN["petrochem"] = 10
MAX_BY_DOMAIN["douyin"] = 20          # 榜单性质，取前 20 条

# 当天全量留档上限：每个领域最多保留 20 条。
# 一天三次抓取会按「级别 → 时间」择优合并，超过 20 条的尾部自动裁掉，
# 既保证当天有多批次抓取的余量，又不让单日文件无限膨胀。
# 抖音是榜单（见 SPECIAL_DOMAINS），取实时前 20，不参与历史补档。
ARCHIVE_CAP_BY_DOMAIN = {d: 20 for d in DOMAIN_IDS}
ARCHIVE_CAP_BY_DOMAIN["douyin"] = 20

SPECIAL_DOMAINS = {"douyin"}          # 不走关键词分类，由专用抓取器产出

# --------------------------------------------------------------------------
# 新闻源清单: (显示名, URL, 默认领域, 权重)
# 默认领域为 None 表示综合源，完全依赖关键词分类
# --------------------------------------------------------------------------
SOURCES = [
    ("中国新闻网", "https://www.chinanews.com.cn/rss/scroll-news.xml", None, 9),
    ("中国新闻网", "https://www.chinanews.com.cn/rss/china.xml",       None, 8),
    ("中国新闻网", "https://www.chinanews.com.cn/rss/world.xml",       "world", 8),
    ("中国新闻网", "https://www.chinanews.com.cn/rss/finance.xml",     "finance", 8),
    ("中国新闻网", "https://www.chinanews.com.cn/rss/it.xml",          "tech", 7),
    ("中国新闻网", "https://www.chinanews.com.cn/rss/ent.xml",         "film", 7),
    ("中国新闻网", "https://www.chinanews.com.cn/rss/sports.xml",      "sports", 7),
    ("中国新闻网", "https://www.chinanews.com.cn/rss/energy.xml",      "energy", 7),
    ("中国新闻网", "https://www.chinanews.com.cn/rss/health.xml",      "health", 7),
    ("中国新闻网", "https://www.chinanews.com.cn/rss/edu.xml",         "edu", 7),
    ("中国新闻网", "https://www.chinanews.com.cn/rss/auto.xml",        "auto", 7),
    ("中国新闻网", "https://www.chinanews.com.cn/rss/culture.xml",     "film", 6),
    ("中国新闻网", "https://www.chinanews.com.cn/rss/mil.xml",         "aerospace", 5),
    ("IT之家",     "https://www.ithome.com/rss/",                      "tech", 7),
    ("量子位",     "https://www.qbitai.com/feed",                      "tech", 6),
    ("雷锋网",     "https://www.leiphone.com/feed",                    "tech", 5),
    ("PingWest",   "https://www.pingwest.com/feed",                    "tech", 5),
    ("36氪",       "https://36kr.com/feed",                            "retail", 6),
    ("界面新闻",   "https://a.jiemian.com/index.php?m=article&a=rss",  None, 6),
    ("澎湃新闻",   "https://www.thepaper.cn/rss_newsDetail_25950.xml", "world", 7),
    ("联合早报",   "https://www.zaobao.com.sg/rss/realtime/china",     "world", 6),
    ("机核网",     "https://www.gcores.com/rss",                       "game", 6),
    ("芥末堆",     "https://www.jiemodui.com/rss",                     "edu", 5),
    ("汽车之家",   "https://www.autohome.com.cn/rss",                  "auto", 5),
    ("钛媒体",     "https://www.tmtpost.com/rss",                      "tech", 6),
    ("ESPN",       "https://www.espn.com/espn/rss/news",               "sports", 4),
]

# --------------------------------------------------------------------------
# HTML 列表页源：垂直站普遍没有 RSS，只能从列表页抽链接
# (显示名, 列表页URL, 默认领域, 权重, 链接正则)
# --------------------------------------------------------------------------
HTML_SOURCES = [
    ("卓创资讯",     "https://www.sci99.com/",               "petrochem", 9,
     r"/info/3_\d+_\d+\.html"),
    ("中国能源网",   "https://www.china5e.com/",             None,        7,
     r"/news/news-\d+-\d+\.html"),
    ("中国石化新闻网", "https://www.sinopecnews.com.cn/",     "petrochem", 8,
     r"/(?:xnews|zhuanti)/content/content_\d+\.html"),
]
TOTAL_SOURCES = len(SOURCES) + len(HTML_SOURCES) + 1   # +1 为抖音热搜接口

# --------------------------------------------------------------------------
# 关键词分类: 领域 -> [(词, 分)]
# 强特征词 3 分，中 2 分，弱 1 分；某领域累计 >= 2 分才判定归入该领域
# --------------------------------------------------------------------------
KEYWORDS = {
    "petrochem": [(w, 3) for w in [
        # 原油与地缘
        "原油", "石油", "炼化", "炼油", "油田", "油气", "油轮", "输油", "钻井", "油价",
        "OPEC", "布伦特", "WTI", "减产", "霍尔木兹", "红海", "马六甲", "原油出口",
        "炼厂", "开工率", "地缘", "制裁", "能源安全",
        # 化工品与装置
        "乙烯", "丙烯", "聚丙烯", "聚乙烯", "PX", "PTA", "乙二醇", "甲醇", "苯乙烯",
        "纯碱", "烧碱", "尿素", "磷肥", "钾肥", "化肥", "橡胶", "树脂", "涂料", "塑料",
        "化纤", "涤纶", "锦纶", "己内酰胺", "芳烃", "裂解", "沥青", "柴油", "汽油",
        # 品种口径（对齐隆众、卓创的跟踪范围）
        "石脑油", "丙烷", "丁烷", "MTBE", "环氧丙烷", "顺酐", "燃料油", "渣油", "基础油",
        "润滑油", "石蜡", "丁二烯", "丁腈橡胶", "聚氨酯", "双氧水", "钛白粉", "纯苯",
        "甲苯", "二甲苯", "苯酚", "丙酮", "醋酸", "辛醇", "增塑剂", "PVC", "ABS", "EVA",
        "聚酯", "长丝", "短纤", "瓶片", "再生料", "液化气", "成品油", "地炼", "成品油价",
        "化工品", "化学品", "装置", "检修", "产能投放", "船货", "库存",
        "化工厂", "化工园区", "炼化装置", "油气管道",
    ]] + [(w, 2) for w in ["化工", "产能", "报价", "期货", "进出口", "停产"]],
    "energy": [(w, 3) for w in [
        "电力", "电网", "发电", "核电", "风电", "光伏", "储能", "水电", "火电", "千瓦",
        "装机", "输配电", "特高压", "绿电", "氢能", "生物质", "碳交易", "碳排放", "煤电",
        "矿井", "煤矿", "矿难", "电厂", "变电站", "增产保供", "煤炭", "煤价", "电力现货", "增产保供", "煤炭", "煤价", "电力现货",
    ]] + [(w, 2) for w in ["能源", "碳中和", "电价", "用电量", "电站", "电池", "停产"]],
    "aerospace": [(w, 3) for w in [
        "航天", "火箭", "卫星", "空间站", "载人", "探测器", "探月", "火星", "运载",
        "发射场", "太空", "北斗", "星链", "飞船", "宇航员", "空间科学", "深空", "航班",
    ]] + [(w, 2) for w in ["民航", "航空", "飞机", "机场", "航线", "C919", "波音", "空客", "无人机"]],
    "film": [(w, 3) for w in [
        "票房", "影片", "电影", "导演", "院线", "定档", "上映", "剧组", "拍摄", "首映",
        "影视", "影帝", "影后", "预告片", "剧集", "主演", "出品方", "戛纳", "金鸡", "奥斯卡",
    ]] + [(w, 2) for w in ["演员", "编剧", "纪录片", "动画电影", "影院", "流媒体"]],
    "game": [(w, 3) for w in [
        "游戏", "版号", "手游", "主机", "电竞", "玩家", "Steam", "PlayStation", "任天堂",
        "索尼", "腾讯游戏", "网易游戏", "米哈游", "开服", "公测", "内测", "赛季", "联机",
    ]] + [(w, 2) for w in ["Xbox", "独立游戏", "二次元", "关卡", "服务器"]],
    "sports": [(w, 3) for w in [
        "比赛", "赛事", "冠军", "夺冠", "联赛", "球员", "球队", "主场", "客场", "半决赛",
        "决赛", "世界杯", "奥运", "全运", "亚运", "NBA", "CBA", "中超", "英超", "西甲",
        "进球", "转会", "主帅", "赛季", "奖牌", "运动员", "田径", "游泳",
    ]] + [(w, 2) for w in ["俱乐部", "教练", "体育", "球迷", "排名榜"]],
    "health": [(w, 3) for w in [
        "医疗", "医院", "药品", "临床", "疫苗", "疾病", "患者", "诊疗", "药企", "医保",
        "器械", "手术", "癌症", "糖尿病", "心血管", "流感", "病毒", "感染", "卫健",
        "创新药", "集采", "药监", "门诊", "住院",
    ]] + [(w, 2) for w in ["医生", "健康", "公共卫生", "筛查", "处方"]],
    "edu": [(w, 3) for w in [
        "教育", "高校", "学生", "教师", "招生", "高考", "考研", "学位", "课程", "校园",
        "中小学", "职教", "留学", "开学", "教材", "教育局", "部属", "毕业",
    ]] + [(w, 2) for w in ["学校", "大学", "培训", "考试", "录取"]],
    "auto": [(w, 3) for w in [
        "汽车", "新能源车", "电动车", "整车", "车企", "车型", "续航", "充电桩", "智能驾驶",
        "自动驾驶", "车规", "动力电池", "比亚迪", "特斯拉", "蔚来", "理想汽车", "小鹏",
        "销量", "召回", "车展", "乘用车", "商用车", "整车厂",
    ]] + [(w, 2) for w in ["出行", "车主", "高速", "铁路", "地铁", "车企"]],
    "realestate": [(w, 3) for w in [
        "楼市", "房地产", "房企", "房价", "房贷", "土地", "土拍", "楼盘", "购房", "限购",
        "限贷", "公积金", "住房", "商品房", "二手房", "租赁", "保障房", "开发商",
        "住建部", "住房城乡建设", "不动产", "存量房", "城中村", "保交楼", "去化",
    ]] + [(w, 2) for w in ["物业", "交楼", "成交面积", "住宅", "写字楼"]],
    "retail": [(w, 3) for w in [
        "零售", "电商", "消费", "品牌", "门店", "连锁", "商超", "直播带货", "双十",
        "促销", "快消", "餐饮", "新消费", "客单价", "会员店", "折扣店",
    ]] + [(w, 2) for w in ["销量", "渠道", "供应链", "外卖", "电商平台"]],
    "law": [(w, 3) for w in [
        "立法", "法案", "条例", "司法解释", "判决", "裁定", "起诉", "逮捕", "违法",
        "处罚", "合规", "监管", "反垄断", "知识产权", "法院", "检察院", "公安",
        "刑法", "民法典", "行政处罚", "约谈", "立案",
    ]] + [(w, 2) for w in ["规定", "办法", "制度", "维权", "诉讼"]],
    "climate": [(w, 3) for w in [
        "台风", "暴雨", "洪水", "地震", "干旱", "高温", "寒潮", "暴雪", "冰雹", "山洪",
        "泥石流", "极端天气", "厄尔尼诺", "拉尼娜", "气候变化", "全球变暖", "气温纪录",
        "气象台", "森林火灾", "海啸", "汛情", "山火", "野火", "火灾蔓延",
    ]] + [(w, 2) for w in [
        "气象", "天气", "降雨", "降温", "大雾", "受灾", "气温", "预警", "灾害", "防汛", "抗旱",
    ]],
    "public_safety": [(w, 4) for w in [
        # 治安与暴力
        "枪击", "恐袭", "恐怖袭击", "劫持", "踩踏", "命案", "枪战", "爆炸案", "袭击事件",
        # 生产安全事故（从各行业并入）
        "爆炸", "起火", "坍塌", "垮塌", "燃爆", "爆燃", "瓦斯爆炸", "矿难",
        "透水事故", "中毒事故", "泄漏事故", "坠机", "坠毁", "空难", "直升机失事",
        "重大事故", "较大事故", "安全事故",
    ]] + [(w, 3) for w in [
        "刑事案件", "嫌犯", "持刀伤人", "缉毒", "绑架", "治安", "警情通报", "暴力事件",
        "人口贩运", "诈骗团伙", "涉黑", "凶手", "遇袭", "失踪案",
        "泄漏", "中毒", "塌方", "起火点", "失事", "翻车", "撞车", "交通事故",
        "遇险", "被困", "搜救",
    ]] + [(w, 2) for w in [
        "警方", "嫌疑人", "逮捕", "伤亡", "遇难", "失联", "救援", "事故", "火灾", "案件",
        "安全事件", "嫌犯落网", "应急处置", "伤者", "暂无生命危险",
    ]],
    "finance": [(w, 3) for w in [
        "股市", "A股", "港股", "美股", "债券", "汇率", "人民币", "降息", "加息", "央行",
        "证监会", "基金", "IPO", "上市公司", "财报", "净利", "融资", "市值",
        "涨跌", "涨停", "跌停", "国债", "美联储", "通胀", "GDP", "CPI", "PMI",
    ]] + [(w, 2) for w in ["金融", "投资", "银行", "保险", "证券", "货币", "营收"]],
    "tech": [(w, 3) for w in [
        "人工智能", "大模型", "AI", "芯片", "算力", "半导体", "英伟达", "OpenAI", "算法",
        "开源", "云计算", "数据中心", "机器人", "量子计算", "操作系统", "处理器", "光刻",
        "5G", "6G", "区块链", "智能体", "多模态",
    ]] + [(w, 2) for w in ["科技", "互联网", "软件", "应用", "硬件", "开发"]],
    "world": [(w, 3) for w in [
        "总统", "总理", "外交", "联合国", "白宫", "国会", "制裁", "会谈",
        "领导人", "国务院", "全国人大", "政协", "中共中央", "外交部", "国防",
        "大选", "选举", "条约", "国事访问", "首相", "议会", "安理会",
    ]] + [(w, 2) for w in ["政策", "会议", "政府", "部委", "国际", "峰会", "论坛"]],
}

# 重要性信号词
IMPORTANT_WORDS = [
    "重磅", "首次", "首个", "突破", "历史新高", "新高", "暴跌", "暴涨", "涨停", "跌停",
    "宣布", "签署", "批准", "获批", "发布", "落地", "启动", "全面", "重大", "紧急",
    "突发", "预警", "决议", "判决", "并购", "停产", "减产", "事故", "撤回", "叫停",
    "刷新", "创下", "超预期", "不及预期", "裁员", "破产", "退市", "解禁",
]

# 明显不适合入板的条目（招聘、广告类）
BLOCK_WORDS = ["招聘", "讣告", "广告", "直播预告", "图片故事"]

# 软新闻 / 活动通稿黑名单 —— 这类稿件数量大但没有信息价值，直接丢弃
SOFT_BLOCK = [
    "采摘季", "采摘节", "丰收节", "新书发布", "新书首发", "读书分享", "签售",
    "走访交流", "交流活动", "启幕", "启幕", "音乐会", "交响", "京剧", "越剧", "戏曲",
    "书画展", "摄影展", "艺术展", "非遗", "民俗", "文旅节", "文化节", "旅游节",
    "诗歌", "散文集", "美术作品", "文艺演出", "演唱会", "粉丝见面会", "探班",
    "美食节", "厨艺", "选美", "时装周走秀", "亲子活动", "公益跑", "健步走",
    "音乐剧", "乐团", "小提琴", "钢琴家", "舞蹈", "美术馆", "歌剧院", "话剧",
    "作家", "文学", "图书", "读书", "书法", "篆刻", "收藏展", "民俗节",
    "焕发", "新活力", "可触可感", "活教材", "自媒体", "感受", "探访", "走读",
    "打卡", "研学", "沉浸式体验", "文旅融合", "视频号", "直播间",
]
# 含以上词语但仍可能是重要新闻的豁免词
SOFT_EXEMPT = ["进博会", "峰会", "论坛开幕", "大会开幕", "运动会", "全运会", "亚运会"]



# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------
def clean_text(raw: str) -> str:
    if not raw:
        return ""
    s = re.sub(r"<[^>]+>", " ", raw)
    s = html.unescape(s)
    s = s.replace("\u3000", " ").replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^(中新网|新华社|央视新闻|界面新闻|澎湃新闻)\s*[\d月日電电]*\s*[：:]\s*", "", s)
    s = re.sub(r"^(最新推荐|热点关注|要闻|焦点|推荐|关注)\s*[|｜丨]\s*", "", s)
    s = re.sub(r"^(最新推荐|热点关注|要闻|焦点|推荐|关注)\s*[|｜丨]\s*", "", s)
    s = re.sub(r"^[\d]{1,2}月[\d]{1,2}日[电讯]?\s*", "", s)
    return s.strip()


def truncate(text: str, limit: int = SUMMARY_MAX) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ("。", "！", "？", "；", "，"):
        idx = cut.rfind(sep)
        if idx > limit * 0.55:
            return cut[: idx + 1]
    return cut.rstrip() + "…"


def parse_time(value: str) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    try:
        dt = parsedate_to_datetime(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(v, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ)
            return dt.astimezone(TZ)
        except Exception:
            continue
    return None


def norm_title(t: str) -> str:
    return re.sub(r"[\s\W_]+", "", t or "")


def bigrams(s: str) -> set[str]:
    s = norm_title(s)
    return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def similar(a: str, b: str) -> float:
    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


def fetch(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


def strip_ns(tag: str) -> str:
    return tag.split("}")[-1].lower() if "}" in tag else tag.lower()


def parse_feed(raw: bytes) -> list[dict]:
    """兼容 RSS 2.0 / Atom"""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(raw)
    except Exception:
        return []

    items: list[dict] = []
    for node in root.iter():
        if strip_ns(node.tag) not in ("item", "entry"):
            continue
        rec = {"title": "", "link": "", "desc": "", "pub": ""}
        for child in node:
            tag = strip_ns(child.tag)
            text = (child.text or "").strip()
            if tag == "title":
                rec["title"] = text
            elif tag == "link":
                rec["link"] = text or child.attrib.get("href", "")
            elif tag in ("description", "summary", "content"):
                if not rec["desc"] or len(text) > len(rec["desc"]):
                    rec["desc"] = text
            elif tag in ("pubdate", "published", "updated", "date"):
                if not rec["pub"]:
                    rec["pub"] = text
        if rec["title"] and rec["link"]:
            items.append(rec)
    return items


# --------------------------------------------------------------------------
# HTML 列表页解析（垂直站没有 RSS 时的兜底）
# --------------------------------------------------------------------------
HTML_A = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S | re.I)
HREF_RE = re.compile(r"""href\s*=\s*['"]([^'"]+)['"]""", re.I)
ATTR_TITLE_RE = re.compile(r"""\btitle\s*=\s*['"]([^'"]+)['"]""", re.I)


def parse_cn_date(text: str, now: datetime) -> datetime | None:
    """从标题或 URL 里抠日期：20261105 / 2026-11-05 / 11月5日"""
    m = re.search(r"(20\d{2})[-/]?(\d{1,2})[-/]?(\d{1,2})", text)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            dt = datetime(y, mo, d, 9, 0, tzinfo=TZ)
            if dt <= now + timedelta(days=1) and (now - dt) < timedelta(days=60):
                return dt
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        try:
            dt = datetime(now.year, mo, d, 9, 0, tzinfo=TZ)
            return dt if dt <= now + timedelta(days=2) else dt.replace(year=now.year - 1)
        except ValueError:
            pass
    return None


def parse_html_list(raw: bytes, link_re: str, base: str, now: datetime) -> list[dict]:
    """从列表页 HTML 抽条目；同一 URL 出现多次时合并标题与摘要"""
    text = None
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", "ignore")

    out: dict[str, dict] = {}
    for m in HTML_A.finditer(text):
        attrs, inner = m.group(1), m.group(2)
        hm = HREF_RE.search(attrs)
        if not hm:
            continue
        href = hm.group(1).strip()
        if not re.search(link_re, href, re.I):
            continue
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = urljoin(base, href)
        if not href.startswith("http"):
            continue

        inner_txt = clean_text(inner)
        tm = ATTR_TITLE_RE.search(attrs)
        attr_txt = clean_text(tm.group(1)) if tm else ""
        # title 属性才是完整标题；inner 里可能是被 CSS 截断的标题或摘要
        if attr_txt:
            cand_title, cand_desc, from_attr = attr_txt, (inner_txt if inner_txt != attr_txt else ""), True
        else:
            cand_title, cand_desc, from_attr = inner_txt, "", False
        if len(cand_title) < 6:
            continue

        rec = out.get(href)
        if rec is None:
            pub = parse_cn_date(f"{cand_title} {href}", now)
            out[href] = {
                "title": cand_title,
                "fromAttr": from_attr,
                "link": href,
                "desc": cand_desc,
                "pub": pub.isoformat(timespec="seconds") if pub else "",
            }
        else:
            if from_attr and not rec["fromAttr"]:
                if rec["title"] != cand_title and len(rec["title"]) > len(rec["desc"]):
                    rec["desc"] = rec["title"]
                rec["title"], rec["fromAttr"] = cand_title, True
            elif not from_attr and len(cand_title) > len(rec["title"]) and not rec["fromAttr"]:
                rec["title"] = cand_title
            if cand_desc and cand_desc != rec["title"] and len(cand_desc) > len(rec["desc"]):
                rec["desc"] = cand_desc
    return list(out.values())


META_DESC = re.compile(r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']{20,})', re.I)
META_DESC_REV = re.compile(r'<meta[^>]*content=["\']([^"\']{20,})["\'][^>]*name=["\']description["\']', re.I)
P_BLOCK = re.compile(r"<p[^>]*>(.*?)</p>", re.S | re.I)
# 站点模板话术，不是真摘要
BOILER_PLATE = ["卓创资讯提供", "为您提供", "洞悉市场", "明确发展趋势", "全面了解市场动态"]


def fetch_detail_summary(url: str, timeout: int = 15) -> str:
    """详情页取一句话摘要：优先 meta description，其次正文首段"""
    try:
        raw = fetch(url, timeout)
    except Exception:
        return ""
    text = raw.decode("utf-8", "ignore")
    if "charset=gb" in text[:3000].lower():
        text = raw.decode("gb18030", "ignore")

    cands: list[str] = []
    m = META_DESC.search(text) or META_DESC_REV.search(text)
    if m:
        cands.append(clean_text(html.unescape(m.group(1))))
    for p in P_BLOCK.findall(text)[:14]:
        t = clean_text(p)
        if len(t) >= 25:
            cands.append(t)
            break

    for c in cands:
        if len(c) >= 20 and not any(b in c for b in BOILER_PLATE):
            return truncate(c, SUMMARY_MAX)
    return ""


# --------------------------------------------------------------------------
# 分类与打分
# --------------------------------------------------------------------------
# 事故强信号：命中即判为公共安全
ACCIDENT_STRONG = [
    "爆炸", "起火", "火灾", "坍塌", "垮塌", "燃爆", "爆燃", "瓦斯", "矿难", "透水",
    "泄漏", "中毒", "坠机", "坠毁", "空难", "失事", "撞车", "翻车", "踩踏", "伤亡",
    "遇难", "被困", "失联", "警情通报", "枪击", "持刀伤人", "命案", "凶案",
]
# 例外：这些语义下不属于安全事故，仍归原领域
ACCIDENT_EXEMPT = [
    "森林火灾", "山火", "野火", "火灾隐患", "防火", "消防演练", "应急演练",
    "爆炸物", "爆炸装置", "未爆炸", "火灾保险", "火险", "爆炸极限",
    "泄漏率", "泄漏检测", "零泄漏", "中毒性", "无毒",
]


def _is_safety_event(title: str, desc: str) -> bool:
    """判断是否为需要归入公共安全的事故/警情类新闻"""
    if any(w in title for w in ACCIDENT_EXEMPT):
        return False
    # 标题命中强信号即成立
    if any(w in title for w in ACCIDENT_STRONG):
        return True
    # 标题没有、摘要命中多个强信号也成立（列表页常把事件写在正文里）
    hits = sum(1 for w in ACCIDENT_STRONG if w in desc)
    return hits >= 2


def classify(title: str, desc: str, default_domain: str | None) -> tuple[str | None, int]:
    """返回 (领域, 置信度)。返回 None 表示不属于任何领域，应丢弃。

    规则：
      1. 垂直源（有 default_domain）默认信任自己的频道，只有当标题被其他领域
         以极强特征（>=6 分）命中时才改判；
      2. 综合源必须标题命中（>=2）或摘要强命中（>=6）才归类，否则丢弃，
         不再无脑塞进「时政要闻」；
    """
    # 前置判定：生产安全事故与治安警情一律归「公共安全」，
    # 避免化工厂爆炸被归进石化、矿井事故被归进能源、坠机被归进航天。
    if _is_safety_event(title, desc):
        return "public_safety", 8

    title_scores: dict[str, int] = {}
    blob_scores: dict[str, int] = {}
    for domain, words in KEYWORDS.items():
        t_hit = sum(w for word, w in words if word in title)
        b_hit = t_hit + sum(w for word, w in words if word in desc) * 0.5
        if t_hit:
            title_scores[domain] = t_hit
        if b_hit:
            blob_scores[domain] = b_hit

    # 垂直源（有明确频道归属）
    if default_domain:
        own = title_scores.get(default_domain, 0)
        others = {d: v for d, v in title_scores.items() if d != default_domain}
        if others:
            bd, bv = max(others.items(), key=lambda kv: kv[1])
            # 标题压根不是本频道主题，且被其他领域明确命中 → 改判
            if own == 0 and bv >= 3:
                return bd, bv
            # 其他领域压倒性命中 → 改判
            if bv >= 6 and bv >= own * 2:
                return bd, bv
        if own >= 1:
            return default_domain, own
        # 标题没有任何本领域特征词，看摘要兜底；兜不住就丢弃，避免软新闻占位
        if blob_scores.get(default_domain, 0) >= 2:
            return default_domain, 1
        return None, 0

    # 综合源：标题命中优先
    if title_scores:
        best_d, best_v = max(title_scores.items(), key=lambda kv: kv[1])
        if best_v >= 2:
            return best_d, best_v

    # 标题不明显命中，退回摘要强命中
    if blob_scores:
        best_d, best_v = max(blob_scores.items(), key=lambda kv: kv[1])
        if best_v >= 6:
            return best_d, int(best_v)

    return None, 0


def importance(title: str, desc: str, source_weight: int, pub: datetime | None, now: datetime) -> float:
    score = source_weight * 0.6

    hits = sum(1 for w in IMPORTANT_WORDS if w in title)
    score += min(hits * 1.4, 4.2)

    # 标题信息量：太短（口号式）减分，适中加分
    tl = len(title)
    if tl < 8:
        score -= 1.5
    elif 14 <= tl <= 40:
        score += 1.2

    # 摘要充实说明是正经报道；HTML 源本就无摘要，不因此扣分
    if len(desc) >= 40:
        score += 1.0
    elif desc and len(desc) < 12:
        score -= 0.8

    # 新鲜度：2 小时内 +2，8 小时内 +1，其余 0
    if pub:
        hours = (now - pub).total_seconds() / 3600
        if hours <= 2:
            score += 2.0
        elif hours <= 8:
            score += 1.0
        elif hours > 30:
            score -= 1.5

    return round(score, 2)


# P0 紧急信号：命中且发布时间在 6 小时内，直接定为最高等级
URGENT_WORDS = [
    "突发", "紧急", "枪击", "恐袭", "恐怖袭击", "劫持", "踩踏", "坠毁", "空难", "失事",
    "地震", "台风登陆", "红色预警", "爆炸", "坍塌", "熔断", "暴跌", "暴涨",
    "发射失败", "召回", "禁售", "辞职", "罢免", "制裁生效",
]

DOMESTIC_MARKERS = [
    "我国", "中国", "全国", "北京", "上海", "天津", "重庆", "广东", "江苏", "浙江", "山东",
    "河南", "四川", "湖北", "湖南", "福建", "安徽", "河北", "陕西", "辽宁", "江西", "云南",
    "广西", "山西", "黑龙江", "吉林", "贵州", "甘肃", "内蒙古", "新疆", "西藏", "宁夏",
    "青海", "海南", "香港", "澳门", "台湾", "深圳", "广州", "杭州", "成都", "武汉", "西安",
    "发改委", "工信部", "商务部", "财政部", "央行", "教育部", "证监会", "住建部",
]
FOREIGN_MARKERS = [
    "美国", "俄罗斯", "乌克兰", "以色列", "伊朗", "加沙", "黎巴嫩", "叙利亚", "也门", "沙特",
    "日本", "韩国", "朝鲜", "印度", "巴基斯坦", "越南", "泰国", "印尼", "菲律宾", "新加坡",
    "英国", "法国", "德国", "意大利", "西班牙", "荷兰", "瑞士", "瑞典", "波兰", "土耳其",
    "巴西", "阿根廷", "墨西哥", "加拿大", "澳大利亚", "新西兰", "南非", "埃及", "尼日利亚",
    "欧盟", "北约", "联合国", "白宫", "五角大楼", "克里姆林宫", "唐宁街",
    "特朗普", "拜登", "普京", "泽连斯基", "内塔尼亚胡", "马克龙", "中东", "欧洲",
    "美洲", "非洲", "东南亚", "中亚", "拉美", "G7", "G20",
]
AREA_MARKERS = [
    ("中东", ["中东", "伊朗", "以色列", "加沙", "沙特", "也门", "黎巴嫩", "叙利亚", "霍尔木兹", "红海"]),
    ("欧洲", ["欧洲", "欧盟", "英国", "法国", "德国", "意大利", "西班牙", "荷兰", "俄罗斯", "乌克兰", "北约"]),
    ("美洲", ["美国", "加拿大", "巴西", "阿根廷", "墨西哥", "白宫", "五角大楼", "拉美"]),
    ("亚太", ["日本", "韩国", "朝鲜", "印度", "越南", "泰国", "印尼", "菲律宾", "新加坡", "澳大利亚", "东南亚"]),
    ("非洲", ["非洲", "埃及", "尼日利亚", "南非"]),
]
# 无地名但明确指向国际市场的信号
OVERSEAS_TOPIC = ["OPEC", "布伦特", "WTI", "霍尔木兹", "红海", "马六甲", "原油出口", "欧元区", "美联储",
                  "国际油价", "国际原油", "全球油价", "纽约油价", "伦敦油价"]


def _has(word: str, text: str) -> bool:
    """英文词大小写不敏感匹配，中文直接包含"""
    if word.isascii():
        return word.lower() in text.lower()
    return word in text


def extract_keywords(title: str, desc: str, domain: str, limit: int = 4) -> list[str]:
    """从标题命中的领域特征词里取前若干个做关键词"""
    words = KEYWORDS.get(domain, [])
    hits = [w for w, _ in words if _has(w, title)]
    if len(hits) < limit:
        hits += [w for w, _ in words if w not in hits and _has(w, desc)]
    # 短词若被更长的命中词包含则丢弃（"外交"让位给"外交部"）
    hits = [w for w in hits if not any(w != o and w in o for o in hits)]
    seen, out = set(), []
    for w in hits:
        if w not in seen:
            seen.add(w)
            out.append(w)
        if len(out) >= limit:
            break
    return out


def detect_region(title: str, desc: str, domain: str) -> str:
    blob = f"{title} {desc}"
    d_hits = sum(1 for m in DOMESTIC_MARKERS if m in blob)
    f_hits = sum(1 for m in FOREIGN_MARKERS if m in blob)
    if d_hits == 0 and f_hits == 0:
        if domain in ("petrochem", "world", "aerospace", "finance") and any(
            _has(w, title) for w in OVERSEAS_TOPIC
        ):
            return "国际"
        return "国内"
    if d_hits >= f_hits:
        return "国内"
    for area, marks in AREA_MARKERS:
        if any(m in blob for m in marks):
            return area
    return "国际"


def grade_importance(title: str, score: float, verified: int, pub: datetime | None, now: datetime) -> str:
    """P0 紧急 / P1 重要 / P2 关注"""
    hours = (now - pub).total_seconds() / 3600 if pub else 99
    urgent = any(_has(w, title) for w in URGENT_WORDS)
    if urgent and hours <= 6:
        return "P0"
    if verified >= 2 or score >= 9.0:
        return "P1"
    return "P2"


# --------------------------------------------------------------------------
# 抖音热搜（不走 RSS，直接调公开榜单接口）
# --------------------------------------------------------------------------
DOUYIN_API = "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/"
# 接口 label 含义：1/3 热，5 新，9 沸，16 荐，0 无标记
DOUYIN_LABEL = {1: "热", 3: "热", 5: "新", 9: "沸", 16: "荐"}


def fmt_heat(v: int) -> str:
    if v >= 100000000:
        return f"{v / 100000000:.1f}亿"
    if v >= 10000:
        return f"{v / 10000:.1f}万"
    return str(v)


def fetch_douyin_hot(now: datetime) -> list[dict]:
    """抓取抖音实时热榜，返回榜单条目（保留排名与热值）"""
    import json as _json
    raw = fetch(DOUYIN_API, timeout=20)
    data = _json.loads(raw.decode("utf-8", errors="ignore"))
    words = data.get("word_list") or []
    active = data.get("active_time") or now.strftime("%Y-%m-%d %H:%M:%S")
    try:
        active_dt = datetime.strptime(active, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
    except Exception:
        active_dt = now

    out: list[dict] = []
    for idx, w in enumerate(words[:24], 1):
        word = (w.get("word") or "").strip()
        if not word:
            continue
        heat = int(w.get("hot_value") or 0)
        label = DOUYIN_LABEL.get(w.get("label"), "")
        out.append({
            "title": word,
            "url": "https://www.douyin.com/search/" + quote(word),
            "summary": "",
            "source": "抖音热搜",
            "sourceWeight": 6,
            "domain": "douyin",
            "confidence": 9,
            "keywords": [],
            "region": "国内",
            "time": active_dt.isoformat(timespec="seconds"),
            "fetchedAt": now.isoformat(timespec="seconds"),
            "score": round(max(1.0, 12 - idx * 0.35), 2),
            "keys": norm_title(word),
            "rank": idx,
            "heat": heat,
            "heatText": fmt_heat(heat),
            "heatLabel": label,
        })
    return out


# --------------------------------------------------------------------------
# 抓取主流程
# --------------------------------------------------------------------------
def collect(now: datetime) -> tuple[list[dict], list[dict]]:
    results: list[dict] = []
    errors: list[dict] = []

    def work(task):
        kind, src = task
        name, url, default_domain, weight = src[:4]
        try:
            raw = fetch(url)
            if kind == "feed":
                items = parse_feed(raw)
            else:
                items = parse_html_list(raw, src[4], url, now)
            return name, url, default_domain, weight, items, None
        except Exception as exc:  # noqa: BLE001
            return name, url, default_domain, weight, [], f"{type(exc).__name__}: {exc}"

    tasks = [("feed", s) for s in SOURCES] + [("html", s) for s in HTML_SOURCES]
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(work, t) for t in tasks]
        for fut in as_completed(futures):
            name, url, default_domain, weight, items, err = fut.result()
            if err:
                errors.append({"source": name, "url": url, "error": err})
                continue
            for it in items:
                title = clean_text(it["title"])
                if not title or len(title) < 4:
                    continue
                if any(b in title for b in BLOCK_WORDS):
                    continue
                if any(s in title for s in SOFT_BLOCK) and not any(e in title for e in SOFT_EXEMPT):
                    continue
                desc = truncate(clean_text(it["desc"]))
                pub = parse_time(it["pub"])
                if pub and (now - pub) > timedelta(hours=RECENT_HOURS):
                    continue
                link = it["link"].strip()
                if not link.startswith("http"):
                    continue
                domain, conf = classify(title, desc, default_domain)
                if not domain:
                    continue
                results.append({
                    "title": title,
                    "url": link,
                    "summary": desc,
                    "source": name,
                    "sourceWeight": weight,
                    "domain": domain,
                    "confidence": conf,
                    "keywords": extract_keywords(title, desc, domain),
                    "region": detect_region(title, desc, domain),
                    "time": (pub or now).isoformat(timespec="seconds"),
                    "fetchedAt": now.isoformat(timespec="seconds"),
                    "score": importance(title, desc, weight, pub, now),
                    "keys": norm_title(title),
                })
    return results, errors


def dedupe(items: list[dict]) -> list[dict]:
    """同事件合并：按领域分组内比对，保留权重/分数更高的那条，并累加多源报道信号"""
    buckets: dict[str, list[dict]] = {}
    seen_urls: set[str] = set()

    for it in sorted(items, key=lambda x: (-x["sourceWeight"], -x["score"])):
        if it["url"] in seen_urls:
            continue
        bucket = buckets.setdefault(it["domain"], [])
        dup = next((k for k in bucket if similar(k["title"], it["title"]) >= 0.72), None)
        if dup is not None:
            dup["verifiedCount"] = dup.get("verifiedCount", 1) + 1
            dup["score"] = round(dup["score"] + 1.2, 2)
            if dup["source"] != it["source"] and it["source"] not in dup.setdefault("alsoIn", []):
                dup["alsoIn"].append(it["source"])
            seen_urls.add(it["url"])
            continue
        seen_urls.add(it["url"])
        bucket.append(it)

    return [i for b in buckets.values() for i in b]


LEVEL_ORDER = {"P0": 0, "P1": 1, "P2": 2}


def finalize(items: list[dict], now: datetime) -> list[dict]:
    """去重之后统一打重要性等级（交叉核验数此时才确定）"""
    for it in items:
        it.setdefault("verifiedCount", 1)
        # 榜单类不做重要性分级，避免挤压 P0/P1 的统计口径
        if it["domain"] in SPECIAL_DOMAINS:
            it["level"] = "P2"
            continue
        try:
            pub = datetime.fromisoformat(it["time"])
        except Exception:
            pub = None
        it["level"] = grade_importance(it["title"], it["score"], it["verifiedCount"], pub, now)

    # 领域内相对分级：每领域得分最高的 1-2 条提为 P1，避免小领域全挤在 P2
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it["domain"], []).append(it)
    for gid, group in groups.items():
        if gid in SPECIAL_DOMAINS or len(group) < 3:
            continue
        group.sort(key=lambda x: -x["score"])
        top_n = 1 if len(group) < 6 else 2
        for it in group[:top_n]:
            if it["level"] == "P2":
                it["level"] = "P1"
    return items


def _sort_key(it: dict):
    """领域内排序：级别优先（P0 → P2），同级按发布时间倒序（最新在前）。

    注：不再用印证数做二级键。同一级别下若按印证数排，时间会来回跳跃，
    读起来像被打乱；改成纯时间倒序后，每个级别内部都是一条清晰的时间线。
    """
    try:
        ts = -datetime.fromisoformat(it["time"]).timestamp()
    except Exception:
        ts = 0.0
    return (LEVEL_ORDER.get(it.get("level", "P2"), 2), ts)


def _rank_key(it: dict):
    """榜单类排序：严格按平台实时排名，不参与级别/时间排序。"""
    try:
        return (int(it.get("rank") or 999), -float(it.get("heat") or 0))
    except Exception:
        return (999, 0.0)


def build_day(items: list[dict], date_str: str) -> dict:
    sections = []
    for did, dname, ddesc in DOMAINS:
        pool = [i for i in items if i["domain"] == did]
        # 榜单类按平台实时排名，其余按 级别 → 时间
        pool.sort(key=_rank_key if did in SPECIAL_DOMAINS else _sort_key)
        chosen = pool[: MAX_BY_DOMAIN.get(did, MAX_PER_DOMAIN)]
        sections.append({
            "id": did,
            "name": dname,
            "desc": ddesc,
            "items": [{
                "title": i["title"],
                "url": i["url"],
                "summary": i["summary"],
                "source": i["source"],
                "time": i["time"],
                "fetchedAt": i["fetchedAt"],
                "level": i["level"],
                "region": i["region"],
                "keywords": i["keywords"],
                "verifiedCount": i["verifiedCount"],
                "alsoIn": i.get("alsoIn", []),
                "score": i["score"],
                # 榜单类字段（仅抖音热搜有值）
                **({"rank": i["rank"], "heat": i["heat"],
                    "heatText": i["heatText"], "heatLabel": i["heatLabel"]}
                   if i.get("rank") else {}),
            } for i in chosen],
            "empty": len(chosen) == 0,
        })
    return sections


def merge_previous_items(sections: list[dict], day_path: Path) -> int:
    """当天全量留档：把当天已归档、本次未抓到的条目全部补回来。

    与旧行为（名额优先给新条目、旧条目只填剩余空位）不同，这里按 URL 求并集，
    上限用 ARCHIVE_CAP_BY_DOMAIN，远高于单次抓取上限，
    因此一天内多批次抓到的条目都会留在当天文件里，不会被新批次挤掉。
    """
    total_now = sum(len(s["items"]) for s in sections)
    if not day_path.exists():
        return total_now
    try:
        old_txt = day_path.read_text(encoding="utf-8")
        m = re.search(r"window\.__DAY__\s*=\s*(\{.*\});?\s*$", old_txt, re.S)
        if not m:
            return total_now
        old = json.loads(m.group(1))
        old_map = {s["id"]: s.get("items", []) for s in old.get("sections", [])}
    except Exception as exc:  # noqa: BLE001
        print(f"  ! 读取旧归档失败（忽略，按本次结果写入）: {exc}")
        return total_now

    added = 0
    for s in sections:
        # 榜单类（抖音热搜）每次只用当次抓取的实时前 20，不补历史、不累积。
        # 榜单的价值在「此刻的热度排名」，旧排名留档反而会污染当日榜单。
        if s["id"] in SPECIAL_DOMAINS:
            s["items"] = s["items"][: MAX_BY_DOMAIN.get(s["id"], 20)]
            s["items"].sort(key=_rank_key)
            s["empty"] = len(s["items"]) == 0
            continue

        cap = ARCHIVE_CAP_BY_DOMAIN.get(s["id"], 20)
        seen = {i["url"] for i in s["items"]}
        for prev in old_map.get(s["id"], []):
            if len(s["items"]) >= cap:
                print(f"  ! {s['id']} 已达留档上限 {cap} 条，其余旧条目未保留")
                break
            if prev["url"] in seen:
                continue
            s["items"].append(prev)
            seen.add(prev["url"])
            added += 1
        # 与单次构建保持同一排序口径：级别 → 时间倒序
        s["items"].sort(key=_sort_key)
        s["empty"] = len(s["items"]) == 0

    total = sum(len(s["items"]) for s in sections)
    print(f"  当天留档：本次抓到 {total - added} 条，补回当天已归档 {added} 条，合计 {total} 条")
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge", action="store_true", help="增量合并当天已有数据")
    ap.add_argument("--days", type=int, default=1, help="回补天数（暂按当天归档）")
    args = ap.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")

    print(f"[{now:%H:%M:%S}] 开始抓取 {TOTAL_SOURCES} 个源（RSS {len(SOURCES)} + HTML {len(HTML_SOURCES)}）...")
    raw_items, errors = collect(now)
    print(f"  抓到原始条目 {len(raw_items)} 条，失败源 {len(errors)} 个")

    # 抖音热搜：JSON 榜单接口，不走 RSS 解析与关键词分类
    try:
        dy = fetch_douyin_hot(now)
        raw_items.extend(dy)
        print(f"  抖音热搜 {len(dy)} 条（榜首：{dy[0]['title'][:20] if dy else '—'}）")
    except Exception as exc:  # noqa: BLE001
        errors.append({"source": "抖音热搜", "url": DOUYIN_API,
                       "error": f"{type(exc).__name__}: {exc}"})
        print(f"    ✗ 抖音热搜抓取失败：{exc}")
    for e in errors:
        print(f"    ✗ {e['source']}: {e['error'][:70]}")

    if not raw_items:
        print("!! 没有任何条目，终止写入（避免覆盖已有数据）")
        return 1

    merged = finalize(dedupe(raw_items), now)
    n_p0 = sum(1 for i in merged if i["level"] == "P0")
    n_p1 = sum(1 for i in merged if i["level"] == "P1")
    print(f"  去重后 {len(merged)} 条（P0 {n_p0} / P1 {n_p1} / P2 {len(merged) - n_p0 - n_p1}）")

    # HTML 源列表页没有摘要，按重要性挑一批抓详情页补上
    missing = [i for i in merged
               if i["domain"] not in SPECIAL_DOMAINS
               and (not i["summary"] or i["summary"].endswith(("…", "...")))]
    missing.sort(key=lambda x: (LEVEL_ORDER.get(x["level"], 2), -x["score"]))
    todo = missing[:80]
    if todo:
        print(f"  补抓摘要 {len(todo)} 条（共 {len(missing)} 条缺摘要）...")
        filled = 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            fut_map = {pool.submit(fetch_detail_summary, i["url"]): i for i in todo}
            for fut in as_completed(fut_map):
                try:
                    s = fut.result()
                except Exception:
                    continue
                if s and len(s) > len(fut_map[fut]["summary"]):
                    fut_map[fut]["summary"] = s
                    filled += 1
        print(f"    补齐 {filled} 条，仍有 {len([i for i in merged if not i['summary']])} 条无摘要")

    sections = build_day(merged, today)
    total = sum(len(s["items"]) for s in sections)
    empty_domains = [s["id"] for s in sections if s["empty"]]
    print(f"  归档领域 {len(sections)} 个，有效条目 {total} 条，空白领域 {len(empty_domains)} 个")

    # 突发快讯：最近 4 小时内的 P0/P1 条目
    breaking = []
    for it in merged:
        if it["domain"] in SPECIAL_DOMAINS:
            continue
        try:
            pub = datetime.fromisoformat(it["time"])
        except Exception:
            continue
        if (now - pub).total_seconds() <= 4 * 3600 and it["level"] in ("P0", "P1"):
            breaking.append(it)
    breaking.sort(key=_sort_key)
    breaking = [{
        "title": i["title"],
        "url": i["url"],
        "source": i["source"],
        "time": i["time"],
        "domain": i["domain"],
        "level": i["level"],
    } for i in breaking[:6]]

    # 当天全量留档：当天已归档的条目全部保留，不再按单次名额挤掉旧条目
    if args.merge:
        day_path = DATA_DIR / f"day-{today}.js"
        total = merge_previous_items(sections, day_path)
    else:
        total = sum(len(s["items"]) for s in sections)

    level_count = {"P0": 0, "P1": 0, "P2": 0}
    for s in sections:
        for it in s["items"]:
            lv = it.get("level", "P2")
            level_count[lv] = level_count.get(lv, 0) + 1
    empty_domains = [s["id"] for s in sections if not s["items"]]

    payload = {
        "date": today,
        "updatedAt": now.isoformat(timespec="seconds"),
        "timezone": "Asia/Shanghai",
        "totalCount": total,
        "sourceCount": TOTAL_SOURCES - len(errors),
        "levelCount": level_count,
        "breaking": breaking,
        "sections": sections,
        "errors": errors,
    }

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    day_path.write_text(f"window.__DAY__ = {body};\n", encoding="utf-8")
    print(f"  ✓ 写入 {day_path.name}（{len(body.encode('utf-8')) / 1024:.1f} KB）")

    # 首页轻量包：每个领域只带首条，首屏只加载这个
    home = {
        "date": today,
        "updatedAt": payload["updatedAt"],
        "totalCount": total,
        "sourceCount": payload["sourceCount"],
        "levelCount": level_count,
        "breaking": breaking,
        "briefs": [{
            "domain": s["id"],
            "name": s["name"],
            "desc": s["desc"],
            "count": len(s["items"]),
            "item": s["items"][0] if s["items"] else None,
        } for s in sections],
    }
    home_body = json.dumps(home, ensure_ascii=False, separators=(",", ":"))
    (DATA_DIR / f"day-{today}.home.js").write_text(
        f"window.__HOME__ = {home_body};\n", encoding="utf-8"
    )
    print(f"  ✓ 写入 day-{today}.home.js（{len(home_body.encode('utf-8')) / 1024:.1f} KB）")

    # 更新归档索引
    index_path = DATA_DIR / "index.js"
    index: list[dict] = []
    if index_path.exists():
        try:
            txt = index_path.read_text(encoding="utf-8")
            m = re.search(r"window\.__INDEX__\s*=\s*(\[.*\]);?\s*$", txt, re.S)
            if m:
                index = json.loads(m.group(1))
        except Exception:
            index = []
    entry = {
        "date": today,
        "count": total,
        "updatedAt": payload["updatedAt"],
        "levelCount": level_count,
        "emptyDomains": empty_domains,
    }
    index = [e for e in index if e.get("date") != today] + [entry]
    index.sort(key=lambda x: x["date"], reverse=True)
    index_path.write_text(
        "window.__INDEX__ = " + json.dumps(index, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    print(f"  ✓ 写入 index.js（共 {len(index)} 天归档）")

    print("\n各领域条目数:")
    for s in sections:
        mark = "—" if s["empty"] else str(len(s["items"]))
        print(f"  {s['name']:<8} {mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
