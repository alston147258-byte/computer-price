"""
生成 data/estimates.js：没有实测数据的早期时段（30 系 / RX 6000 首发到本站数据开始之前）的按月估算区间。

不确定的就标成区间，例如 ¥3000 ± 500。网页上画成灰色带，不进 K 线、不算涨跌。

锚点来源（都在 data/estimates/ 下，手工整理）：
  - tomshardware_ebay_us.json        Tom's Hardware「eBay Historical GPU Prices」每期表格的 eBay 平均成交价（美国）
  - tomshardware_ebay_us_chart.json  同系列 2020-12 至 2021-03 一期的散点图中位线，人工目测读数（更粗）
  - cn_anchors.json                  国内新闻 / 知乎每日显卡行情里的京东或渠道报价，以及国行首发价

算法：
  1. 每个型号、每个地区，从首发月到本站实测数据开始前一个月，每月取一个点（15 日；首发月取首发日之后）。
  2. 该月附近（±12 天）有锚点：区间 = 锚点价格 ± 锚点自身的误差（见 ANCHOR_ERR）。
  3. 没有锚点：在前后两个锚点之间按对数线性插值，误差随离锚点的月数加宽，最多 ±35%。
     最后一个锚点之后不外推（宁可留空，也不编）。
  4. 加拿大：没有找到早期加元数据，用美国估算 × 当年美元兑加元年均汇率，误差再加 10 个百分点。

运行：python scraper/build_estimates.py   （只在改了锚点后手动跑一次）
"""
import json
import math
import os
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, 'data', 'estimates')
HISTORY = os.path.join(ROOT, 'data', 'history.json')
OUT = os.path.join(ROOT, 'data', 'estimates.js')

TH_URL = 'https://www.tomshardware.com/news/gpus-historical-ebay-pricing'

# 美国首发价（美元）与首发日：NVIDIA / AMD 官方公布
US_MSRP = {
    '3050': ('2022-01-27', 249), '3060': ('2021-02-25', 329), '3060ti': ('2020-12-02', 399),
    '3070': ('2020-10-29', 499), '3070ti': ('2021-06-10', 599), '3080': ('2020-09-17', 699),
    '3080ti': ('2021-06-03', 1199), '3090': ('2020-09-24', 1499), '3090ti': ('2022-03-29', 1999),
    '6600': ('2021-10-13', 329), '6600xt': ('2021-08-11', 379), '6700xt': ('2021-03-18', 479),
    '6800': ('2020-11-18', 579), '6800xt': ('2020-11-18', 649), '6900xt': ('2020-12-08', 999),
}
US_MSRP_URL = 'https://en.wikipedia.org/wiki/GeForce_RTX_30_series'
AMD_MSRP_URL = 'https://en.wikipedia.org/wiki/Radeon_RX_6000_series'

# 美元兑加元年均汇率（加拿大央行年均值）
USDCAD = {2020: 1.3415, 2021: 1.2535, 2022: 1.3013}

# 锚点自身的误差（相对值）
ANCHOR_ERR = {
    'th': 0.15,        # eBay 均价：新卡二手混合，零售店偶尔能按首发价抢到
    'th_chart': 0.20,  # 目测读图
    'cn': 0.10,        # 报道里的京东 / 渠道价：不同品牌差几百元
}
MAX_ERR = 0.35
WIDEN_PER_MONTH = 0.05
NEAR_DAYS = 12


def d(s):
    y, m, dd = map(int, s.split('-'))
    return date(y, m, dd)


def months(start, end):
    """start 所在月到 end（不含）之前的每月 15 日；首发月若晚于 15 日则取首发日后 3 天。"""
    out = []
    y, m = start.year, start.month
    while True:
        t = date(y, m, 15)
        if y == start.year and m == start.month and start > date(y, m, 12):
            t = start + timedelta(days=3)
        if t >= end:
            break
        out.append(t)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def estimate(anchors, start, end):
    """anchors: [(date, lo, hi, err, src)]，按日期排序。返回每月区间列表。"""
    if not anchors:
        return []
    pts = []
    for (t, lo, hi, err, src) in anchors:
        mid = math.sqrt(lo * hi)
        r = max(err, (hi - lo) / 2 / mid + err / 2)
        pts.append((t, mid, r, src))
    first, last_t = pts[0][0], pts[-1][0]
    res = []
    for t in months(start, end):
        if t < first - timedelta(days=NEAR_DAYS) or t > last_t + timedelta(days=NEAR_DAYS):
            continue  # 第一个锚点之前 / 最后一个之后不外推
        near = [p for p in pts if abs((p[0] - t).days) <= NEAR_DAYS]
        if near:
            lo = min(p[1] * (1 - p[2]) for p in near)
            hi = max(p[1] * (1 + p[2]) for p in near)
            srcs = []
            for p in near:
                if p[3] not in srcs:
                    srcs.append(p[3])
            res.append({'d': t.isoformat(), 'lo': lo, 'hi': hi, 'k': 'a', 'src': srcs})
            continue
        before = [p for p in pts if p[0] < t][-1]
        after = [p for p in pts if p[0] > t][0]
        f = (t - before[0]).days / (after[0] - before[0]).days
        mid = math.exp(math.log(before[1]) * (1 - f) + math.log(after[1]) * f)
        gap = min((t - before[0]).days, (after[0] - t).days) / 30.4
        r = min(MAX_ERR, max(before[2], after[2]) + WIDEN_PER_MONTH * max(1, gap))
        res.append({'d': t.isoformat(), 'lo': mid * (1 - r), 'hi': mid * (1 + r), 'k': 'i',
                    'src': [before[3], after[3]] if before[3] != after[3] else [before[3]]})
    return res


