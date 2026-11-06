# 每日行业新闻智能看板 · 部署手册

18 个领域 · 每天 07:30 更新 · 纯静态 · 零后端 · 零依赖

---

## 一、访问方式

| 方式    | 地址                                                                           | 说明                         |
| ----- | ---------------------------------------------------------------------------- | -------------------------- |
| 已部署线上 | `https://daily-industry-news-62462.app.workbuddy.host/?sharecode=3TNSQNA7VK` | 立即可用，三端自适应，链接可直接转发         |
| 本地预览  | `python -m http.server 8080 --directory dist`                                | 打开 `http://localhost:8080` |
| 自建托管  | GitHub Pages / Netlify / Cloudflare Pages                                    | 见第三节，可获得无 sharecode 的公开域名  |

> 分享链接的静态资源同样需要 `sharecode` 参数，页面已自动把该参数透传到 `data/*.js` 请求上，因此正常访问不需要任何额外操作。  
> 若要一个**完全公开、无参数**的域名，请用第三节任一方案部署。



---

## 二、文件结构

### 仓库（源）

```
每日行业新闻智能看板助手/
├─ index.html               前端单文件：HTML + CSS + JS 全部内联（约 88 KB）
├─ fetch_news.py            抓取聚合脚本，纯标准库，无需 pip 安装
├─ build_site.py            构建脚本：生成可部署的 dist/
├─ data/                    数据归档（脚本产物，需提交到仓库）
│  ├─ index.js              归档索引，记录有哪些天、每天多少条
│  ├─ day-YYYY-MM-DD.js     当天全量数据（18 领域 + 突发快讯）
│  └─ day-YYYY-MM-DD.home.js 当天首屏数据（首页只取每领域首条）
├─ dist/                    构建产物 = 可直接部署的站点根目录
├─ .github/workflows/daily.yml  定时抓取 + 自动发布到 GitHub Pages
├─ netlify.toml             Netlify 配置（publish = "dist"）
└─ README-DEPLOY.md         本文件
```

### 构建产物 `dist/`（可直接拷到任何静态托管）

```
dist/
├─ index.html        前端页面
├─ data/*.js         数据（同上）
├─ 404.html          未找到页兜底
├─ robots.txt
├─ _headers          Cloudflare Pages / Netlify 的响应头规则
├─ netlify.toml      Netlify 配置（publish = "."，因为此目录就是站点根）
└─ .nojekyll         GitHub Pages 必需，禁用以 `_` 开头的文件被 Jekyll 忽略
```

---

## 三、三个平台的部署步骤

### 方案 A：GitHub Pages（推荐，自带每日自动更新）

**最快路径：全自动脚本**

```bash
bash deploy-github.sh
```

依次完成：校验 token → 创建仓库 → 推送代码 → 把 Pages 的 Source 设为 GitHub Actions → 触发首次构建 → 打印站点地址。
Token 权限：classic 勾选 `repo` + `workflow`。

**手动路径**

```bash
# 1. 建仓库并推送到 main 分支
git init -b main && git add . && git commit -m "init"
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

1. 仓库 **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**（不要选 Deploy from a branch）。
2. 仓库 **Settings → Actions → General → Workflow permissions** 选 **Read and write permissions**（定时任务要回写 `data/`）。
3. 手动触发一次 **Actions → 每日抓取并发布 → Run workflow**，约 2 分钟后得到  
   `https://<你的用户名>.github.io/<仓库名>/`

之后每天 UTC 23:30（北京时间 07:30）自动抓取 → 构建 → 发布，数据同时提交回仓库形成归档。

> 公开仓库的定时任务完全免费。私有仓库会消耗 Actions 分钟数（本流程每次约 1 分钟），
> 且 **私有仓库的 Pages 需要付费账户**，建议用公开仓库。

### 方案 B：Netlify

```bash
npm i -g netlify-cli
netlify login
netlify deploy --prod --dir=dist
```

或用 Git 集成：New site from Git → 选仓库 → **Build command 留空**、**Publish directory 填 `dist`**。  
根目录的 `netlify.toml` 已写好这两项，通常无需手动填。

### 方案 C：Cloudflare Pages

Git 集成 → 选仓库 → Framework preset 选 **None** → **Build command 留空** → **Build output directory 填 `dist`** → Save and Deploy。  
得到 `https://<项目名>.pages.dev`。`dist/_headers` 会被自动识别，用于设置缓存与安全响应头。

> 三个平台都不需要任何 API Key、数据库或服务端。数据来自公开 RSS / 列表页，全量静态化。

---

## 四、日常维护

```bash
python fetch_news.py     # 抓取当天数据，写入 data/
python build_site.py     # 重建 dist/
```

- 增量补抓（当天多次更新，保留已有条目）：`python fetch_news.py --merge`
- 重新部署到分享链接：把 `dist/` 重新发布一次即可（URL 不变）
- 数据保留天数：改 `build_site.py` 里的 `KEEP_DAYS`（当前 0 = 全保留）

Windows 计划任务已在用（`行业新闻看板-每日0730` / `行业新闻看板-增量`），  
解释器路径：`C:\Users\56561\.workbuddy\binaries\python\versions\3.13.12\python.exe`

