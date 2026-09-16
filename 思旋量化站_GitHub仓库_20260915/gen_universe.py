#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重新生成 data/universe.txt（沪深主板全部代码）。新股上市变多后跑一次即可。"""
import requests, time, concurrent.futures as cf, io, os
UA='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1'
H={'User-Agent':UA,'Referer':'https://gu.qq.com/'}
S=requests.Session(); S.headers.update(H)
codes=[]
for p in ('600','601','603','605'): codes+=['sh%s%03d'%(p,i) for i in range(1000)]
for p in ('000','001','002','003'): codes+=['sz%s%03d'%(p,i) for i in range(1000)]
B=80; batches=[codes[i:i+B] for i in range(0,len(codes),B)]
def go(b):
    for _ in range(2):
        try:
            return S.get('https://qt.gtimg.cn/q='+','.join(b),timeout=25).content.decode('gbk','ignore')
        except Exception: time.sleep(0.5)
    return ''
good=[]
with cf.ThreadPoolExecutor(max_workers=16) as ex:
    for txt in ex.map(go,batches):
        for seg in txt.split(';'):
            seg=seg.strip()
            if not seg.startswith('v_') or '=' not in seg: continue
            k=seg[2:seg.index('=')]
            if k.startswith('sh') or k.startswith('sz'): good.append(k)
good=sorted(set(good))
os.makedirs('data',exist_ok=True)
io.open('data/universe.txt','w',encoding='utf-8').write(''.join(c[2:] for c in good))
print('✅ data/universe.txt 已更新：%d 只'%len(good))
