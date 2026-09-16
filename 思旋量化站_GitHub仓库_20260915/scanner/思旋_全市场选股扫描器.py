#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
思旋 · 全市场选股扫描器  v1.1  (2026-08-31 建立, v3.17 升级)
=================================================
解决的核心问题：以前每天只人工看 20-30 只票（采样率 0.4%），
导致天天"没机会"。本工具 6 秒扫完全市场 5400+ 只，
让全市场自己把「此刻正在回踩买点」的票吐出来。

筛选框架升级史：
  v3.15 买点是第一道闸门：①买区(±2.5%) ②生命周期(多头=主升期) ③质量
  v3.16 治"天天没机会"根因：全市场暴力遍历 + 板块联动验证（当日兄弟票普涨）
  v3.17 【思旋 8/31 13:40 指令"主线要是一段时间的真主线·市场差时好票会跌到位置·选股要灵活"】
       → Stage4 升级为双引擎：
         (A) 持续性主线：用板块组内 r5/r10/r20 复合强度 + 多头排列占比量化
             "一段时间真主线"，而非看单日涨跌（单日跌不代表主线结束）
         (B) 弱市回踩买点：当大盘弱（创业板/上证齐跌）时，在真主线组内找
             "随大盘缩量回踩到关键支撑(MA10/MA20)且未破位"的票＝被错杀跌到位置的好票
       → 同时保留原 Stage3 CORE（强市右侧追强用），两种模式按大盘环境自动切换

账户硬约束：NO_CYK=True，创业板300/301、科创板688、北交所一律禁止（思旋定调·焊死）

用法：
  python3 思旋_全市场选股扫描器.py --v51       # ★ v5.1 每日选股榜（网站数据层，默认）
  python3 思旋_全市场选股扫描器.py             # 全量扫描（v4 遗留，仅对照，不作买入依据）
  python3 思旋_全市场选股扫描器.py --top 30    # 指定输出条数
  python3 思旋_全市场选股扫描器.py --reuse     # 复用 /tmp/kl_analyzed.json 跳过拉取

数据源降级链（v3.14）：
  L1 腾讯 qt.gtimg.cn 快照   -> 稳定
  L2 腾讯 fqkline K线        -> 大量请求会触发 WAF 501，需限流（v5.1 已弃用）
  L3 新浪 CN_MarketData K线  -> ✅ v5.1 指定口径（datalen=70，不复权）
  L4 东财 push2              -> 经常性不可用

──────────────────────────────────────────────────────────────────────
【v5.1 数据层改造说明（2026-09-09）】—— 本轮只改数据层，不重写，v4 逻辑原样保留
  新增：prefilter_v51() / kline_sina70() / calc_v51() / stage_v51() / build_site_data()
  停用：prefilter() / qualify() / stage3() / stage4_flex() 均为 v4 口径，与 v5.1 方向相反，
        **不得作为买入依据**（追当日最强实测为负 alpha：涨停潮次日买涨停 −1.529% t=−4.71）。
  复用：stage1() 全市场快照、线程池骨架、tag_sector() 板块启发式标签。