---

## 五、数据格式

数据文件是 **JS 赋值语句**而不是纯 JSON，这是刻意设计：

- 直接双击 `index.html`（`file://`）也能加载，不受 `fetch()` 的 CORS 限制；
- 免去一次网络往返，首屏更快。

内容本身就是标准 JSON，只要去掉 `window.__DAY__ = ` 前缀和结尾的 `;` 即可解析。

### `data/index.js`

```js
window.__INDEX__ = [
  {
    "date": "2026-11-06",
    "count": 142,
    "updatedAt": "2026-11-06T10:02:37+08:00",
    "levelCount": { "P0": 2, "P1": 33, "P2": 107 },
    "emptyDomains": []
  }
];
```

### `data/day-YYYY-MM-DD.js`

```js
window.__DAY__ = {
  "date": "2026-11-06",
  "updatedAt": "2026-11-06T10:02:37+08:00",
  "timezone": "Asia/Shanghai",
  "totalCount": 142,
  "sourceCount": 30,
  "levelCount": { "P0": 2, "P1": 33, "P2": 107 },
  "breaking": [
    {
      "title": "芯片股遭“血洗”：美股经历过山车式暴跌，纳指 6 分钟抹平 2026 中期选举后涨幅",
      "url": "https://www.ithome.com/0/xxx.htm",
      "source": "IT之家",
      "time": "2026-11-06T09:15:00+08:00",
      "domain": "tech",
      "level": "P0"
    }
  ],
  "sections": [
    {
      "id": "tech",
      "name": "科技与AI",
      "desc": "大模型、芯片、互联网与前沿研究",
      "items": [
        {
          "title": "芯片股遭“血洗”：美股经历过山车式暴跌，纳指 6 分钟抹平 2026 中期选举选后涨幅",
          "url": "https://www.ithome.com/0/xxx.htm",
          "summary": "该来源未提供摘要时，前端显示占位文案",
          "source": "IT之家",
          "time": "2026-11-06T09:15:18+08:00",
          "fetchedAt": "2026-11-06T10:02:20+08:00",
          "level": "P0",
          "region": "美洲",
          "keywords": ["半导体", "美股"],
          "verifiedCount": 1,
          "alsoIn": [],
          "score": 8.6
        }
      ]
    }
  ]
};
```

抖音热搜条目额外带 `rank` / `heat` / `heatText` / `heatLabel`：

```js
{ "title": "BLG战胜DK晋级半决赛", "rank": 1, "heat": 9823100,
  "heatText": "982.3万", "heatLabel": "热", "level": "P2", "domain": "douyin" }
```

### 字段含义

| 字段              | 说明                          |
| --------------- | --------------------------- |
| `level`         | `P0` 紧急 / `P1` 重要 / `P2` 常规 |
| `region`        | 地域：国内 / 亚洲 / 欧洲 / 美洲 / 国际   |
| `verifiedCount` | 同一事件被几家信源同时报道               |
| `alsoIn`        | 其他报道该事件的来源名                 |
| `score`         | 排序权重，由信源权重、时效、交叉核验共同决定      |
| `fetchedAt`     | 抓取时刻，用于展示"数据新鲜度"            |

---

## 六、前端关键实现

**坐标方式**：全部状态写进 URL（`history.replaceState`），复制链接即可还原视图。

| 参数      | 含义             | 示例              |
| ------- | -------------- | --------------- |
| `view`  | 首页 / 资讯流       | `?view=feed`    |
| `d`     | 归档日期           | `&d=2026-11-05` |
| `q`     | 搜索词            | `&q=芯片`         |
| `sel`   | 单选领域筛选         | `&sel=tech`     |
| `cols`  | 卡片列数 1/2/3     | `&cols=3`       |
| `fs`    | 字号 sm/md/lg/xl | `&fs=lg`        |
| `theme` | light/dark     | `&theme=dark`   |

**响应式**：三档断点 1024 / 720 / 430 px；字号用 `rem`，根字号由 `--fs` 变量驱动，全站随之缩放。

**零图片请求**：所有插画由 `artSvg()` / `artDeco()` 在浏览器内用几何图形绘制（18 个领域各一套母题），不加载任何图片文件。

**无障碍**：跳转链接、`aria-pressed`、2px 焦点环、`prefers-reduced-motion` 降级动画。

---

## 七、常见问题

**页面空白 / 显示"数据文件加载失败"**  
`data/day-<日期>.js` 没被部署。检查 `dist/data/` 是否存在该文件，以及服务器是否正确返回 `200`（不是 403/404）。

**分享链接里某些资源 403**  
托管方要求子资源也带 `sharecode`。前端已用 `window.__ASSET_QS__` 自动透传，请勿删除 `loadScript()` 中的拼接逻辑。

**中文乱码**  
确认服务器对 `.js` 返回 `charset=utf-8`。三个平台的默认配置都正确；自建 Nginx 需加 `charset utf-8;`。

**某天没有数据**  
`fetch_news.py` 抓取失败时不会写文件，页面按 `data/index.js` 的日期列表回退到最近一天。可手动 `python fetch_news.py --merge` 补抓。
