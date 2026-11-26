"""
国内渠道市场价（CFM 闪存市场 chinaflashmarket.com，robots.txt 允许抓取）。

CFM 每周发布深圳等国内渠道市场的内存条 / SSD 批发报价（美元）：周低点、周高点、本周价。
产品详情页公开最近半年的周报价；更早的从互联网档案馆的详情页快照里拼接（每个快照同样含半年）。

写成国内的单独大盘（"渠道价"），不和京东零售价混合：
  - DDR5 内存·渠道价 = DDR5 UDIMM 16GB 6000 × 2（套条）
  - DDR4 内存·渠道价 = DDR4 UDIMM 16GB 3200 × 2
  - 2TB 固态·渠道价 = SSD (PCIe 4.0) 2TB
周K线：高 / 低 = 周高点 / 周低点，收 = 本周价，开 = 上周本周价；美元按当周汇率折成人民币。

用法：python scraper/cfm_channel.py            # 只取当前页面（每天采集时运行）
      python scraper/cfm_channel.py --backfill # 另外从档案馆快照补更早的历史（一次性）
"""
import datetime as dt
import gzip
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_prices as fp  # noqa: E402

BASE = 'https://www.chinaflashmarket.com/price/ews/'
SOURCE = 'CFM 国内渠道周报价'
ITEMS = [
    {'cat': 'ddr5ch', 'id': 100269, 'mult': 2, 'group': '内存', 'name': 'DDR5 内存·渠道价', 'code': 'DDR5-CH',
     'spec': 'DDR5 UDIMM 16GB 6000 × 2（国内渠道市场批发价，CFM）'},
    {'cat': 'ddr4ch', 'id': 100224, 'mult': 2, 'group': '内存', 'name': 'DDR4 内存·渠道价', 'code': 'DDR4-CH',
     'spec': 'DDR4 UDIMM 16GB 3200 × 2（国内渠道市场批发价，CFM）'},
    {'cat': 'ssd2tch', 'id': 100245, 'mult': 1, 'group': '硬盘', 'name': '2TB 固态·渠道价', 'code': 'SSD-2T-CH',
     'spec': 'PCIe 4.0 NVMe 2TB（国内渠道市场批发价，CFM）'},
]
ROW = re.compile(r'<i>(\d{4}-\d{2}-\d{2})</i>\s*<b>([\d.]+)</b>\s*<strong>([\d.]+)</strong>\s*<span>([\d.]+)</span>')
CACHE = os.path.join(fp.ROOT, 'data', 'cfm_weekly.json')


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={'User-Agent': fp.UA, 'Accept': 'text/html'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = r.read()
    return (gzip.decompress(d) if d[:2] == b'\x1f\x8b' else d).decode('utf-8', 'replace')


def parse(html):
    return {m.group(1): (float(m.group(2)), float(m.group(3)), float(m.group(4))) for m in ROW.finditer(html)}


def load_cache():
    if os.path.exists(CACHE):
        with open(CACHE, encoding='utf-8') as f:
            return json.load(f)
    return {}


def usd_cny(fx, day):
    days = [d for d in fx if d <= day]
    if days:
        return fx[max(days)]['USD']
    return fx[min(fx)]['USD'] if fx else None


def run(backfill=False):
    cache = load_cache()  # {id: {date: [low, high, avg]}}
    for it in ITEMS:
        key = str(it['id'])
        rows = cache.setdefault(key, {})
        if backfill:
            try:
                cdx = json.loads(get(f'http://web.archive.org/cdx/search/cdx?url=chinaflashmarket.com/price/ews/{it["id"]}'
                                     f'&output=json&fl=timestamp,original&filter=statuscode:200', 120))[1:]
            except Exception as e:
                fp.log(f'[CFM] {it["name"]} 查询存档失败：{e}')
                cdx = []
            for ts, orig in cdx:
                try:
                    got = parse(get(f'http://web.archive.org/web/{ts}id_/{orig}', 120))
                    for d, v in got.items():
                        rows[d] = list(v)  # 较新的快照后处理，覆盖同一周
                    fp.log(f'[CFM] {it["name"]} 存档 {ts[:8]}：{len(got)} 周')
                except Exception as e:
                    fp.log(f'[CFM] {it["name"]} 存档 {ts[:8]} 读取失败：{e}')
                time.sleep(3)
        got = parse(get(BASE + key))  # 当前页面最后处理，以它为准
        for d, v in got.items():
            rows[d] = list(v)
        time.sleep(2)
    with open(CACHE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)

    history = fp.load_history()
    ents = history['entities']
    fx = history.get('fx', {})
    for it in ITEMS:
        rows = cache.get(str(it['id']), {})
        if not rows:
            continue
        e = ents.setdefault(it['cat'], {})
        e.update({'kind': 'index', 'region': 'CN', 'group': it['group'], 'name': it['name'], 'code': it['code'],
                  'spec': it['spec'], 'source': SOURCE, 'url': BASE + str(it['id']), 'currency': 'CNY',
                  'decimals': 0, 'index_mode': 'median'})
        bars, prev = [], None
        for d in sorted(rows):
            low, high, avg = rows[d]
            rate = usd_cny(fx, d)
            if not rate:
                continue
            k = it['mult'] * rate
            c, h, l = round(avg * k), round(high * k), round(low * k)
            o = prev if prev is not None else c
            bars.append([d, o, max(h, o, c), min(l, o, c), c])
            prev = c
        e['bars'] = bars
        if it['cat'] not in history.setdefault('order', []):
            history['order'].append(it['cat'])
        fp.log(f"[CFM] {it['name']}：{len(bars)} 周，{bars[0][0]} → {bars[-1][0]}，最新 ¥{bars[-1][4]}")
    fp.save_history(history, quiet=True)


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    fp.run_locked(run, backfill='--backfill' in sys.argv)
