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
    # 后续世代（只收单一规格的型号；8G / 16G 混合的大盘不标首发价）
    '4060': ('2023-06-29', 299), '4070': ('2023-04-13', 599), '4070s': ('2024-01-17', 599),
    '4070ti': ('2023-01-05', 799), '4070tis': ('2024-01-24', 799), '4080': ('2022-11-16', 1199),
    '4080s': ('2024-01-31', 999), '4090': ('2022-10-12', 1599),
    '5060': ('2025-05-19', 299), '5070': ('2025-03-05', 549), '5070ti': ('2025-02-20', 749),
    '5080': ('2025-01-30', 999), '5090': ('2025-01-30', 1999),
    '6650xt': ('2022-05-10', 399), '6750xt': ('2022-05-10', 549), '6950xt': ('2022-05-10', 1099),
    '7600': ('2023-05-25', 269), '7600xt': ('2024-01-24', 329), '7700xt': ('2023-09-06', 449),
    '7800xt': ('2023-09-06', 499), '7900gre': ('2024-02-27', 549), '7900xt': ('2022-12-13', 899),
    '7900xtx': ('2022-12-13', 999), '9070': ('2025-03-06', 549), '9070xt': ('2025-03-06', 599),
    'a750': ('2022-10-12', 289), 'a770': ('2022-10-12', 349), 'a580': ('2023-10-10', 179),
    'b580': ('2024-12-13', 249), 'b570': ('2025-01-16', 219),
}
US_MSRP_URL = 'https://en.wikipedia.org/wiki/GeForce_RTX_30_series'
AMD_MSRP_URL = 'https://en.wikipedia.org/wiki/Radeon_RX_6000_series'


def msrp_url(m):
    if m.startswith(('a', 'b')):
        return 'https://en.wikipedia.org/wiki/Intel_Arc'
    if m.startswith('6'):
        return AMD_MSRP_URL
    if m.startswith('7'):
        return 'https://en.wikipedia.org/wiki/Radeon_RX_7000_series'
    if m.startswith('9'):
        return 'https://en.wikipedia.org/wiki/Radeon_RX_9000_series'
    return f'https://en.wikipedia.org/wiki/GeForce_RTX_{m[0]}0_series'


# 首发日（各地区通用）：没有统一首发价的混合规格型号、CPU 系列只用来确定从哪个月开始补
LAUNCH = {k: v[0] for k, v in US_MSRP.items()}
LAUNCH.update({'4060ti': '2023-05-24', '5060ti': '2025-04-16', '9060xt': '2025-06-05', '5050': '2025-07-15',
               '3050': '2022-01-27', 'r5z5': '2024-08-08', 'r7z5': '2024-08-08', 'r9z5': '2024-08-15',
               'u5al': '2024-10-24', 'u7al': '2024-10-24', 'u9al': '2024-10-24'})

# 已核实的国行首发价（其余型号的国行首发价没找到可靠出处，不标）
CN_MSRP = {
    '4090': ('2022-10-12', 12999, 'https://news.mydrivers.com/1/863/863542_3.htm'),
    '4080': ('2022-11-16', 9499, 'https://news.mydrivers.com/1/863/863542_3.htm'),
    '4060': ('2023-06-29', 2399, 'https://www.bilibili.com/opus/892962364139241491'),
    '7900gre': ('2023-07-28', 5299, 'https://wccftech.com/amd-radeon-rx-7900-gre-16-gb-gpu-launch-china-exclusive-cut-down-navi-31-gpu/'),
    '5090d': ('2025-01-30', 16499, 'https://www.tomshardware.com/pc-components/gpus/msi-and-colorful-raise-nvidia-rtx-50-series-prices-in-china-by-up-to-59-percent-across-the-entire-lineup-change-in-distributer-pricing-suggests-gpu-price-hikes-are-on-the-way'),
    '5080': ('2025-01-30', 8299, 'https://www.tomshardware.com/pc-components/gpus/msi-and-colorful-raise-nvidia-rtx-50-series-prices-in-china-by-up-to-59-percent-across-the-entire-lineup-change-in-distributer-pricing-suggests-gpu-price-hikes-are-on-the-way'),
}

