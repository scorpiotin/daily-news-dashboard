#!/usr/bin/env bash
# =============================================================================
# 一键部署到 GitHub Pages
#
# 用法（在项目根目录）：
#   bash deploy-github.sh                    # 交互式，token 不回显
#   GH_TOKEN=ghp_xxx bash deploy-github.sh   # 或从环境变量读
#
# 需要的 token 权限：repo（含 public_repo）+ workflow
# 生成地址：https://github.com/settings/tokens?type=beta
# =============================================================================
set -euo pipefail

OWNER="${GH_OWNER:-}"
REPO="${GH_REPO:-daily-news-dashboard}"
BRANCH="main"

die() { echo "✗ $*" >&2; exit 1; }

command -v git >/dev/null || die "未找到 git"
command -v curl >/dev/null || die "未找到 curl"

# ---------- 1. 收集参数 ----------
if [ -z "$OWNER" ]; then
  read -r -p "GitHub 用户名: " OWNER
fi
[ -n "$OWNER" ] || die "用户名不能为空"

read -r -p "仓库名 [$REPO]: " _r
REPO="${_r:-$REPO}"
read -r -p "是否公开仓库（Pages 免费版需要公开）[Y/n]: " _pub
PUBLIC="true"; case "${_pub:-Y}" in [nN]*) PUBLIC="false";; esac

if [ -z "${GH_TOKEN:-}" ]; then
  read -r -s -p "GitHub Personal Access Token（输入不回显）: " GH_TOKEN
  echo
fi
[ -n "$GH_TOKEN" ] || die "token 不能为空"

API="https://api.github.com"
AUTH=(-H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
      -H "X-GitHub-Api-Version: 2022-11-28")

# ---------- 2. 校验 token ----------
echo "→ 校验 token..."
ME=$(curl -fsSL "${AUTH[@]}" "$API/user" | sed -n 's/.*"login":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
[ -n "$ME" ] || die "token 无效或已过期"
echo "  已登录: $ME"
if [ "$ME" != "$OWNER" ]; then
  echo "  ⚠ 用户名与你填写的不一致，将以 $ME 为准"
  OWNER="$ME"
fi

# ---------- 3. 创建仓库（已存在则跳过） ----------
echo "→ 检查仓库 $OWNER/$REPO ..."
CODE=$(curl -s -o /dev/null -w "%{http_code}" "${AUTH[@]}" "$API/repos/$OWNER/$REPO")
if [ "$CODE" = "200" ]; then
  echo "  仓库已存在，将推送覆盖"
else
  echo "  创建中..."
  BODY=$(printf '{"name":"%s","private":%s,"auto_init":false,"description":"18 个领域每日资讯聚合看板，每天 07:30 自动更新"}' "$REPO" \
        "$([ "$PUBLIC" = "true" ] && echo false || echo true)")
  RESP=$(curl -fsSL -X POST "${AUTH[@]}" -d "$BODY" "$API/user/repos") || die "创建仓库失败"
  echo "  已创建: https://github.com/$OWNER/$REPO"
fi

# ---------- 4. 推送代码 ----------
echo "→ 推送代码..."
git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/$OWNER/$REPO.git"
# token 只用于这一次推送，不写入 .git/config
git -c credential.helper= push "https://x-access-token:$GH_TOKEN@github.com/$OWNER/$REPO.git" "$BRANCH:$BRANCH" --force
git branch --set-upstream-to="origin/$BRANCH" "$BRANCH" 2>/dev/null || true

# ---------- 5. 启用 Pages（Source = GitHub Actions） ----------
echo "→ 启用 GitHub Pages ..."
PC=$(curl -s -o /dev/null -w "%{http_code}" "${AUTH[@]}" "$API/repos/$OWNER/$REPO/pages")
if [ "$PC" = "200" ]; then
  curl -fsSL -X PUT "${AUTH[@]}" -d '{"build_type":"workflow"}' \
    "$API/repos/$OWNER/$REPO/pages" >/dev/null && echo "  已切换为 GitHub Actions 发布"
else
  curl -fsSL -X POST "${AUTH[@]}" -d '{"build_type":"workflow"}' \
    "$API/repos/$OWNER/$REPO/pages" >/dev/null && echo "  已创建 Pages 站点"
fi

# ---------- 6. 等待并触发首次构建 ----------
echo "→ 触发首次构建..."
sleep 3
curl -fsSL -X POST "${AUTH[@]}" \
  -d "{\"ref\":\"$BRANCH\"}" \
  "$API/repos/$OWNER/$REPO/actions/workflows/daily.yml/dispatches" >/dev/null 2>&1 \
  && echo "  已触发" || echo "  push 已自动触发，无需重复"

# ---------- 7. 结果 ----------
cat <<EOF

============================================================
✅ 部署已发起

仓库    https://github.com/$OWNER/$REPO
Actions https://github.com/$OWNER/$REPO/actions
站点    https://$OWNER.github.io/$REPO/

首次构建约需 1–3 分钟。构建完成后站点即可访问。
之后每天 北京时间 07:30 自动抓取并发布。

若 Actions 报权限错误：
  仓库 Settings → Actions → General → Workflow permissions
  选择「Read and write permissions」后重新运行工作流。
============================================================
EOF
