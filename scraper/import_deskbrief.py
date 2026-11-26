"""
用 The Desk Brief「Canadian Tech Price Index」补加拿大显卡 / 固态在 PCPartPicker 历史结束之后的空档。

数据：Best Buy Canada、Canada Computers、Newegg Canada、Memory Express 四家的全新有货报价，
每天每个配置取各家最低价，再取这几家最低价的中位数（median_retailer_low_cad）。
发布方许可："Free to cite with a link to the relevant tracker or index page"（页面页脚注明出处并链接）。
原文件已从网站下线，这里用互联网档案馆 2026-09-22 的存档（覆盖 2026-07-24 → 2026-09-21），
下载一次后缓存到 data/deskbrief/。
"""
import csv
import gzip
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_prices as fp  # noqa: E402

SOURCE = 'The Desk Brief 加拿大价格指数'
LINK = 'https://thedeskbrief.com/canadian-tech-price-index/'
BASE = 'https://thedeskbrief.com/wp-content/uploads/tdb-data/'
DIR = os.path.join(fp.ROOT, 'data', 'deskbrief')
FILES = {
    'gpu': ('canadian-gpu-price-history.csv', 'https://thedeskbrief.com/gpu-price-tracker/'),
    'ssd': ('canadian-ssd-price-history.csv', 'https://thedeskbrief.com/ssd-price-tracker/'),
    'ram': ('canadian-ram-price-history.csv', 'https://thedeskbrief.com/ram-price-tracker/'),
}
MAP = {
    'rtx-5060-8gb': 'ca-5060', 'rtx-5060-ti-16gb': 'ca-5060ti', 'rtx-5070-12gb': 'ca-5070',
    'rtx-5070-ti-16gb': 'ca-5070ti', 'rtx-5080-16gb': 'ca-5080', 'rtx-5090-32gb': 'ca-5090',
    'rx-9070-16gb': 'ca-9070', 'rx-9070-xt-16gb': 'ca-9070xt',
    'nvme-gen4-2tb': 'ca-ssd2t', 'ddr5-6000-32gb': 'ca-ddr5', 'ddr4-3200-32gb': 'ca-ddr4',
}


def _get(url):
    req = urllib.request.Request(url, headers={'User-Agent': fp.UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        b = r.read()
    return gzip.decompress(b) if b[:2] == b'\x1f\x8b' else b


def ensure_files():
    os.makedirs(DIR, exist_ok=True)
    for name, _ in FILES.values():
        path = os.path.join(DIR, name)
        if os.path.exists(path):
            continue
        for url in (BASE + name, f'https://web.archive.org/web/2026id_/{BASE}{name}'):
            try:
                data = _get(url)
                if data.lstrip().startswith(b'observation_date'):
                    with open(path, 'wb') as f:
                        f.write(data)
                    break
            except Exception as e:
                fp.log(f'[DeskBrief] {url} 下载失败：{e}')


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ensure_files()
    by = {}
    tracker = {}
    for cat, (name, turl) in FILES.items():
        path = os.path.join(DIR, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                key = MAP.get(r['configuration'])
                v = r.get('median_retailer_low_cad') or r.get('lowest_price_cad')
                if key and v:
                    by.setdefault(key, {})[r['observation_date']] = (float(v), r)
                    tracker[key] = turl

    history = fp.load_history()
    ents = history['entities']
    total = 0
    for key, days in by.items():
        e = ents.get(key)
        if not e:
            continue
        segs = e.get('segments', [])
        pcpp_end = max((s['to'] for s in segs if s['source'].startswith('PCPartPicker')), default='')
        meta = e.get('points_meta') or {}
        others = [d for d, m in meta.items() if m.get('source') != SOURCE]
        own = [b[0] for b in e.get('bars', []) if b[0] > pcpp_end and b[0] not in meta]
        stop = min(others + own) if (others or own) else '9999-12-31'
        pts = {}
        for d, (v, r) in days.items():
            if pcpp_end < d < stop:
                pts[d] = (round(v), f"Desk Brief：{r['reporting_retailer_count']} 家加拿大零售商当日最低价的中位数"
                                    f"（全场最低 C${r['lowest_price_cad']}，{r['lowest_price_retailer']}）", tracker[key])
        total += fp.set_point_source(e, SOURCE, pts)
    history.setdefault('attribution', {})['deskbrief'] = f'The Desk Brief, Canadian Tech Price Index ({LINK})'
    fp.save_history(history, quiet=True)
    fp.log(f'[DeskBrief] 写入 {total} 个点（补加拿大 2026-07-24 → 09-21 的空档）')
    return total


if __name__ == '__main__':
    fp.run_locked(main)