def rnd(v, cur):
    step = 10 if cur in ('USD', 'CAD') else 50
    return int(round(v / step) * step)


def main():
    with open(HISTORY, encoding='utf-8') as f:
        E = json.load(f)['entities']
    first_bar = {k: d(e['bars'][0][0]) for k, e in E.items() if e.get('kind') == 'index' and e.get('bars')}

    with open(os.path.join(DIR, 'tomshardware_ebay_us.json'), encoding='utf-8') as f:
        th = json.load(f)
    with open(os.path.join(DIR, 'tomshardware_ebay_us_chart.json'), encoding='utf-8') as f:
        thc = json.load(f)
    with open(os.path.join(DIR, 'cn_anchors.json'), encoding='utf-8') as f:
        cn = json.load(f)

    sources = []  # 全部出处，网页按序号引用

    def sid(url, label):
        for i, s in enumerate(sources):
            if s['url'] == url and s['label'] == label:
                return i
        sources.append({'url': url, 'label': label})
        return len(sources) - 1

    out = {}

    # ---------- 美国 ----------
    us_anchor = {}
    for day, p in thc['points'].items():
        s = sid(thc['url'], "Tom's Hardware eBay 成交价图（目测读数）")
        for m, v in p.items():
            us_anchor.setdefault(m, []).append((d(day), v, v, ANCHOR_ERR['th_chart'], s))
    for day, p in th['points'].items():
        s = sid(f"{TH_URL}/{p['page']}", "Tom's Hardware eBay 月均成交价")
        for m, v in p['m'].items():
            us_anchor.setdefault(m, []).append((d(day), v, v, ANCHOR_ERR['th'], s))
    for m, (launch, msrp) in US_MSRP.items():
        anchors = sorted(us_anchor.get(m, []))
        # 同一天表格和读图都有时只留表格
        dedup = {}
        for a in anchors:
            if a[0] not in dedup or a[3] < dedup[a[0]][3]:
                dedup[a[0]] = a
        anchors = sorted(dedup.values())
        msrp_src = sid(AMD_MSRP_URL if m.startswith('6') else US_MSRP_URL, '官方首发价')
        for region, cur in (('US', 'USD'), ('CA', 'CAD')):
            eid = f"{region.lower()}-{m}"
            if eid not in first_bar:
                continue
            bands = estimate(anchors, d(launch), first_bar[eid])
            if region == 'CA':
                for b in bands:
                    rate = USDCAD[int(b['d'][:4])]
                    mid = math.sqrt(b['lo'] * b['hi']) * rate
                    r = (b['hi'] - b['lo']) / (b['hi'] + b['lo']) + 0.10
                    b['lo'], b['hi'] = mid * (1 - r), mid * (1 + r)
                    b['k'] = 'c'
            for b in bands:
                b['lo'], b['hi'] = rnd(b['lo'], cur), rnd(b['hi'], cur)
            entry = {'bands': bands}
            if region == 'US':
                entry['msrp'] = {'d': launch, 'p': msrp, 'src': msrp_src}
            if bands or region == 'US':
                out[eid] = entry

    # ---------- 国内 ----------
    cn_anchor = {}
    for a in cn['anchors']:
        s = sid(a['url'], a['note'])
        cn_anchor.setdefault(a['m'], []).append((d(a['day']), a['lo'], a['hi'], ANCHOR_ERR['cn'], s))
    for m, ms in cn['msrp'].items():
        if m not in first_bar:
            continue
        bands = estimate(sorted(cn_anchor.get(m, [])), d(ms['day']), first_bar[m])
        for b in bands:
            b['lo'], b['hi'] = rnd(b['lo'], 'CNY'), rnd(b['hi'], 'CNY')
        out[m] = {'bands': bands, 'msrp': {'d': ms['day'], 'p': ms['price'], 'src': sid(ms['url'], '国行首发价')}}

    payload = {'sources': sources, 'entities': out}
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('// 由 scraper/build_estimates.py 生成：早期估算区间（非实测），说明见该脚本\n')
        f.write('window.PRICE_ESTIMATES = ')
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
        f.write(';\n')
    n = sum(len(v['bands']) for v in out.values())
    print(f'[估算] {len(out)} 个型号，{n} 个月度区间，{len(sources)} 个出处 → {OUT}')
    for k, v in out.items():
        b = v['bands']
        if b:
            print(f"  {k:10} {b[0]['d']} → {b[-1]['d']}  {len(b):3} 个月")


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    main()
