#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
刷新 v51_pool.json —— 与「浏览器内扫描」完全同源同口径（防止两边对不上）：
  快照 qt.gtimg.cn/q=...          （GBK，字段位与页面一致）
  日K  ifzq.gtimg.cn/appstock/app/kline/kline?param=CODE,day,,,70,&_var=
  ret60 = close[-1]/close[-61]-1   （最后 61 根收盘，不是首根）
  amt20 = mean(close*vol) 最近 20 根，腾讯 vol 单位是手 → ×100 变股
用法：python3 refresh_pool.py
"""
import json, os, io, sys, time, threading, queue
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
UNIV = os.path.join(BASE, 'data', 'universe.txt')
OUT = os.path.join(BASE, 'data', 'v51_pool.json')

CFG = dict(priceLo=20, priceHi=40, amt20Min=5000, retWin=60, amtWin=20, pickN=3, topN=12)
UA = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
      'Referer': 'https://gu.qq.com/'}
SEC = [('银行','银行'),('证券','券商'),('保险','保险'),('地产','地产'),('医药','医药'),
 ('生物','医药'),('科技','科技'),('电子','电子'),('半导体','半导体'),('芯片','半导体'),
 ('锂','锂电'),('钨','有色'),('铜','有色'),('铝','有色'),('黄金','黄金'),('材料','材料'),
 ('化工','化工'),('化学','化工'),('汽车','汽车'),('机械','机械'),('装备','机械'),
 ('食品','食品'),('酒','酿酒'),('电力','电力'),('能源','能源'),('矿业','有色'),
 ('建设','基建'),('建材','建材'),('纺织','纺织'),('农业','农业'),('传媒','传媒'),
 ('通信','通信'),('软件','软件'),('数据','软件'),('重工','机械')]
def tag_sec(nm):
    for k, v in SEC:
        if k in nm:
            return v
    return '其他'

def get(url, timeout=25, gbk=True):
    req = urllib.request.Request(url, headers=UA)
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    return raw.decode('gbk', 'ignore') if gbk else raw.decode('utf-8', 'ignore')

def r2(x):
    return round(x, 2)

def calc_v51(kl):
    W, A = CFG['retWin'], CFG['amtWin']
    n = len(kl)
    if n < W + 1:
        return None
    cl = [float(x[2]) for x in kl]
    vo = [float(x[5]) * 100 for x in kl]          # 手 → 股
    s = 0.0
    for i in range(n - A, n):
        s += cl[i] * vo[i]
    return dict(ret60=r2((cl[-1] / cl[-61] - 1) * 100), amt20=(s / A) / 1e8, last_day=kl[-1][0])

def kline(key):
    url = 'https://ifzq.gtimg.cn/appstock/app/kline/kline?param=%s,day,,,70,&_var=k_%s' % (key, key)
    txt = get(url, gbk=False)
    j = json.loads(txt[txt.index('=') + 1:])
    d = j['data'][key]
    return d.get('day') or d.get('qfqday')

def main():
    raw = io.open(UNIV, encoding='utf-8').read().strip()   # 定长 6 位拼接，无分隔符
    codes = [raw[i:i + 6] for i in range(0, len(raw), 6)]
    print('全市场主板 %d 只' % len(codes))

    # ① 快照过滤：20~40 元 + 非 ST
    rows = []
    batches = [codes[i:i + 80] for i in range(0, len(codes), 80)]
    lock = threading.Lock()
    def snap(b):
        url = 'https://qt.gtimg.cn/q=' + ','.join((c[0] == '6' and 'sh' or 'sz') + c for c in b)
        try:
            txt = get(url)
        except Exception as e:
            print('  快照批次失败:', e)
            return
        out = []
        for line in txt.split(';'):
            line = line.strip()
            if not line.startswith('v_'):
                continue
            try:
                key = line[2:line.index('=')]
                f = line[line.index('"') + 1:line.rindex('"')].split('~')
            except Exception:
                continue
            if len(f) < 40:
                continue
            nm, px, amt = f[1], float(f[3]), float(f[37])
            if not nm or not (px > 0):
                continue
            if 'ST' in nm.upper() or '退' in nm:
                continue
            if not (CFG['priceLo'] <= px <= CFG['priceHi']):
                continue
            if amt <= 0:
                continue
            out.append(dict(code=key[2:], mkt=key[:2], name=nm, p=round(px, 2),
                            chg=float(f[32]) if f[32] else 0.0))
        with lock:
            rows.extend(out)
    ts = []
    for i in range(0, len(batches), 6):
        grp = batches[i:i + 6]
        ths = [threading.Thread(target=snap, args=(b,)) for b in grp]
        for t in ths: t.start()
        for t in ths: t.join()
        print('  快照 %d/%d · 命中 %d 只' % (min(i + 6, len(batches)), len(batches), len(rows)), flush=True)
    if not rows:
        sys.exit('快照没拉到，检查网络')

    # ② 日K：算 ret60 / amt20
    res = []
    q = queue.Queue()
    for r in rows:
        q.put(r)
    def work():
        while True:
            try:
                r = q.get_nowait()
            except queue.Empty:
                return
            k = r['mkt'] + r['code']
            for attempt in (0, 1):
                try:
                    kl = kline(k)
                    x = calc_v51(kl)
                    if x and x['amt20'] * 1e4 >= CFG['amt20Min']:
                        r.update(ret60=x['ret60'], amt20=round(x['amt20'], 2), last_day=x['last_day'])
                        with lock:
                            res.append(r)
                    break
                except Exception:
                    time.sleep(0.4 * (attempt + 1))
            q.task_done()
    ths = [threading.Thread(target=work) for _ in range(8)]
    for t in ths: t.start()
    for t in ths: t.join()
    print('  日K 完成 · 合格 %d 只' % len(res), flush=True)
    if len(res) < 5:
        sys.exit('合格数太少，疑似被限流，未写入文件')

    res.sort(key=lambda x: x['ret60'])
    pool = []
    for i, x in enumerate(res[:CFG['topN']]):
        x['rank'] = i + 1
        x['five'] = i < CFG['pickN']
        x['sector'] = tag_sec(x['name'])
        pool.append(x)

    # ③ 上证指数 + 交易日历
    idx_px, idx_chg, cal = 0.0, 0.0, []
    try:
        kl = kline('sh000001')
        cal = [x[0] for x in kl]
        idx_px = float(kl[-1][2])
        idx_chg = r2((float(kl[-1][2]) / float(kl[-2][2]) - 1) * 100)
    except Exception as e:
        print('指数拉取失败：', e)
    day = max([x['last_day'] for x in pool]) if pool else (cal[-1] if cal else '')

    data = dict(generated_at=time.strftime('%H:%M:%S'), last_trade_day=day,
                index=dict(sh=idx_px, sh_chg=idx_chg), calendar=cal,
                pool_count=len(res), pool=pool)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(data, io.open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)
    print('✅ %s → %s 收盘 · 合格 %d 只 · 日历 %d 天' % (OUT, day, len(res), len(cal)))
    for x in pool[:5]:
        print('   %d. %s %s %s ret60=%.2f amt20=%.2f亿'
              % (x['rank'], x['name'], x['code'], x['p'], x['ret60'], x['amt20']))

if __name__ == '__main__':
    main()