# 内存 / 固态的额外参照：国内渠道价、DRAMeXchange 现货价（走势相近，价位不同，用比例换算）
EXTRA_PEERS = {
    # 现货颗粒价和零售价的比例在 2026 年涨价潮里变化太大，不当参照
    'ddr4': ['ddr4ch'], 'ddr5': ['ddr5ch'], 'ssd2t': ['ssd2tch'],
}
REGION_LABEL = {'CN': '国内', 'US': '美国', 'CA': '加拿大', 'GLOBAL': '全球现货'}

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


# ---------------------------------------------------------------------------
# 第二步：所有大盘、所有空档（首发前、中间断档、停更后）按月补估算区间
# ---------------------------------------------------------------------------
NEAR = 16          # 某月 15 日前后 16 天内有实测，就算这个月有数据
MAX_ONE_SIDE = 24  # 只有一头有比例 / 数据时，最多往外推几个月
ACTUAL_ERR = 0.05  # 实测月中位数本身的误差
# 第二步的区间按倍数算：下沿 = 中值 ÷ (1+e)，上沿 = 中值 × (1+e)
MAX_FACTOR = 1.0   # 参照推算最宽到 2 倍（价格剧烈波动、只有一头比例时）
MAX_INTERP = 0.6   # 纯插值最宽到 1.6 倍


def ym(t):
    return t.year * 12 + t.month - 1


