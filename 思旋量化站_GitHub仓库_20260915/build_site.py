#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 思旋_全市场选股扫描器.py --v51 产出的 v51_pool.json 内联进模板，
生成**自包含单文件 HTML**（file:// 双击就能开，不依赖任何外部 json/css/js）。

用法：
    python3 思旋_全市场选股扫描器.py --v51 --out data/v51_pool.json
    python3 build_site.py
"""
import json, os, sys, io

BASE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(BASE, '思旋_量化站_模板.html')
SRC = os.path.join(BASE, 'data', 'v51_pool.json')
HIST_SRC = os.path.join(BASE, 'data', 'v51_history.json')
DST = os.path.join(BASE, '思旋_量化站_v51.html')

if not os.path.exists(SRC):
    sys.exit('找不到 %s —— 先跑：python3 思旋_全市场选股扫描器.py --v51 --out data/v51_pool.json' % SRC)

data = json.load(open(SRC, encoding='utf-8'))

# 精简内联体积：只保留页面真正用到的字段
slim = {
    'generated_at': data['generated_at'],
    'last_trade_day': data['last_trade_day'],
    'pool_count': data['pool_count'],
    'index': data['index'],
    'calendar': data['calendar'],
    'pool': data['pool'][:12],          # 页面只显示前 12（五星仍是前 3）
}
payload = json.dumps(slim, ensure_ascii=False, separators=(',', ':'))

html = io.open(TPL, encoding='utf-8').read()
if '/*__DATA__*/null' not in html:
    sys.exit('模板里找不到 /*__DATA__*/ 占位符')
html = html.replace('/*__DATA__*/null', payload)

# 内联 v5.1 历史回算（过去每一天的榜单 + 每批真实战绩）—— 日历不再依赖「当天有没有打开」。
# 只留页面要用到的字段，控制体量。
if os.path.exists(HIST_SRC):
    hraw = json.load(open(HIST_SRC, encoding='utf-8'))
    boards = {}
    for d, b in hraw.get('boards', {}).items():
        t = []
        for x in (b.get('top') or []):
            it = {'name': x['name'], 'code': x['code'], 'ret60': x['ret60']}
            if x.get('buy'):
                it['buy'] = x['buy']
            t.append(it)
        if t:
            boards[d] = {'sig': 1 if b.get('sig') else 0, 'top': t}
    bts = []
    for b in hraw.get('batches', []):
        bts.append({'no': b['no'], 'sig': b['sig'], 'buy': b['buy'], 'due': b['due'],
                    'ret': b['ret'],
                    'picks': [{'name': p['name'], 'code': p['code'], 'ret': p['ret'],
                               'reason': p.get('reason', '')} for p in (b.get('picks') or [])]})
    slim_hist = dict(window=hraw.get('window'), universe=hraw.get('universe'),
                     stats=hraw.get('stats'), batches=bts, boards=boards)
    hpayload = json.dumps(slim_hist, ensure_ascii=False, separators=(',', ':'))
    if '/*__HIST__*/null' not in html:
        sys.exit('模板里找不到 /*__HIST__*/ 占位符')
    html = html.replace('/*__HIST__*/null', hpayload)
    print('✅ 内联历史回算：%d 天榜单 · %d 批战绩（%d 字节）' % (len(boards), len(bts), len(hpayload)))
else:
    html = html.replace('/*__HIST__*/null', 'null')
    print('⚠ 没有 %s —— 跳过历史回算（先跑 history_v51.py）' % HIST_SRC)

# 内联股票池（沪深主板全部代码），供页面在浏览器内自己跑全市场扫描
UNIV = os.path.join(BASE, 'data', 'universe.txt')
if "/*__UNIV__*/''" in html:
    if not os.path.exists(UNIV):
        sys.exit('找不到 %s —— 先跑：python3 gen_universe.py' % UNIV)
    univ = io.open(UNIV, encoding='utf-8').read().strip()
    html = html.replace("/*__UNIV__*/''", "'%s'" % univ)

io.open(DST, 'w', encoding='utf-8').write(html)

# 同时输出 Pages 站点（docs/）与联网数据 data.json
OUT_DIR = os.path.join(BASE, 'docs')
os.makedirs(OUT_DIR, exist_ok=True)
io.open(os.path.join(OUT_DIR, 'index.html'), 'w', encoding='utf-8').write(html)
DATA_JSON = os.path.join(OUT_DIR, 'data.json')
io.open(DATA_JSON, 'w', encoding='utf-8').write(payload)
print('✅ 已生成 Pages 站点：docs/index.html')
print('✅ 已生成联网数据：docs/data.json（%d 字节）' % len(payload))
print('✅ 已生成自包含单文件：%s' % DST)
print('   %d 字节 ｜ 榜单 %d 只 ｜ 数据截止 %s ｜ 交易日历 %d 天'
      % (os.path.getsize(DST), len(slim['pool']), slim['last_trade_day'], len(slim['calendar'])))
