#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 / 增量更新 docs/archive.json —— 日历存档的云端副本。

GitHub Actions 每个交易日收盘后跑一次：
  1. 把「今天」的榜单追加进去
  2. 顺手把 history_v51.py 回算出的历史榜单也放进去（换设备 / 清缓存也能全量恢复）

页面启动时会拉这个文件并合并进本机日历（只补空缺，不覆盖真实记录），
所以「你没打开网页的那些天」也会被自动记下来。

用法：python3 archive_day.py
"""
import json, os, io, time

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'docs', 'data.json')
HIST = os.path.join(BASE, 'data', 'v51_history.json')
OUT = os.path.join(BASE, 'docs', 'archive.json')


def r2(x):
    return round(x, 2)


def main():
    days = {}
    if os.path.exists(OUT):
        try:
            days = json.load(io.open(OUT, encoding='utf-8')).get('days', {}) or {}
        except Exception:
            days = {}

    # ① 历史回算（过去的每一天）
    n_hist = 0
    if os.path.exists(HIST):
        h = json.load(io.open(HIST, encoding='utf-8'))
        sigs = set(b['sig'] for b in h.get('batches', []))
        for d, b in (h.get('boards') or {}).items():
            top = []
            for x in (b.get('top') or []):
                it = {'name': x['name'], 'code': x['code'], 'ret60': x['ret60']}
                if x.get('buy'):
                    it['buy'] = x['buy']
                    it['stop'] = r2(x['buy'] * 0.85)
                top.append(it)
            if not top:
                continue
            e = {'sig': 1 if (b.get('sig') or d in sigs) else 0, 'top': top, 'hist': 1}
            if d not in days:
                n_hist += 1
            days[d] = e

    # ② 今天（来自当次扫描的 data.json）
    n_now = 0
    if os.path.exists(DATA):
        d = json.load(io.open(DATA, encoding='utf-8'))
        day = d.get('last_trade_day')
        pool = d.get('pool') or []
        if day and pool:
            top = []
            for x in pool:
                px = float(x.get('p') or 0)
                if not px:
                    continue
                it = {'name': x['name'], 'code': x['mkt'] + x['code'], 'ret60': x.get('ret60'),
                      'buy': r2(px * 0.995), 'stop': r2(px * 0.995 * 0.85)}
                top.append(it)
            e = days.get(day) or {}
            e['sig'] = e.get('sig', 0)
            e['top'] = top
            e.pop('hist', None)                 # 当天的记录是真的，不再是回算标记
            e['real'] = 1
            if day not in days:
                n_now += 1
            days[day] = e

    out = {'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'days': days}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, io.open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print('✅ %s · 共 %d 天（历史补齐 +%d，今日 +%d）' % (OUT, len(days), n_hist, n_now))


if __name__ == '__main__':
    main()