def ym_date(k):
    return date(k // 12, k % 12 + 1, 15)


def region_of(e):
    return e.get('region') or ('GLOBAL' if 'DRAM' in str(e.get('group')) else 'CN')


def monthly(bars):
    """{月序号: 当月 15 日前后 NEAR 天内收盘价的中位数}"""
    out = {}
    pts = [(d(b[0]), b[4]) for b in bars]
    if not pts:
        return out
    for k in range(ym(pts[0][0]) - 1, ym(pts[-1][0]) + 2):
        t = ym_date(k)
        vals = sorted(v for (dd, v) in pts if abs((dd - t).days) <= NEAR)
        if vals:
            out[k] = vals[len(vals) // 2]
    return out


def fill_gaps(E, out, sid):
    idx = {k: e for k, e in E.items() if e.get('kind') == 'index' and e.get('bars')}
    latest = max(d(e['bars'][-1][0]) for e in idx.values())
    last_k = ym(latest) if latest.day >= 15 else ym(latest) - 1
    act = {k: monthly(e['bars']) for k, e in idx.items()}
    by_cmp = {}
    for k, e in idx.items():
        if e.get('compare'):
            by_cmp.setdefault(e['compare'], []).append(k)

    def known(k):
        """目标自己的已知值：实测月中位数、第一步的估算、首发价。{月: (值, 误差, 类型)}"""
        res = {m: (v, ACTUAL_ERR, 'act') for m, v in act[k].items()}
        o = out.get(k, {})
        for b in o.get('bands', []):
            m = ym(d(b['d']))
            if m not in res:
                res[m] = (math.sqrt(b['lo'] * b['hi']), (b['hi'] - b['lo']) / (b['hi'] + b['lo']), 'est')
        ms = o.get('msrp')
        if ms:
            m = ym(d(ms['d']))
            if m not in res:
                res[m] = (ms['p'], 0.12, 'msrp')
        return res

    added = 0
    for k, e in idx.items():
        reg = region_of(e)
        if reg == 'GLOBAL':
            continue
        base = k.split('-', 1)[1] if k.startswith(('us-', 'ca-')) else k
        base = {'5090d': '5090'}.get(base, base)
        cur = e.get('currency', 'CNY')
        K = known(k)
        first_act = min(act[k]) if act[k] else None
        launch = LAUNCH.get(base)
        start = ym(d(launch)) if launch else (first_act - 12 if first_act is not None else None)
        if start is None:
            continue
        if base in ('ddr4', 'ddr5', 'ssd2t'):
            start = first_act - 12  # 内存 / 固态没有“首发”，最多往前补一年
        peers = [p for p in by_cmp.get(e.get('compare'), []) if p != k and region_of(idx[p]) != 'GLOBAL']
        peers += [p for p in EXTRA_PEERS.get(base, []) if p in idx]
        if reg == 'CA':  # 加拿大优先参照美国
            peers.sort(key=lambda p: 0 if p.startswith('us-') else 1)

        last_known = max(K) if K else None
        xb = {}  # 第一遍：参照推算
        for m in range(start, last_k + 1):
            if m in K:
                continue
            tail = last_known is not None and m > last_known
            cands = []
            for p in peers:
                pv = act[p].get(m)
                if pv is None:
                    continue
                # 比例锚点：目标已知、参照实测的月份
                ratios = [(mm, K[mm][0] / act[p][mm], K[mm][1]) for mm in K if mm in act[p]]
                # 双方都有首发价时，首发价之比也算一个比例点
                pm = out.get(p, {}).get('msrp')
                km = out.get(k, {}).get('msrp')
                if pm and km:
                    ratios.append((ym(d(km['d'])), km['p'] / pm['p'], 0.12))
                before = [r for r in ratios if r[0] < m]
                after = [r for r in ratios if r[0] > m]
                rb = max(before) if before else None
                ra = min(after) if after else None
                if rb and ra:
                    f = (m - rb[0]) / (ra[0] - rb[0])
                    ratio = math.exp(math.log(rb[1]) * (1 - f) + math.log(ra[1]) * f)
                    dist = min(m - rb[0], ra[0] - m)
                    # 两头比例差得越多，中间越不确定
                    err = max(rb[2], ra[2]) + 0.05 + 0.02 * dist + 0.25 * abs(math.log(rb[1] / ra[1]))
                elif rb or ra:
                    r = rb or ra
                    dist = abs(m - r[0])
                    if dist > (12 if tail else MAX_ONE_SIDE):
                        continue
                    ratio = r[1]
                    # 只有一头：参照价格从比例点到这个月变动越大，固定比例越靠不住
                    p0 = act[p].get(r[0])
                    drift = abs(math.log(pv / p0)) if p0 else 0.3
                    err = r[2] + 0.10 + 0.025 * dist + 0.5 * drift
                else:
                    continue
                cands.append((pv * ratio, min(MAX_FACTOR, err), p))
            if cands:
                w = [1 / (c[1] ** 2) for c in cands]
                mid = math.exp(sum(wi * math.log(c[0]) for wi, c in zip(w, cands)) / sum(w))
                spread = max(abs(math.log(c[0] / mid)) for c in cands)
                err = min(MAX_FACTOR, max(min(c[1] for c in cands), spread))
                srcs = []
                for c in sorted(cands, key=lambda c: c[1])[:2]:
                    pe = idx[c[2]]
                    srcs.append(sid(pe.get('url') or '', f"按{REGION_LABEL[region_of(pe)]}{pe.get('name')}同期价格 × 价差比例推算"))
                xb[m] = (mid, err, srcs)
        # 第二遍：没有参照的月份，在前后已知值（含第一遍结果）之间插值；首尾不外推
        K2 = dict(K)
        K2.update({m: (v[0], v[1], 'x') for m, v in xb.items()})
        bands = []
        for m in range(start, last_k + 1):
            if m in K:
                continue
            if m in xb:
                mid, err, srcs = xb[m]
                bands.append({'d': ym_date(m).isoformat(), 'lo': mid / (1 + err), 'hi': mid * (1 + err), 'k': 'x', 'src': srcs})
                continue
            before = [mm for mm in K2 if mm < m]
            after = [mm for mm in K2 if mm > m]
            if before and after:
                mb, ma = max(before), min(after)
                f = (m - mb) / (ma - mb)
                mid = math.exp(math.log(K2[mb][0]) * (1 - f) + math.log(K2[ma][0]) * f)
                dist = min(m - mb, ma - m)
                err = min(MAX_INTERP, max(K2[mb][1], K2[ma][1]) + 0.06 + 0.04 * dist)
                bands.append({'d': ym_date(m).isoformat(), 'lo': mid / (1 + err), 'hi': mid * (1 + err), 'k': 'i',
                              'src': [sid(e.get('url') or '', '前后两个已知价格之间插值')]})
        if not bands:
            continue
        for b in bands:
            b['lo'], b['hi'] = rnd(b['lo'], cur), rnd(b['hi'], cur)
        entry = out.setdefault(k, {'bands': []})
        entry['bands'] = sorted(entry['bands'] + bands, key=lambda b: b['d'])
        added += len(bands)
    print(f'[估算] 第二步：补了 {added} 个月度区间')


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
        msrp_src = sid(msrp_url(m), '官方首发价')
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
    for m, (day, price, url) in CN_MSRP.items():
        if m in first_bar:
            out.setdefault(m, {'bands': []})['msrp'] = {'d': day, 'p': price, 'src': sid(url, '国行首发价')}

    fill_gaps(E, out, sid)

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
