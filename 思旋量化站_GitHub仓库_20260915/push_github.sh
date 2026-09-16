#!/usr/bin/env bash
# 把本目录推到 GitHub 建仓（需要先授权 GitHub）
# 用法1：先 gh auth login，然后 ./push_github.sh
# 用法2：GITHUB_TOKEN=ghp_xxx ./push_github.sh
set -e
cd "$(dirname "$0")"
REPO_NAME="${REPO_NAME:-sixuan-quant}"

git init -q 2>/dev/null || true
git add -A
git -c user.name=sixuan -c user.email=sixuan@local commit -q -m "初始化：思旋 v5.1 量化站（每日自动扫描）" 2>/dev/null || echo "（无新改动可提交）"
git branch -M main

if [ -n "$GITHUB_TOKEN" ]; then
  OWNER=$(curl -s -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user | python3 -c "import sys,json;print(json.load(sys.stdin)['login'])")
  echo "GitHub 账号：$OWNER"
  curl -s -H "Authorization: token $GITHUB_TOKEN" -d "{\"name\":\"$REPO_NAME\",\"public\":true}" https://api.github.com/user/repos >/dev/null
  git remote remove origin 2>/dev/null || true
  git remote add origin "https://x-access-token:$GITHUB_TOKEN@github.com/$OWNER/$REPO_NAME.git"
  git push -u origin main
  echo "✅ 仓库：https://github.com/$OWNER/$REPO_NAME"
else
  gh repo create "$REPO_NAME" --public --source=. --push
  echo "✅ 已创建并推送"
fi

echo ""
echo "下一步（只有第一次需要，30 秒）："
echo "  1) 打开仓库 → Settings → Pages"
echo "  2) Source 选 Deploy from a branch"
echo "  3) Branch 选 main / 目录选 /docs → Save"
echo "  4) Actions 页 → 左侧「每日选股扫描（v5.1）」→ Run workflow（手动跑第一次）"
