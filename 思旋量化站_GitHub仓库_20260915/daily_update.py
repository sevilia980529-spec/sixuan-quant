#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日收盘后一键更新（给 GitHub Actions 用）。

  1. 交易日自检：非交易日（周末 / 法定节假日 / 临时休市）立即退出，不写任何文件
  2. 刷新当日榜单（refresh_pool.py）
  3. 重算历史榜单与每批战绩（history_v51.py，带日K 缓存）
  4. 重建站点（build_site.py）
  5. 更新云端日历存档（archive_day.py）

非交易日 exit 0（不产生提交，Actions 就是绿的）。
"""
import io, json, os, sys, time, subprocess, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
UA = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
      'Referer': 'https://gu.qq.com/'}
os.environ.setdefault('TZ', 'Asia/Shanghai')


def shanghai_today():
    try:
        time.tzset()
    except Exception:
        pass
    return time.strftime('%Y-%m-%d')


def market_day():
    """上证指数快照里的时间戳日期 = 市场最新交易日"""
    req = urllib.request.Request('https://qt.gtimg.cn/q=sh000001', headers=UA)
    raw = urllib.request.urlopen(req, timeout=20).read().decode('gbk', 'ignore')
    f = raw.split('"')[1].split('~')
    ts = f[30] if len(f) > 30 else ''
    return (ts[:4] + '-' + ts[4:6] + '-' + ts[6:8]) if len(ts) >= 8 else ''


def run(name):
    print('\n── %s ──' % name, flush=True)
    r = subprocess.run([sys.executable, os.path.join(BASE, name)], cwd=BASE)
    if r.returncode != 0:
        print('⚠ %s 退出码 %d（继续，不中断）' % (name, r.returncode))
    return r.returncode


def main():
    today = shanghai_today()
    try:
        md = market_day()
    except Exception as e:
        print('交易日自检失败：%s —— 按非交易日处理' % e)
        md = ''
    print('今天(上海) = %s ｜ 市场最新交易日 = %s' % (today, md or '取不到'))
    if md != today:
        print('⏸ 今天不是 A 股交易日（周末 / 节假日 / 休市）—— 不更新任何数据，正常结束。')
        return 0

    # 股票池超过 30 天没更新就重生成一次（新股上市）
    univ = os.path.join(BASE, 'data', 'universe.txt')
    if not os.path.exists(univ) or (time.time() - os.path.getmtime(univ) > 30 * 86400):
        run('gen_universe.py')

    run('refresh_pool.py')
    run('history_v51.py')
    run('build_site.py')
    run('archive_day.py')

    dj = os.path.join(BASE, 'docs', 'data.json')
    if os.path.exists(dj):
        d = json.load(io.open(dj, encoding='utf-8'))
        print('\n✅ 本次更新到 %s 收盘 · 合格 %s 只' % (d.get('last_trade_day'), d.get('pool_count')))
        for x in (d.get('pool') or [])[:3]:
            print('   %d. %s %s %s ret60=%s' % (x['rank'], x['name'], x['code'], x['p'], x['ret60']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
