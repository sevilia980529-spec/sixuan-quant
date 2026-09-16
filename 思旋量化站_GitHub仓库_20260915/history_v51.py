#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v5.1 历史回算 —— 一次性把「过去每一天的推荐榜」和「每一批的真实战绩」算出来。

目的：日历不再依赖「当天有没有打开网页」—— 历史可以直接从日 K 反推出来。
口径与页面 / refresh_pool.py 完全一致（同源，防止两边打架）：
  快照 qt.gtimg.cn/q=...                       （GBK）
  日K  ifzq.gtimg.cn/appstock/app/kline/kline?param=CODE,day,,,180（不复权）
  ret60 = close[i]/close[i-60]-1                （最后 61 根收盘）
  amt20 = mean(close*vol) 最近 20 根，腾讯 vol 单位是手 → ×100 变股
  过滤   收盘价 20~40 元 + 20 日均额 ≥ 5000 万 （都按「当天」的值算，不用今天的）

信号节奏（非重叠采样）：信号日 S → 次日开盘挂 open×0.995 → 持有 12 个交易日到期 → 到期日即下一个信号日
输出：data/v51_history.json
"""
import json, os, io, sys, time, threading, queue
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
UNIV = os.path.join(BASE, 'data', 'universe.txt')
POOL = os.path.join(BASE, 'data', 'v51_pool.json')
OUT = os.path.join(BASE, 'data', 'v51_history.json')
CACHE = os.path.join(BASE, 'data', 'k180_cache.json')

CFG = dict(priceLo=20, priceHi=40, amt20Min=5000, retWin=60, amtWin=20, pickN=3, topN=12)
WIDE_LO, WIDE_HO = 10.0, 90.0          # 今日价格宽带：捕捉「过去在 20~40、现在跑出区间」的票
K_LEN = 180
UA = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
      'Referer': 'https://gu.qq.com/'}


def get(url, timeout=25, gbk=True):
    req = urllib.request.Request(url, headers=UA)
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    return raw.decode('gbk', 'ignore') if gbk else raw.decode('utf-8', 'ignore')


def r2(x):
    return round(x, 2)


def kline(key, n=K_LEN):
    url = 'https://ifzq.gtimg.cn/appstock/app/kline/kline?param=%s,day,,,%d,&_var=k_%s' % (key, n, key)
    txt = get(url, gbk=False)
    j = json.loads(txt[txt.index('=') + 1:])
    d = j['data'][key]
    return d.get('day') or d.get('qfqday') or []


# ───────── ① 全市场快照 ─────────
def load_universe():
    raw = io.open(UNIV, encoding='utf-8').read().strip()
    return [raw[i:i + 6] for i in range(0, len(raw), 6)]


def snapshot(codes):
    rows, lock = [], threading.Lock()
    batches = [codes[i:i + 80] for i in range(0, len(codes), 80)]

    def snap(b):
        url = 'https://qt.gtimg.cn/q=' + ','.join((c[0] == '6' and 'sh' or 'sz') + c for c in b)
        for attempt in (0, 1, 2):
            try:
                txt = get(url)
                break
            except Exception:
                time.sleep(0.6 * (attempt + 1))
        else:
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
            nm, px = f[1], float(f[3])
            if not nm or px <= 0:
                continue
            up = nm.upper()
            if 'ST' in up or '退' in nm:
                continue
            out.append(dict(code=key[2:], mkt=key[:2], name=nm, p=round(px, 2)))
        with lock:
            rows.extend(out)

    ths = []
    for b in batches:
        ths.append(threading.Thread(target=snap, args=(b,)))
    for i in range(0, len(ths), 6):
        g = ths[i:i + 6]
        for t in g: t.start()
        for t in g: t.join()
        print('  快照 %d/%d · 命中 %d 只' % (min(i + 6, len(batches)), len(batches), len(rows)), flush=True)
    return rows


# ───────── ② 日 K ─────────
def fetch_klines(keys, threads=10):
    cache = {}
    if os.path.exists(CACHE):
        try:
            cache = json.load(io.open(CACHE, encoding='utf-8'))
        except Exception:
            cache = {}
    todo = [k for k in keys if k not in cache]
    print('  日K：需抓 %d 只（缓存命中 %d）' % (len(todo), len(keys) - len(todo)), flush=True)
    if todo:
        q, lock = queue.Queue(), threading.Lock()
        for k in todo:
            q.put(k)

        def work():
            while True:
                try:
                    k = q.get_nowait()
                except queue.Empty:
                    return
                kl = None
                for attempt in (0, 1, 2):
                    try:
                        kl = kline(k)
                        if kl:
                            break
                    except Exception:
                        pass
                    time.sleep(0.5 * (attempt + 1))
                with lock:
                    cache[k] = kl or []
                q.task_done()

        ths = [threading.Thread(target=work) for _ in range(threads)]
        for t in ths: t.start()
        for t in ths: t.join()
        try:
            json.dump(cache, io.open(CACHE, 'w', encoding='utf-8'), ensure_ascii=False)
        except Exception as e:
            print('  缓存写入失败（忽略）:', e)
    return cache


# ───────── ③ 对齐到交易日历 ─────────
def align(cal, kl):
    """返回 (o,c,h,l,v) 五个 list，长度 = len(cal)，缺勤为 None"""
    idx = {d: i for i, d in enumerate(cal)}
    n = len(cal)
    o, c, h, l, v = [None] * n, [None] * n, [None] * n, [None] * n, [None] * n
    for row in kl:
        i = idx.get(row[0])
        if i is None:
            continue
        try:
            o[i] = float(row[1]); c[i] = float(row[2])
            h[i] = float(row[3]); l[i] = float(row[4])
            v[i] = float(row[5]) * 100          # 手 → 股
        except Exception:
            pass
    return o, c, h, l, v


def rank_day(i, stocks, cal):
    """第 i 个交易日的 v5.1 榜单（过滤全按当天的值）"""
    W, A = CFG['retWin'], CFG['amtWin']
    got = []
    for s in stocks:
        c, vv = s['c'], s['v']
        ci = c[i]
        if ci is None or not (CFG['priceLo'] <= ci <= CFG['priceHi']):
            continue
        j = i - W
        if j < 0 or c[j] is None:
            continue
        ret = (ci / c[j] - 1) * 100
        tot, cnt = 0.0, 0
        for k in range(i - A + 1, i + 1):
            if k >= 0 and c[k] is not None and vv[k] is not None:
                tot += c[k] * vv[k]; cnt += 1
        if cnt < A:
            continue
        amt = (tot / cnt) / 1e8                 # 亿元
        if amt * 1e4 < CFG['amt20Min']:
            continue
        got.append((ret, s['name'], s['id'], ci, amt))
    got.sort(key=lambda x: x[0])
    return got


# ───────── ④ 单批模拟 ─────────
def simulate(sig_i, picks, cal, board):
    """picks: 信号日榜单前 3；返回该批的模拟结果"""
    n = len(cal)
    out = []
    for ret60, nm, sid, cl, amt in picks:
        s = board['map'][sid]
        o, c, h, l = s['o'], s['c'], s['h'], s['l']
        buy_i, entry, tries = sig_i + 1, None, 0
        while buy_i < n and tries < 3:
            if o[buy_i] is None or c[buy_i] is None:
                buy_i += 1; continue
            lim = o[buy_i] * 0.995
            if l[buy_i] is not None and l[buy_i] <= lim:
                entry = r2(lim); break
            buy_i += 1; tries += 1              # 当天没摸到 → 次日按新开盘价重挂
        if entry is None:
            out.append(dict(name=nm, code=sid, ret60=r2(ret60), amt20=round(amt, 2),
                            filled='', entry=None, exit_d='', exit_px=None, ret=None, reason='三天没成交'))
            continue
        stop = r2(entry * 0.85)
        trail = r2(entry * 1.15)
        due_i = min(buy_i + 12, n - 1)
        hi, launched, exit_i, exit_px, reason = c[buy_i], False, due_i, None, '到期'
        for i in range(buy_i, due_i + 1):
            ci = c[i]
            if ci is None:
                continue
            hi = max(hi, ci)
            if ci >= trail:
                launched = True
            if i > buy_i:
                if ci <= stop:
                    exit_i, exit_px, reason = i, stop, '止损'
                    break
                if launched and ci <= hi * 0.90:
                    exit_i, exit_px, reason = i, r2(hi * 0.90), '移动止盈'
                    break
        if exit_px is None:
            if reason == '到期':
                for i in range(due_i, buy_i - 1, -1):
                    if c[i] is not None:
                        exit_i, exit_px = i, c[i]
                        break
            else:
                exit_px = stop
        ret = (exit_px - entry) / entry * 100
        out.append(dict(name=nm, code=sid, ret60=r2(ret60), amt20=round(amt, 2),
                        filled=cal[buy_i], entry=entry, stop=stop, trail=trail,
                        exit_d=cal[exit_i], exit_px=r2(exit_px), ret=r2(ret), reason=reason))
    return out


def main():
    codes = load_universe()
    print('全市场主板 %d 只' % len(codes), flush=True)

    rows = snapshot(codes)
    if not rows:
        sys.exit('快照没拉到，检查网络')
    wide = [r for r in rows if WIDE_LO <= r['p'] <= WIDE_HO]
    print('  宽带候选（今日 %.0f~%.0f 元）%d 只' % (WIDE_LO, WIDE_HO, len(wide)), flush=True)

    # 指数 → 交易日历
    idx_kl = kline('sh000001', K_LEN)
    cal = [x[0] for x in idx_kl]
    print('  交易日历 %d 天：%s ~ %s' % (len(cal), cal[0], cal[-1]), flush=True)

    keys = [r['mkt'] + r['code'] for r in wide]
    cache = fetch_klines(keys)

    board = {'map': {}}
    stocks = []
    for r in wide:
        k = r['mkt'] + r['code']
        kl = cache.get(k) or []
        if len(kl) < CFG['retWin'] + CFG['amtWin']:
            continue
        o, c, h, l, v = align(cal, kl)
        s = dict(id=k, name=r['name'], o=o, c=c, h=h, l=l, v=v)
        stocks.append(s)
        board['map'][k] = s
    print('  对齐完成 %d 只' % len(stocks), flush=True)

    n = len(cal)
    start = CFG['retWin'] + 1

    # ── 真实锚点：第 1 批信号日 2026-09-07 ──
    anchor_sig = '2026-09-07'
    if anchor_sig not in cal:
        sys.exit('锚点 %s 不在交易日历里（可能没开盘）' % anchor_sig)
    ai = cal.index(anchor_sig)

    # 往前推信号日：sig_{n-1} = due_{n-1} 往前：buy = due-12, sig = buy-1
    sigs = []
    i = ai
    while True:
        b = i - 1 - 12          # buy = sig+1；sig_prev = buy_prev... 反推：due_prev = sig → buy_prev = due_prev-12 → sig_prev = buy_prev-1
        buy_prev = i - 12
        sig_prev = buy_prev - 1
        if sig_prev < start:
            break
        i = sig_prev
        sigs.append(sig_prev)
    sigs = sorted(sigs) + [ai]
    # 往后推（含当前批）
    i = ai
    while True:
        buy = i + 1
        due = buy + 12
        if due >= n:
            break
        i = due
        sigs.append(due)

    print('  信号日 %d 个：%s ~ %s' % (len(sigs), cal[sigs[0]], cal[sigs[-1]]), flush=True)

    # ── 逐回 ──
    batches, boards = [], {}
    for bi, si in enumerate(sigs, 1):
        got = rank_day(si, stocks, cal)
        if len(got) < CFG['pickN']:
            continue
        picks = got[:CFG['pickN']]
        res = simulate(si, picks, cal, board)
        ok = [x for x in res if x['ret'] is not None]
        bret = sum(x['ret'] for x in ok) / len(ok) if ok else None
        batches.append(dict(no=bi, sig=cal[si], buy=cal[min(si + 1, n - 1)],
                            due=cal[min(si + 13, n - 1)], ret=r2(bret) if bret is not None else None,
                            win=(sum(1 for x in ok if x['ret'] > 0) / len(ok) * 100) if ok else None,
                            picks=res))
        boards[cal[si]] = dict(sig=1, top=[dict(name=x[1], code=x[2], ret60=r2(x[0]),
                                                buy=r2(x[3] * 0.995)) for x in got[:CFG['topN']]])

    # ── 普通交易日也留一份榜（只留前 3，压缩体积）──
    for i in range(start, n):
        d = cal[i]
        if d in boards:
            continue
        got = rank_day(i, stocks, cal)
        if got:
            boards[d] = dict(top=[dict(name=x[1], code=x[2], ret60=r2(x[0])) for x in got[:CFG['pickN']]])

    done = [b for b in batches if b['ret'] is not None]
    stats = dict(
        batches=len(batches), settled=len(done),
        avg=r2(sum(b['ret'] for b in done) / len(done)) if done else None,
        winrate=r2(sum(1 for b in done if b['ret'] > 0) / len(done) * 100) if done else None,
        best=max((b['ret'] for b in done), default=None),
        worst=min((b['ret'] for b in done), default=None),
        cum=r2(_cum(done)) if done else None,
    )

    data = dict(generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                window=[cal[start], cal[-1]], anchor=anchor_sig,
                universe=len(stocks), calendar=cal[start:],
                stats=stats, batches=batches, boards=boards)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(data, io.open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)
    print('\n✅ %s  (%d 字节)' % (OUT, os.path.getsize(OUT)))
    print('   回算区间 %s ~ %s · 候选 %d 只 · 榜单 %d 天 · 批次 %d（已结算 %d）'
          % (cal[start], cal[-1], len(stocks), len(boards), len(batches), len(done)))
    if done:
        print('   平均 %.2f%% · 胜率 %.0f%% · 最好 %.2f%% · 最差 %.2f%% · 复利 %.2f%%'
              % (stats['avg'], stats['winrate'], stats['best'], stats['worst'], stats['cum']))
    print('\n   批次明细：')
    for b in batches:
        if b['ret'] is None:
            print('   第%2d批 %s 至今' % (b['no'], b['sig']))
        else:
            picks = ' / '.join('%s %s %+.1f%%(%s)' % (p['name'], p['code'], p['ret'], p['reason'])
                               for p in b['picks'] if p['ret'] is not None)
            print('   第%2d批 %s → %+.2f%%  %s' % (b['no'], b['sig'], b['ret'], picks))


def _cum(done):
    v = 1.0
    for b in done:
        v *= (1 + b['ret'] / 100)
    return (v - 1) * 100


if __name__ == '__main__':
    main()
