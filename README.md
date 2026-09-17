# 思旋 A股量化站 v5.1（发布用仓库）

**站点地址：https://sevilia980529-spec.github.io/sixuan-quant/**

## 这个仓库是干什么的

它只负责**发布**，不存放源代码。仓库里只有：

| 文件 | 作用 |
| --- | --- |
| `.github/workflows/pages.yml` | 每个工作日北京时间 17:00 自动重建站点并发布到 GitHub Pages |
| `README.md` | 本说明 |

## 代码和数据在哪

源码、行情数据、回测历史、持仓的**唯一真相源**都在 CNB：
<https://cnb.cool/sixuan.upa/sixuan-quant>

工作流每天做三件事：

1. 从 CNB 拉最新代码与数据（CNB 是公开仓库，可匿名 clone）
2. 跑一次收盘更新 `daily_update.py`，再 `build_site.py` 重建站点
   （非交易日会自动跳过数据更新，站点照常重建）
3. 把 `docs/` 发布到 GitHub Pages

## 持仓数据

持仓来自项目云盘 `持仓_latest.json`，由 WorkBuddy 每日同步进 CNB 的
`data/holdings.json`，再由上面的工作流带进站点。
如果云盘当天没更新，页面顶部会显示「云盘持仓可能未更新」横幅，不会拿旧数据冒充新数据。

## 想手动重跑一次

去 Actions 页面 → 「每日重建站点并发布 Pages」→ Run workflow。
