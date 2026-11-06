#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建可部署的静态站点产物 -> dist/

用法:
    python build_site.py

产物内容:
    dist/index.html            前端单文件（HTML+CSS+JS 内联）
    dist/data/index.js         归档索引
    dist/data/day-*.js         每日全量数据
    dist/data/day-*.home.js    每日首屏数据
    dist/404.html              找不到页面时的兜底
    dist/netlify.toml          Netlify 配置
    dist/_headers              Cloudflare Pages / Netlify 响应头
    dist/robots.txt
    dist/.nojekyll             GitHub Pages 必需（禁止 Jekyll 处理）

只复制运行期必需文件，不包含 fetch_news.py、design-refs/、.workbuddy/。
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
DATA_SRC = os.path.join(ROOT, "data")

# 只保留最近 N 天，避免仓库无限膨胀；设为 0 表示全部保留
KEEP_DAYS = 0


def log(msg):
    print("  " + msg)


def main():
    if not os.path.isfile(os.path.join(ROOT, "index.html")):
        sys.exit("找不到 index.html，请在项目根目录运行本脚本")

    if os.path.isdir(DIST):
        shutil.rmtree(DIST)
    os.makedirs(os.path.join(DIST, "data"), exist_ok=True)

    # 1. 前端
    shutil.copy2(os.path.join(ROOT, "index.html"), os.path.join(DIST, "index.html"))
    log("index.html")

    # 2. 数据文件（只收 .js，且必须是 day-*.js / day-*.home.js / index.js）
    if not os.path.isdir(DATA_SRC):
        sys.exit("找不到 data/ 目录，请先运行 fetch_news.py 抓取数据")

    files = [f for f in os.listdir(DATA_SRC) if f.endswith(".js")]
    days = sorted({f[4:14] for f in files if f.startswith("day-")})

    if KEEP_DAYS and len(days) > KEEP_DAYS:
        keep = set(days[-KEEP_DAYS:])
        files = [f for f in files
                 if f == "index.js" or (f.startswith("day-") and f[4:14] in keep)]
        log("仅保留最近 %d 天: %s ~ %s" % (KEEP_DAYS, days[-KEEP_DAYS], days[-1]))

    total = 0
    for name in files:
        src = os.path.join(DATA_SRC, name)
        shutil.copy2(src, os.path.join(DIST, "data", name))
        total += os.path.getsize(src)

    log("data/ 共 %d 个文件，%.1f KB，覆盖 %d 天" % (len(files), total / 1024, len(days)))
    if days:
        log("日期范围: %s ~ %s" % (days[0], days[-1]))

    # 3. 静态站配套文件
    assets = {
        "404.html": """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>页面不存在 · 每日行业新闻智能看板</title>
<style>
  html,body{margin:0;height:100%}
  body{display:grid;place-items:center;background:#FDF6EE;color:#221046;
       font:16px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;padding:24px}
  .box{max-width:460px;text-align:center}
  .n{font-size:64px;font-weight:800;letter-spacing:-.03em;
     background:linear-gradient(120deg,#E8386F,#FF7A4D);-webkit-background-clip:text;background-clip:text;color:transparent}
  p{color:#6E5F7D;margin:10px 0 26px}
  a{display:inline-block;padding:12px 26px;border-radius:999px;color:#fff;text-decoration:none;
    background:linear-gradient(120deg,#E8386F,#FF7A4D);font-weight:600}
  @media (prefers-color-scheme:dark){
    body{background:#151021;color:#F3ECFA} p{color:#B6A8C8}
  }
</style></head>
<body><div class="box">
  <div class="n">404</div>
  <p>这个地址没有对应的页面。<br>可能是链接过期，或该日期的数据尚未生成。</p>
  <a href="/">返回今日简报</a>
</div></body></html>
""",
        "robots.txt": """User-agent: *
Allow: /
""",
        ".nojekyll": "",
        "_headers": """/*
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin

/data/*
  Cache-Control: public, max-age=300, must-revalidate
""",
        "netlify.toml": """# Netlify 部署配置
[build]
  publish = "."
  command = ""

[[headers]]
  for = "/*"
  [headers.values]
    X-Content-Type-Options = "nosniff"
    Referrer-Policy = "strict-origin-when-cross-origin"

[[headers]]
  for = "/data/*"
  [headers.values]
    Cache-Control = "public, max-age=300, must-revalidate"
""",
    }
    for name, content in assets.items():
        with open(os.path.join(DIST, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    log("404.html / robots.txt / _headers / netlify.toml / .nojekyll")

    # 4. 汇总
    size = sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(DIST) for f in fs)
    print("\n构建完成 -> %s  (%.1f KB)" % (DIST, size / 1024))
    print("本地预览: python -m http.server 8080 --directory dist")


if __name__ == "__main__":
    main()