"""
import requests, concurrent.futures, re, json, time, sys, os, argparse, bisect

OUT = os.environ.get('SCAN_OUT', '/tmp')

# 账户硬约束：无创业板/科创板交易权限（思旋 8/31 再次强调，之前曾提过）
NO_CYK = True

# ---------------- Stage 1 ----------------
def gen_codes():
    codes = []
    for i in range(600000, 604001): codes.append('sh%06d' % i)
    for i in range(605000, 606001): codes.append('sh%06d' % i)   # ⚠️ 605 段：原版本漏了，主板规则里有 605 却扫不到
    for i in range(1, 4001):        codes.append('sz%06d' % i)
    for i in range(300000, 302001): codes.append('sz%06d' % i)
    for i in range(688000, 689001): codes.append('sh%06d' % i)
    return codes

SNAP = 'https://qt.gtimg.cn/q='

def fetch_snap(batch):
    for _ in range(2):
        try:
            txt = requests.get(SNAP + ','.join(batch), timeout=12).content.decode('gbk', 'ignore')
            break
        except Exception:
            txt = ''
            time.sleep(0.6)
    if not txt:
        return []
    out = []
    for m in re.finditer(r'v_(\w+)="(.*?)";', txt):
        code = m.group(1); f = m.group(2).split('~')
        if len(f) < 50: continue
        try:
            name = f[1]; price = float(f[3]); chg = float(f[32])
        except Exception: continue
        if not name or price <= 0: continue
        def fl(i, d=0.0):
            try: return float(f[i])
            except Exception: return d
        out.append({'code': code, 'name': name, 'price': price, 'chg': chg,
                    'turn': fl(38), 'pe': fl(39), 'pb': fl(46), 'fmv': fl(44),
                    'lb': fl(49), 'amp': fl(43), 'amount': fl(37)})
    return out

def stage1():
    codes = gen_codes()
    batches = [codes[i:i+100] for i in range(0, len(codes), 100)]
    rows = []
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        for rs in ex.map(fetch_snap, batches):
            rows.extend(rs)
    seen = {}
    for r in rows: seen[r['code']] = r
    rows = list(seen.values())
    print('[Stage1] 全市场有效标的 %d 只，耗时 %.1fs' % (len(rows), time.time()-t0))
    json.dump(rows, open(OUT + '/all_snapshot.json', 'w'), ensure_ascii=False)
    return rows

# ---------------- Stage 2 ----------------
KL_TX = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,60,qfq'
KL_SINA = 'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=70'

def prefilter(rows, exclude_cyk=NO_CYK):
    """exclude_cyk=True 时剔除创业板(300/301)与科创板(688)——思旋账户无此两板交易权限"""
    out = []
    for r in rows:
        n = r['name'].upper()
        if 'ST' in n or '退' in r['name']: continue
        if exclude_cyk and r['code'][2:5] in ('300', '301', '688'):
            continue
        if not (4.0 <= r['price'] <= 60.0):   continue
        # v3.19 修复：原上限 900 亿把全部行业龙头（紫金8900亿/洛钼4000亿/江铜1600亿…）在入口就杀掉，
        # 导致扫描器「从设计上选不出龙头」。放宽到 3000 亿，让真龙头进池参与龙头优先排序。
        if not (30.0 <= r['fmv'] <= 10000.0): continue   # 流通市值 30-10000 亿（覆盖紫金8900亿等超大龙头）
        if not (-5.0 <= r['chg'] <= 6.0):     continue   # 整理态
        if not (0.8 <= r['turn'] <= 20.0):    continue
        if not (0.5 <= r['lb'] <= 2.5):       continue
        if not (0 < r['pe'] <= 80):           continue
        out.append(r)
    return out

def kline_tx(code):
    try:
        d = requests.get(KL_TX % code, timeout=12).json()
        node = d['data'][code]
        kl = node.get('qfqday') or node.get('day')
        if kl and len(kl) >= 35:
            return [[x[0], float(x[1]), float(x[2]), float(x[3]), float(x[4]),
                     float(x[5]) if len(x) > 5 else 0.0] for x in kl]
    except Exception:
        pass
    return None

def kline_sina(code):
    try:
        kl = requests.get(KL_SINA % code, timeout=15).json()
        if kl and len(kl) >= 35:
            return [[x['day'], float(x['open']), float(x['close']), float(x['high']),
                     float(x['low']), float(x['volume'])] for x in kl]
    except Exception:
        pass
    return None

def calc(code, kl):
    cl = [x[2] for x in kl]; vo = [x[5] for x in kl]
    i = len(cl) - 1
    ma = lambda k: sum(cl[i-k+1:i+1]) / k
    ma5, ma10, ma20, ma30 = ma(5), ma(10), ma(20), ma(30)
    px = cl[-1]
    v_prev = sum(vo[-10:-5]) / 5
    vr = (sum(vo[-5:]) / 5) / v_prev if v_prev > 0 else 9.99
    r5  = round((px/cl[i-5]-1)*100, 2)  if i >= 5  else 0.0
    r10 = round((px/cl[i-10]-1)*100, 2) if i >= 10 else 0.0
    # v4.0 新增 amp5：近 5 日日均振幅 (高-低)/收
    #   回测结论：严过滤高波动（振幅≤4.5%）**降低**收益，故不作为硬门槛，
    #   只做展示 + 上限 10% 的宽松兜底，供人工判断"这票颠不颠"。
    _amps = [(float(k[3])-float(k[4]))/float(k[2])*100 for k in kl[-5:] if float(k[2]) > 0]
    amp5 = round(sum(_amps)/len(_amps), 2) if _amps else 0.0
    return {'code': code, 'ma5': round(ma5,3), 'ma10': round(ma10,3), 'ma20': round(ma20,3),
            'ma30': round(ma30,3), 'px': px, 'amp5': amp5,
            'd5': round((px/ma5-1)*100,2), 'd10': round((px/ma10-1)*100,2),
            'd20': round((px/ma20-1)*100,2), 'bull': ma5 > ma10 > ma20,
            'vr': round(vr,2), 'heat': round((ma10/ma20-1)*100,2),
            'r5': r5, 'r10': r10,
            'r20': round((px/cl[i-20]-1)*100,2), 'r60': round((px/cl[0]-1)*100,2),
            'dd5': round((px/max(x[3] for x in kl[-5:])-1)*100,2), 'bars': len(cl)}

def stage2(rows, workers=20):
    cand = prefilter(rows, exclude_cyk=NO_CYK)
    print('[Stage2] 粗筛 %d 只，开始拉K线...' % len(cand))
    res, fail = [], []
    t0 = time.time()
    def work(r):
        kl = kline_tx(r['code'])
        if not kl:
            time.sleep(0.3)
            kl = kline_sina(r['code'])
        if not kl:
            return r['code'], None
        return r['code'], calc(r['code'], kl)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for code, x in ex.map(work, cand):
            if x: res.append({**next(c for c in cand if c['code'] == code), **x})
            else: fail.append(code)
    print('[Stage2] 解析成功 %d 只，失败 %d，耗时 %.0fs' % (len(res), len(fail), time.time()-t0))
    json.dump(res, open(OUT + '/kl_analyzed.json', 'w'), ensure_ascii=False)
    return res

# ---------------- Stage 3 (v3.15/3.16 原闸门，强市右侧用) ----------------
def qualify(x):
    # ⚠️ v4.0 修正（2026-09-04 回测）：原条件为 x['bull']（三均线多头排列 MA5>MA10>MA20）。
    # 回测证明「三均线多头排列」是有害条件 —— 含它的组合全部落在最差区间，
    # 超额 -0.37% ~ -0.45%，t = -3.9 ~ -5.8（统计显著为负）。已降级为「趋势向上 MA5>MA10」。
    if x['ma5'] <= x['ma10']:         return False   # ② 趋势向上（**不是三均线多头排列**）
    if abs(x['d10']) > 2.5:           return False   # ① 买区闸门（第一道）
    if x['vr'] >= 1.25:               return False   # 缩量企稳
    if not (2.0 <= x['heat'] <= 9.0): return False   # 未过热
    if not (3.0 <= x['r20'] <= 35.0): return False   # 有涨幅但非暴涨
    if x['dd5'] < -6.0:               return False   # 近5日未破位
    if not (0 < x['pe'] <= 60):       return False
    if not (30 <= x['fmv'] <= 10000): return False   # v3.19 同 prefilter，放开龙头入池
    if not (0.8 <= x['turn'] <= 12):  return False
    return True

def stage3(res, top=30):
    if NO_CYK:
        n0 = len(res)
        res = [x for x in res if x['code'][2:5] not in ('300', '301', '688')]
        print('[账户过滤] 剔除创业板/科创板 %d 只，剩余 %d' % (n0 - len(res), len(res)))
    for x in res:
        if 'heat' not in x:
            x['heat'] = round((x['ma10'] / x['ma20'] - 1) * 100, 2)
        if 'dd5' not in x:
            x['dd5'] = 0.0
    A = [x for x in res if x['ma5'] > x['ma10'] and abs(x['d10']) <= 2.5 and x['vr'] < 1.35]
    core = [x for x in res if qualify(x)]
    core.sort(key=lambda x: abs(x['d10']) + x['vr'] * 0.5)
    print('\n[Stage3·旧版对照·仅供参考] A档(买区+缩量+趋势向上): %d 只' % len(A))
    print('[Stage3·旧版对照·仅供参考] CORE(含过热度/估值/涨幅过滤): %d 只' % len(core))
    print('   ⚠️ 本表为 v3.x 遗留口径，**不作为买入依据**；买入一律以底部 [v4.0 回测验证候选] 为准')
    h = '%-3s %-8s %-8s %6s %7s %7s %7s %6s %6s %6s %6s %7s %7s %7s'
    print('\n' + h % ('#','代码','名称','现价','MA10','MA20','距MA10%','量能比','过热%','换手%','PE','20日涨%','5日回撤%','流通亿'))
    for i, x in enumerate(core[:top], 1):
        print(h % (i, x['code'], x['name'][:6], '%.2f' % x['price'], '%.2f' % x['ma10'],
                   '%.2f' % x['ma20'], '%+.2f' % x['d10'], '%.2f' % x['vr'],
                   '%.1f' % x['heat'], '%.2f' % x['turn'], '%.1f' % x['pe'],
                   '%+.1f' % x['r20'], '%.1f' % x['dd5'], '%.0f' % x['fmv']))
    json.dump({'A': A, 'core': core}, open(OUT + '/gate_result.json', 'w'), ensure_ascii=False)
    return A, core

# ---------------- Stage 4 (v3.17 双引擎：持续性主线 + 弱市回踩买点) ----------------
# 板块关键词字典：用名称启发式聚合板块（腾讯快照无行业字段）。
# 顺序即优先级，具体板块放前，泛板块放后。
SECTOR_KW = [
    ('涂料建材', ['涂料','油漆','建材','防水','石膏','管材','玻璃','陶瓷','装饰','幕墙','水泥','混凝土','石英','纤维','五金']),
    ('铝业',     ['铝']),
    ('电磁线',   ['电磁','漆包','扁线','线缆','电缆','铜缆']),
    ('锂电新能', ['锂电','电池','储能','光伏','充电桩','电解液','锂业','正极']),
    ('半导体',   ['半导体','芯片','集成','微电子','封测','硅片']),
    ('汽车汽配', ['汽车','汽配','车灯','轮胎','齿轮','轴承','底盘','座椅','内饰']),
    ('医药生物', ['医药','生物','制药','药业','医疗','基因','疫苗','酶','保健']),
    ('食品消费', ['食品','饮料','乳','农','牧','渔','糖','盐','粮油','酒']),
    ('航运物流', ['航运','海运','港','物流','运输','快递']),
    ('煤炭电力', ['煤','电力','能源','燃气','水务','热电']),
    ('家电',     ['家电','电器','空调','厨']),
    ('钢铁',     ['钢铁','特钢','不锈']),
    ('有色矿业', ['铜','锌','锡','黄金','稀土','矿业','资源','金属','镍','铅']),
    ('化工',     ['化工','化学','树脂','橡胶','塑料','化纤','高分子','材料']),
    ('机械装备', ['机械','装备','重工','工程','机床','泵','阀']),
    ('地产',     ['地产','置业','城发','城建','开发']),
    ('军工',     ['军工','防务','航空','航天','船舶','兵器']),
    ('软件AI',   ['软件','信息','智能','数据','网络']),
]

def tag_sector(name):
    for sec, kws in SECTOR_KW:
        if any(k in name for k in kws):
            return sec
    return '其他'

# ---------------- v3.19 龙头识别（思旋 9/1 纠偏·核心修复）----------------
# 背景：v3.16/17 重建扫描器时丢失了 v3.4.0「强制龙头优先」纪律，
# 评分纯按「距MA10近+缩量」排序 → 天然选出波动最大的投机票（涨得猛、跌得狠，正好落在MA10），
# 而真龙头走势稳健不易跌到MA10，反而被筛掉。结果选的是「派发票」不是「回踩到位的龙头」。
def mark_pump(x):
    """伪回踩识别：暴涨后崩回 MA10 = 派发嫌疑，非健康回踩"""
    r20, dd5 = x.get('r20', 0), x.get('dd5', 0)
    x['pump'] = bool(r20 > 22 and dd5 < -4.0)
    x['overheat'] = bool(r20 > 35)
    return x

# 核心板块公认龙头硬标记（v3.19）：避免"粗筛池内市值最大"误判成龙头
# （粗筛池是全市场子集，组内第一未必是真龙头——如赤峰黄金在有色的粗筛池里市值最大，但真龙头是紫金）
SECTOR_LEADERS = {
    '有色矿业': ['紫金矿业','洛阳钼业','江西铜业','中国铝业','北方稀土','山东黄金','西部矿业','中金黄金','铜陵有色','云南铜业','兴业银锡'],
    '铝业':     ['中国铝业','南山铝业','云铝股份','神火股份','明泰铝业'],
    '煤炭电力': ['中国神华','陕西煤业','兖矿能源','山西焦煤','潞安环能','长江电力','华能国际','国电电力'],
    '化工':     ['万华化学','华鲁恒升','宝丰能源','卫星化学','荣盛石化','恒力石化'],
    '钢铁':     ['宝钢股份','中信特钢','华菱钢铁','南钢股份'],
    '锂电新能': ['宁德时代','赣锋锂业','天齐锂业','亿纬锂能','华友钴业'],
    '航运物流': ['中远海控','招商轮船','中远海能','顺丰控股','东航物流'],
    '涂料建材': ['东方雨虹','三棵树','北新建材','海螺水泥','科顺股份'],
    '家电':     ['美的集团','格力电器','海尔智家','老板电器'],
    '汽车汽配': ['比亚迪','长城汽车','福耀玻璃','华域汽车','赛力斯'],
    '医药生物': ['恒瑞医药','药明康德','迈瑞医疗','云南白药','片仔癀'],
    '食品消费': ['贵州茅台','五粮液','伊利股份','海天味业','双汇发展'],
    '半导体':   ['中芯国际','北方华创','韦尔股份','长电科技'],
    '军工':     ['中航沈飞','航发动力','中航光电','中航西飞'],
    '机械装备': ['三一重工','中联重科','徐工机械','恒立液压'],
    '地产':     ['保利发展','万科A','招商蛇口','华发股份'],
    '电磁线':   ['精达股份','长城科技','冠城大通'],
    '软件AI':   ['科大讯飞','用友网络','金山办公','浪潮信息'],
}

def leader_rank(pool):
    """板块内龙头识别：公认龙头名单硬标记 + 流通市值百分位（双轨，名单优先）"""
    g = {}
    for x in pool:
        g.setdefault(x.get('sector') or tag_sector(x['name']), []).append(x)
    for sec, arr in g.items():
        arr.sort(key=lambda z: -z.get('fmv', 0))
        n = len(arr)
        named = SECTOR_LEADERS.get(sec, [])
        for i, x in enumerate(arr):
            x['cap_pct'] = round(1.0 - i / float(max(n - 1, 1)), 3) if n > 1 else 1.0
            x['is_lead'] = x['cap_pct'] >= 0.70            # 组内市值前 30% = 龙头候选
            if x['name'] in named:                          # 公认龙头名单 → 强制龙头
                x['cap_pct'], x['is_lead'], x['named_lead'] = 1.0, True, True
            mark_pump(x)
    return pool

def sector_strength(res):
    """量化「一段时间真主线」：板块组内 r5/r10/r20 复合强度 + 多头排列占比"""
    g = {}
    for x in res:
        g.setdefault(x['sector'], []).append(x)
    out = {}
    for sec, xs in g.items():
        if len(xs) < 2:          # 单只样本不足以称板块，降权
            out[sec] = {'n': len(xs), 'r5': 0, 'r10': 0, 'r20': 0, 'bull': 0, 'main': False}
            continue
        n = len(xs)
        r5 = sum(x.get('r5', 0) for x in xs) / n
        r10 = sum(x.get('r10', 0) for x in xs) / n
        r20 = sum(x.get('r20', 0) for x in xs) / n
        bull = sum(1 for x in xs if x['bull']) / n
        # 真主线：r20 持续强(>4%) 且 r10 仍为正(近期未退潮) 且 组内多头占比高(>0.5)
        main = (r20 > 4.0) and (r10 > -1.0) and (bull > 0.5)
        out[sec] = {'n': n, 'r5': round(r5, 1), 'r10': round(r10, 1),
                    'r20': round(r20, 1), 'bull': round(bull, 2), 'main': main}
    return out

def snapshot_index():
    codes = ['sh000001', 'sz399001', 'sz399006', 'sh000016', 'sz399005']
    out = {}
    try:
        txt = requests.get(SNAP + ','.join(codes), timeout=12).content.decode('gbk', 'ignore')
        for m in re.finditer(r'v_(\w+)="(.*?)";', txt):
            f = m.group(2).split('~')
            if len(f) < 40: continue
            try: out[m.group(1)] = {'name': f[1], 'price': float(f[3]), 'chg': float(f[32])}
            except Exception: pass
    except Exception:
        pass
    return out

def weak_market(idx):
    cyb = idx.get('sz399006', {}).get('chg', 0)
    sh  = idx.get('sh000001', {}).get('chg', 0)
    return (cyb <= -0.8) or (sh < 0 and cyb < 0)

def market_regime(res):
    """v4.0 市场环境开关（2026-09-04 回测·本次最重要的发现）
    规则强烈依赖市场环境，且**反直觉**：弱市/跌市最有效，市场大涨时反而亏钱。
      市场(近20日)       超额收益    胜率    动作
      大涨 > +8%         -0.93%    37.1%   禁买
      强 +3% ~ +8%       +0.33%    51.3%   观望
      平 0% ~ +3%        +0.49%    52.5%   减半
      弱 -5% ~ 0%        +2.25%    46.7%   出手
      大跌 < -5%         +1.35%    57.3%   出手
    逻辑：弱市里还能维持强势(rp≥0.70)的票是真资金抱团，反弹弹性最大；
          普涨时鸡犬升天，"强"失去区分度，此时买强势＝追高接盘。

    ⚠️ 口径必须与回测一致：回测用的是「全市场等权近20日收益」，
       这里用「全市场个股 r20 的均值」近似。
       实测校验（近30日）：两者平均绝对差仅 0.25 个百分点，最大 0.78 → 可替代。
       **不能用上证指数**：上证是市值加权（大盘股主导），与全市场等权差异可达 2 个百分点，
       会把"强市"误判成"平市"，导致在规则失效的环境里开仓。

    门槛 3% 下调为 2.5%：临界区（2.5%~3.5%）两个口径可能跨档，取更保守的一档。
    返回 (档位名, 市场近20日涨幅%, 是否可出手, 仓位系数)
    """
    vals = [x.get('r20', 0) for x in res if x.get('r20') is not None]
    if len(vals) < 200:
        return '未知(样本不足)', 0.0, False, 0.0
    m = sum(vals) / len(vals)
    if m > 8:    return '大涨(禁买)', m, False, 0.0
    if m > 2.5:  return '强市(观望)', m, False, 0.0
    if m > 0:    return '平市(减半)', m, True, 0.5
    return '弱市/跌市(可出手)', m, True, 1.0

def stage4_flex(res, top=30):
    """v3.17 双引擎：持续性主线 × 弱市回踩买点"""
    idx = snapshot_index()
    print('\n[Stage4-flex] 大盘：' + '  '.join('%s %+5.2f%%' % (v['name'], v['chg']) for v in idx.values()))
    weak = weak_market(idx)
    print('[Stage4-flex] 市场环境（v3.x 旧口径·仅参考，不决定能否开仓）：%s'
          % ('⚠️ 弱市（启用回踩买点识别）' if weak else '✅ 非弱市（右侧追强为主）'))

    for x in res:
        x['sector'] = tag_sector(x['name'])
    leader_rank(res)                      # v3.19：先算板块内龙头位次+派发嫌疑
    strengths = sector_strength(res)
    mainlines = set(s for s, v in strengths.items() if v['main'])

    # 板块持续性榜
    ranked = sorted(strengths.items(), key=lambda kv: kv[1]['r20'], reverse=True)
    print('\n[板块持续性·一段时间真主线量化] (r20=20日板块均涨, bull=多头占比)')
    print('%-10s %4s %7s %7s %7s %6s  %s' % ('板块','样本','r5%','r10%','r20%','多头%','是否真主线'))
    for sec, v in ranked[:12]:
        print('%-10s %4d %7.1f %7.1f %7.1f %6.0f%%  %s' % (
            sec, v['n'], v['r5'], v['r10'], v['r20'], v['bull']*100,
            '🔥真主线' if v['main'] else ''))

    # ===== v3.22 强度门槛（思旋 2026-09-03 13:20 纠偏·核心修复）=====
    # 教训：9/3 有色/黄金板块共振日，AI 用「位置优先」选了当日**最弱**的山东黄金
    #   （37.93·溢价 -0.62%，看着"买点好、没追高"），却以"追高 +4.33%"为由
    #   排除同板块**最强**的中金黄金。结果：中金 +3.72% 创新高，山东 -1.06% 创新低。
    # 根因：v3.17 弱市回踩引擎 `weak and chg >= 0: continue` 只留下跌的票，
    #   且 `min(d10*-0.5, 4)` 给「跌得深」加分 → 这是算法化的"贪便宜"。
    # 原理：弱势是负反馈（跌→套牢盘→反弹即抛→继续弱），强势是正反馈
    #   （涨→浮盈惜售→突破买盘→继续强）。**便宜不是折价，是风险定价。**
    # 修正：强度优先 → 位置次之。便宜的票必须先过强度门槛，过不了就是"便宜有原因"。
    sec_chg = {}
    for x in res:
        sec_chg.setdefault(x.get('sector') or tag_sector(x['name']), []).append(x.get('chg', 0))
    for _s in sec_chg:
        sec_chg[_s].sort()

    def sec_rank_pct(sector, chg):
        """板块内当日涨幅百分位：0=组内最弱，1=组内最强"""
        arr = sec_chg.get(sector) or []
        if len(arr) < 3:
            return 0.5                      # 样本太少不评判
        return round(bisect.bisect_left(arr, chg) / float(len(arr) - 1), 3)

    # ================= v4.0 选股（2026-09-04 回测验证·替换 v3.22 / v3.23）=================
    # 回测：2,425 只主板股 × 120 个交易日 × 472 组参数，样本外验证 29/32 双正（最稳一组 t=5.67 / 840 笔）
    #
    # 【已证伪并从规则中删除的三条，禁止再加回来】
    #   ① 三均线多头排列 MA5>MA10>MA20 —— 含此条件的组合全部落在最差区间，
    #      超额 -0.37% ~ -0.45%，t 值 -3.9 ~ -5.8（显著为负）。只需 MA5 > MA10。
    #   ② 波动率严过滤（振幅≤4.5%/5.5%）—— 过滤越严收益越低。低波动只改善体验、不改善收益。
    #   ③ 只买最强（rp≥0.85）—— 纯强度优先胜率 26.5%，比随机还差。最优是 rp≥0.70。
    #
    # 【保留的旧规则】v3.19 龙头优先（龙头分）+ 派发重罚（-25 分），回测未证伪。
    #
    # 全市场当日涨幅分布（回测用的是**全市场**百分位，不是板块内）
    all_chg = sorted(x.get('chg', 0) for x in res)
    def mkt_rank_pct(chg):
        if len(all_chg) < 50: return 0.5
        return bisect.bisect_left(all_chg, chg) / float(len(all_chg) - 1)

    regime, mkt_r20, can_buy, pos_scale = market_regime(res)
    print('\n[v4.0 市场环境开关] 全市场近20日 %+.2f%% → %s ｜ %s' % (
        mkt_r20, regime, ('可出手，仓位系数 %.1f' % pos_scale) if can_buy else '**禁止开新仓**'))
    print('   （口径＝全市场个股 20 日涨幅均值，与回测一致；上证为市值加权，会低估，不用）')
    print('   ⚠️ **能否开新仓一律以此开关为准**，上方 v3.x 旧口径与板块表均不决定仓位')

    picks = []
    for x in res:
        # ── v4.0 六条硬条件（网格 Top1）──
        if x.get('d5', 0) < 0: continue              # ① 站上 MA5
        if x['ma5'] <= x['ma10']: continue           # ② 趋势向上（**不是三均线多头排列**）
        chg = x.get('chg', 0)
        rp = mkt_rank_pct(chg)
        if rp < 0.70: continue                       # ③ 全市场当日强度前 30%
        r20 = x.get('r20', 0)
        if r20 > 15: continue                        # ④ 排除过热（20 日涨幅 ≤15%，获利盘重）
        if x.get('amp5', 0) > 10: continue           # ⑤ 5 日均振幅兜底（严过滤有害，仅兜底）
        d10 = x['d10']
        if not (-2.0 <= d10 <= 10.0): continue       # ⑥ 距 MA10 区间（允许强势票不深回踩）
        if not (0 < x.get('pe', 0) <= 70): continue
        if x.get('dd5', 0) < -8: continue            # 近 5 日未破位
        main = x['sector'] in mainlines
        cap_pct = x.get('cap_pct', 0.0)
        is_lead = x.get('is_lead', False)
        pump    = x.get('pump', False)
        lead_score = cap_pct * 30                    # v3.19 龙头优先（保留）
        pump_pen  = -25 if pump else 0               # v3.19 派发重罚（保留）
        # v4.0 评分：全市场强度为主(50) + 20日涨幅贴近 8% 加分 + 龙头 + 派发惩罚
        score = rp * 50 + lead_score * 0.3 + pump_pen + max(0, 15 - abs(r20 - 8)) * 0.6
        picks.append({**x, 'main': main, 'lead': is_lead, 'pump': pump,
                      'rp': rp, 'can_buy': can_buy, 'pos_scale': pos_scale,
                      'regime': regime, 'score': round(score, 2)})
    picks.sort(key=lambda x: x['score'], reverse=True)

    print('\n[v4.0 回测验证候选·Top%d] 全市场强度≥70分位 · MA5>MA10 · 20日涨≤15%% · 振幅≤10%%' % top)
    h = '%-3s %-8s %-8s %6s %7s %6s %6s %7s %6s %6s %5s %-9s %6s %6s %5s %-6s %6s'
    print(h % ('#','代码','名称','现价','MA10','距10%','量能','20日%','5日振','今日%','PE','板块','强度','市值%','龙头','派发','得分'))
    for i, x in enumerate(picks[:top], 1):
        print(h % (i, x['code'], x['name'][:6], '%.2f'%x['price'], '%.2f'%x['ma10'],
                   '%+.1f'%x['d10'], '%.2f'%x['vr'], '%+.1f'%x['r20'],
                   '%.1f'%x.get('amp5',0), '%+.1f'%x.get('chg',0), '%.0f'%x['pe'],
                   x['sector'], '%.2f'%x.get('rp',0), '%.2f'%x.get('cap_pct',0),
                   '★' if x.get('lead') else '', '⚠' if x.get('pump') else '',
                   '%.1f'%x['score']))
    print('\n[v4.0 持仓参数] 持有 20 个交易日 ｜ 止损 -8% ｜ 止盈 +8% ｜ 单只上限 60%（思旋总纲）')
    print('[v4.0 摩擦线] 单笔需 >0.37% 超额才不白干；本规则样本外超额 +1.80%/笔，扣费后 +1.43%')
    if not can_buy:
        print('\n⛔ [v4.0 市场环境=%s] 该环境下规则超额仅 +0.33%% ~ -0.93%%，扣费后为负。' % regime)
        print('   建议：不开新仓，空仓观望。现有持仓按各自止损执行。')
    json.dump({'strengths': strengths, 'picks': picks,
               'regime': regime, 'mkt_r20': mkt_r20, 'can_buy': can_buy,
               'pos_scale': pos_scale, 'rule_version': 'v4.0'},
              open(OUT + '/flex_result.json', 'w'), ensure_ascii=False)
    return strengths, picks

# ============================================================================
#  v5.1 数据层（2026-09-09 新增）—— 网站「每日选股榜」的唯一数据源
#  规则只有 3 条，禁止再加任何过滤（实测全部有害，详见运行上下文第 63 条）：
#     ① 沪深主板 600/601/603/605 + 000/001/002/003，剔 ST、剔停牌
#     ② 收盘价 20~40 元 ＋ 20 日均成交额 ≥ 5,000 万
#     ③ 按 ret60（60 日涨幅）升序取前 3
# ============================================================================

MAIN_BOARD_PREFIX = ('600', '601', '603', '605', '000', '001', '002', '003')

# 回测口径铁律（第 69 条）：新浪日 K，datalen=70，不复权。
# ⚠️ 2026-09-09 实测校准：datalen=70 返回 70 根，**ret60 必须取「末 61 根收盘」**，
#    不能取第 1 根。以 大金重工 sz002487 校验：
#       取第 1 根 → −53.66%（错）      取末 61 根 → −44.18%（与 9/7 选股池一致 ✅）
KL_SINA70 = ('https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
             'CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=70')


def is_main_board(code):
    """code 形如 sh600000 / sz002487；只认沪深主板，创业板/科创板/北交所一律 False"""
    return code[2:5] in MAIN_BOARD_PREFIX


def prefilter_v51(rows):
    """v5.1 第一道闸门：主板 + 20~40 元 + 剔 ST/退市/停牌。
    ⛔ 不要在这里加 PE / 市值 / 涨跌幅 / 量比 / 均线 —— 那是 v4 的 prefilter()，已实测有害。"""
    out = []
    for r in rows:
        n = r['name'].upper()
        if 'ST' in n or '退' in r['name']:
            continue
        if not is_main_board(r['code']):
            continue
        if not (20.0 <= r['price'] <= 40.0):      # 整手约束：1 万本金÷3 必须买得起 100 股
            continue
        if r.get('amount', 0) <= 0:               # 停牌 / 无成交
            continue
        out.append(r)
    return out


def kline_sina70(code, retry=2):
    """v5.1 唯一 K 线源。返回 [[day, open, close, high, low, volume], ...]（不复权）"""
    for _ in range(retry):
        try:
            kl = requests.get(KL_SINA70 % code, timeout=15).json()
            if kl and len(kl) >= 61:
                return [[x['day'], float(x['open']), float(x['close']),
                         float(x['high']), float(x['low']), float(x['volume'])] for x in kl]
        except Exception:
            time.sleep(0.4)
    return None


def calc_v51(kl):
    """在 70 根不复权日 K 上算 v5.1 需要的全部字段"""
    cl = [x[2] for x in kl]
    hi = [x[3] for x in kl]
    vo = [x[5] for x in kl]
    # ⚠️ 口径：末 61 根收盘 → 跨度正好 60 个交易日（已与 9/7 选股池 −44.18% 对上）
    ret60 = round((cl[-1] / cl[-61] - 1) * 100, 2)
    r5 = round((cl[-1] / cl[-6] - 1) * 100, 2)
    r20 = round((cl[-1] / cl[-21] - 1) * 100, 2)
    hi60 = max(hi[-61:])
    dd60 = round((cl[-1] / hi60 - 1) * 100, 2)          # 距 60 日最高（负）
    # 20 日均成交额（元）。新浪日 K 不含金额，用 close × volume 估算，页面须标注「估算」
    amt20 = sum(cl[i] * vo[i] for i in range(len(cl) - 20, len(cl))) / 20.0
    return {'ret60': ret60, 'r5': r5, 'r20': r20, 'dd60': dd60,
            'hi60': round(hi60, 2), 'amt20': round(amt20 / 1e8, 4),   # 亿元
            'bars': len(cl), 'last_day': kl[-1][0], 'open_last': round(kl[-1][1], 2)}


def stage_v51(rows, workers=24, top=12):
    """v5.1 主流程：粗筛 → 拉 K → 算 ret60/amt20 → 过滤 → ret60 升序"""
    cand = prefilter_v51(rows)
    print('[v5.1] 主板 + 20~40元 + 剔ST/停牌 → %d 只，开始拉新浪日K（datalen=70 不复权）...' % len(cand))
    res, fail = [], []
    t0 = time.time()

    def work(r):
        kl = kline_sina70(r['code'])
        if not kl:
            return r['code'], None
        return r['code'], calc_v51(kl)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for code, x in ex.map(work, cand):
            if x:
                res.append({**next(c for c in cand if c['code'] == code), **x})
            else:
                fail.append(code)
    print('[v5.1] 解析成功 %d 只，失败 %d，耗时 %.0fs' % (len(res), len(fail), time.time() - t0))

    n_amt = len(res)
    res = [x for x in res if x['amt20'] * 1e8 >= 5.0e7]     # 20 日均成交额 ≥ 5000 万
    print('[v5.1] 20日均额≥5000万 → %d 只（剔除 %d 只）' % (len(res), n_amt - len(res)))

    res.sort(key=lambda x: x['ret60'])                       # ③ ret60 升序
    for i, x in enumerate(res, 1):
        x['rank'] = i
        x['sector'] = tag_sector(x['name'])
        x['five_star'] = (i <= 3)                            # 三项全过 + ret60 进同池前 3
    return res


def trading_calendar(index_code='sh000001'):
    """用上证指数日 K 的日期序列当交易日历（比自己算节假日可靠）"""
    try:
        kl = requests.get(KL_SINA70 % index_code, timeout=15).json()
        return [x['day'] for x in kl]
    except Exception:
        return []


def build_site_data(pool, top=12, out_json=None):
    """产出网站内联数据：榜单 + 大盘 + 交易日历"""
    idx = snapshot_index()
    cal = trading_calendar()
    topn = pool[:top]
    data = {
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'last_trade_day': topn[0]['last_day'] if topn else (cal[-1] if cal else ''),
        'index': {'sh': idx.get('sh000001', {}).get('price', 0),
                  'sh_chg': idx.get('sh000001', {}).get('chg', 0)},
        'calendar': cal,
        'pool_count': len(pool),
        'pool': [{
            'code': x['code'][2:],
            'mkt': x['code'][:2],
            'name': x['name'],
            'p': x['price'],                 # 最新收盘 / 现价
            'chg': x['chg'],                 # 当日涨跌 %
            'ret60': x['ret60'],
            'r5': x['r5'],
            'dd60': x['dd60'],
            'amt20': x['amt20'],             # 亿元
            'rank': x['rank'],
            'five': x['five_star'],
            'sector': x['sector'],
            'pe': round(x.get('pe', 0) or 0, 1),
            'fmv': round(x.get('fmv', 0) or 0, 1),   # 流通市值 亿
        } for x in topn],
    }
    if out_json:
        json.dump(data, open(out_json, 'w'), ensure_ascii=False, indent=1)
    return data


def print_v51(res, top=12):
    print('\n[v5.1 每日选股榜 · ret60 升序] 主板 + 20~40元 + 20日均额≥5000万')
    h = '%-3s %-9s %-8s %7s %7s %9s %9s %8s %-8s %s'
    print(h % ('#', '代码', '名称', '收盘', '今日%', 'ret60%', '20日均额', '距60高%', '板块', '星级'))
    for x in res[:top]:
        print(h % (x['rank'], x['code'], x['name'][:6], '%.2f' % x['price'], '%+.2f' % x['chg'],
                   '%.2f' % x['ret60'], '%.2f亿' % x['amt20'], '%.1f' % x['dd60'],
                   x['sector'], '★★★★★' if x['five_star'] else ''))

# ---------------- main ----------------
if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--top', type=int, default=30)
    ap.add_argument('--workers', type=int, default=20)
    ap.add_argument('--reuse', action='store_true', help='复用已有 kl_analyzed.json，跳过Stage1/2')
    ap.add_argument('--v51', action='store_true', help='★ v5.1 每日选股榜（网站数据层）')
    ap.add_argument('--out', default=None, help='v5.1 输出 json 路径')
    a = ap.parse_args()

    if a.v51:
        # ---------- v5.1 分支（网站数据层，默认走这里）----------
        rows = stage1()
        pool = stage_v51(rows, workers=a.workers or 24, top=a.top)
        print_v51(pool, top=a.top or 12)
        out = a.out or (OUT + '/v51_pool.json')
        d = build_site_data(pool, top=a.top or 12, out_json=out)
        print('\n[v5.1] 榜单已落盘 → %s' % out)
        print('[v5.1] 大盘：上证 %.2f (%+.2f%%) ｜ 最后交易日 %s ｜ 交易日历 %d 天'
              % (d['index']['sh'], d['index']['sh_chg'], d['last_trade_day'], len(d['calendar'])))
        print('\n[排期提醒] v5.1 为**非重叠采样**：每 12 个交易日一批信号，'
              '一年约 20 批 —— 不是每天出信号。')
        sys.exit(0)

    # ---------- v4 遗留分支（仅对照，不作买入依据）----------
    if a.reuse and os.path.exists(OUT + '/kl_analyzed.json'):
        res = json.load(open(OUT + '/kl_analyzed.json'))
        print('[reuse] 载入已有解析结果 %d 只' % len(res))
    else:
        res = stage2(stage1(), a.workers)
    stage3(res, a.top)
    stage4_flex(res, a.top)   # v3.17：无论强弱市都跑，按环境自动切换权重
