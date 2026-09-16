# 三步上手（一次性，之后全自动）

> 全程在浏览器里操作，大约 1 分钟。做完之后**每天 16:30（北京时间）GitHub Actions 会自动更新**，
> 周末和法定节假日自动跳过，不用你管。

## 第 1 步：新建仓库并上传

1. 打开 https://github.com/new
2. 仓库名填 `sixuan-quant`，选 **Public**，**不要**勾 README / .gitignore（保持空仓库）→ Create repository
3. 在仓库首页点 **uploading an existing file**（或 "Add file → Upload files"）
4. 把本压缩包解压后，**选中里面全部文件和文件夹**（含 `.github` 目录）拖进上传框
5. 底部填一句 `初始化 v5.1 站点` → Commit changes

> ⚠️ 上传后请确认仓库根目录里有 `.github/workflows/daily-update.yml`。
> 万一 `.github` 没跟着传上去（少数浏览器会跳过隐藏目录），补救办法在下面「附录 A」。

## 第 2 步：开 Pages

仓库 → **Settings** → 左侧 **Pages** → Build and deployment：

- Source 选 **Deploy from a branch**
- Branch 选 **main**，目录选 **/docs** → Save

等 1~2 分钟，站点地址固定为：

```
https://<你的用户名>.github.io/sixuan-quant/
```

## 第 3 步：启用 Actions + 手动跑第一次

仓库 → **Actions** 标签 → 如果是第一次会提示 "Enable Actions"，点 **I understand my workflows, enable them**
→ 左侧点工作流 **每日收盘后更新（v5.1）** → 右上角 **Run workflow** → 选 `main` 分支 → Run workflow

跑完（约 2~5 分钟）变绿，`docs/data.json` 和 `docs/archive.json` 就会被更新提交。

## 第 4 步：把站点和仓库连起来

用手机打开 Pages 地址 → 右边找 **⚙ 仓库**（数据源那一行）→ 填 `用户名/sixuan-quant`
（例：`sixuan/sixuan-quant`）→ 保存

这样页面每次打开都会从 GitHub 拉当天的最新榜单和日历存档 —— **你就算一整周不打开，日历也不会断**。

---

## 附录 A：`.github` 没传上去时的补救

在仓库首页点 "Add file → Create new file"，文件名框里**完整输入**：

```
.github/workflows/daily-update.yml
```

内容粘贴下面这段：

```yaml
name: 每日收盘后更新（v5.1）

on:
  schedule:
    # UTC 08:30 = 北京时间 16:30（A 股收盘后，数据已落定）；周一至周五
    - cron: '30 8 * * 1-5'
  workflow_dispatch:

permissions:
  contents: write

jobs:
  update:
    runs-on: ubuntu-latest
    timeout-minutes: 50

    steps:
      - name: 检出代码
        uses: actions/checkout@v4

      - name: 装 Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: 交易日自检 + 刷新榜单 + 重算历史 + 重建站点 + 更新日历存档
        env:
          TZ: Asia/Shanghai
        run: python3 daily_update.py

      - name: 提交更新
        env:
          TZ: Asia/Shanghai
        run: |
          git config user.name  "sixuan-quant-bot"
          git config user.email "bot@users.noreply.github.com"
          git add -f docs/data.json docs/index.html docs/archive.json
          if git diff --cached --quiet; then
            echo "没有变化（多半是非交易日），跳过提交"
          else
            DAY=$(python3 -c "import json;print(json.load(open('docs/data.json',encoding='utf-8'))['last_trade_day'])")
            git commit -m "更新 $DAY 收盘数据"
            git push
          fi
```

Commit 之后回到第 3 步。

---

## 附录 B：常见疑问

**Q：会不会自动修改我的持仓？**
不会。Actions 只动 `docs/` 下三个产物文件，不碰任何持仓数据 —— 持仓存在你自己手机浏览器里。

**Q：周末会跑吗？**
`daily_update.py` 第一步就做交易日自检：拉上证指数时间戳，不是今天就立即退出，**不写文件不提交**。所以周末和节假日 Actions 也是绿的，但没有 commit。

**Q：数据会陈旧吗？**
不会。① Actions 每天 16:30 更新云端 `data.json` / `archive.json`；② 你自己打开页面时，15:10 之后还会自动联网重算一遍全市场；③ 历史榜单已用日 K 反算写死进页面，119 天随时可查。

**Q：临时链接 `*.csb.app` 能长期用吗？**
能，但它是**静态快照**，不会自动更新。想要自动更新请用上面的 GitHub Pages 地址。
