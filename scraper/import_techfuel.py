"""
用 TechFuelHQ GPU Price Index（CC BY 4.0，https://techfuelhq.com/data/gpu-price-index/）
补美国显卡在 "PCPartPicker 历史结束（9 月上旬）" 和 "Pricify 周数据开始（10 月中）" 之间的空档。

TechFuelHQ 记录 8 款显卡在 Newegg / Best Buy 的合格报价（全新、有货、非套装），从 2026-09-14 起。
每天取当日各条报价的中位数，作为单独的来源点；只写进上述空档，不和其他来源交错。
"""
import csv
import io
import os
import statistics
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_prices as fp  # noqa: E402

URL = 'https://techfuelhq.com/data/gpu-price-observations.csv'
LINK = 'https://techfuelhq.com/data/gpu-price-index/'
SOURCE = 'TechFuelHQ 报价（CC BY 4.0）'
LOCAL = os.path.join(fp.ROOT, 'data', 'techfuel-observations.csv')
MAP = {
    'geforce-rtx-5060-ti-16gb': '5060ti', 'geforce-rtx-5070-12gb': '5070', 'geforce-rtx-5070-ti-16gb': '5070ti',
    'geforce-rtx-5080-16gb': '5080', 'geforce-rtx-5090-32gb': '5090', 'radeon-rx-9070-xt-16gb': '9070xt',
    'radeon-rx-9070-16gb': '9070', 'radeon-rx-9060-xt-16gb': '9060xt',
}


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    try:
        req = urllib.request.Request(URL, headers={'User-Agent': fp.UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        with open(LOCAL, 'wb') as f:
            f.write(data)
    except Exception as e:
        fp.log(f'[TechFuel] 下载失败，用本地副本：{e}')
    if not os.path.exists(LOCAL):
        return 0
    with open(LOCAL, encoding='utf-8') as f:
        rows = list(csv.DictReader(io.StringIO(f.read())))
    by = {}
    for r in rows:
        key = MAP.get(r['product_id'])
        if key and r.get('price_usd'):
            by.setdefault(key, {}).setdefault(r['capture_date_utc'], []).append(float(r['price_usd']))

    history = fp.load_history()
    ents = history['entities']
    total = 0
    for key, days in by.items():
        e = ents.get(f'us-{key}')
        if not e:
            continue
        segs = e.get('segments', [])
        pcpp_end = max((s['to'] for s in segs if s['source'].startswith('PCPartPicker')), default='')
        others = [d for d, m in (e.get('points_meta') or {}).items() if m.get('source') != SOURCE]
        own = [b[0] for b in e.get('bars', []) if b[0] > pcpp_end and b[0] not in (e.get('points_meta') or {})]
        stop = min(others + own) if (others or own) else '9999-12-31'
        pts = {d: (round(statistics.median(v)), f'TechFuelHQ 当日 {len(v)} 条合格报价中位数（Newegg / Best Buy）', LINK)
               for d, v in days.items() if pcpp_end < d < stop}
        total += fp.set_point_source(e, SOURCE, pts)
    history.setdefault('attribution', {})['techfuel'] = 'TechFuelHQ GPU Price Index, CC BY 4.0'
    fp.save_history(history, quiet=True)
    fp.log(f'[TechFuel] 写入 {total} 个点（补美国显卡 9 月中到 10 月中的空档）')
    return total


if __name__ == '__main__':
    fp.run_locked(main)
