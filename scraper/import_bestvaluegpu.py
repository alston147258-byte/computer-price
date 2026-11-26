"""
用 BestValueGPU（bestvaluegpu.com）页面里的「月均新卡价」补美国 / 加拿大老型号的历史空档。

- 只读互联网档案馆里已有的存档（该站对程序访问返回 429，不直接访问它，也不请求档案馆新建存档）。
- 页面数据 last12MonthsChart：每月 Amazon 新卡均价（new）与 eBay 二手均价（used），只用 new。
- 每月记为当月 15 日的单点；剔除明显异常值（低于该型号序列中位数一半或高于两倍）。
- 只写进 PCPartPicker 历史结束之后、其他来源开始之前的空档，不与其他来源交错。
"""
import gzip
import json
import os
import re
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_prices as fp  # noqa: E402

SOURCE = 'BestValueGPU 月均价（Amazon 新卡）'
DIR = os.path.join(fp.ROOT, 'data', 'bestvaluegpu')
STATE = os.path.join(DIR, 'state.json')
REGIONS = {'': 'us', 'en-ca/': 'ca'}
SLUGS = {
    'rtx-3050': '3050', 'rtx-3060': '3060', 'rtx-3060-ti': '3060ti', 'rtx-3070': '3070', 'rtx-3070-ti': '3070ti',
    'rtx-3080': '3080', 'rtx-3080-ti': '3080ti', 'rtx-3090': '3090', 'rtx-3090-ti': '3090ti',
    'rtx-4060': '4060', 'rtx-4060-ti': '4060ti', 'rtx-4070': '4070', 'rtx-4070-super': '4070s',
    'rtx-4070-ti': '4070ti', 'rtx-4070-ti-super': '4070tis', 'rtx-4080': '4080', 'rtx-4080-super': '4080s',
    'rtx-4090': '4090', 'rx-6600': '6600', 'rx-6600-xt': '6600xt', 'rx-6650-xt': '6650xt', 'rx-6700-xt': '6700xt',
    'rx-6750-xt': '6750xt', 'rx-6800': '6800', 'rx-6800-xt': '6800xt', 'rx-6900-xt': '6900xt',
    'rx-6950-xt': '6950xt', 'rx-7600': '7600', 'rx-7600-xt': '7600xt', 'rx-7700-xt': '7700xt',
    'rx-7800-xt': '7800xt', 'rx-7900-gre': '7900gre', 'rx-7900-xt': '7900xt', 'rx-7900-xtx': '7900xtx',
    'a750': 'a750', 'a770': 'a770', 'b570': 'b570', 'b580': 'b580',
}
MONTHS = {m: i for i, m in enumerate(['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], 1)}


def _get(url):
    req = urllib.request.Request(url, headers={'User-Agent': fp.UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        b = r.read()
    return gzip.decompress(b) if b[:2] == b'\x1f\x8b' else b


def refresh(max_age_days=7):
    """查档案馆有没有更新的存档；有就下载并只保留价格相关字段。"""
    os.makedirs(DIR, exist_ok=True)
    try:
        state = json.load(open(STATE, encoding='utf-8'))
    except Exception:
        state = {}
    if time.time() - state.get('checked', 0) < max_age_days * 86400:
        return 0
    got = 0
    for prefix, region in REGIONS.items():
        q = (f'https://web.archive.org/cdx/search/cdx?url=bestvaluegpu.com/{prefix}history/*&output=json'
             f'&fl=timestamp,original&filter=statuscode:200&from=2025&limit=20000')
        try:
            rows = json.loads(_get(q) or b'[]')
        except Exception as e:
            fp.log(f'[BestValueGPU] 档案馆查询失败：{e}')
            return got
        latest = {}
        for ts, o in rows[1:]:
            m = re.search(r'history/new-and-used-(.+?)-price-history', o)
            if m and m.group(1) in SLUGS and (m.group(1) not in latest or ts > latest[m.group(1)][0]):
                latest[m.group(1)] = (ts, o)
        for slug, (ts, o) in latest.items():
            path = os.path.join(DIR, f'{region}_{slug}.json')
            try:
                if json.load(open(path, encoding='utf-8')).get('snapshot', '') >= ts:
                    continue
            except Exception:
                pass
            try:
                h = _get(f'https://web.archive.org/web/{ts}id_/{o}').decode('utf-8', 'replace')
            except Exception as e:
                fp.log(f'[BestValueGPU] {slug} 存档下载失败：{e}')
                continue
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
            if not m:
                continue
            pp = json.loads(m.group(1)).get('props', {}).get('pageProps', {})
            keep = {k: pp.get(k) for k in ('region', 'currency', 'msrp', 'last12MonthsChart')}
            keep.update(snapshot=ts, url=o)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(keep, f)
            got += 1
            time.sleep(3)
        time.sleep(5)
    state['checked'] = time.time()
    with open(STATE, 'w', encoding='utf-8') as f:
        json.dump(state, f)
    return got


def load_points():
    """{entity_id: {day: (price, note, url)}}，同一型号多份存档合并（新存档优先）。"""
    out = {}
    files = sorted(f for f in os.listdir(DIR) if f.endswith('.json') and f != 'state.json') if os.path.isdir(DIR) else []
    for name in files:
        m = re.match(r'(us|ca)_(.+?)(?:_\d{14})?\.json$', name)
        if not m or m.group(2) not in SLUGS:
            continue
        region, slug = m.groups()
        d = json.load(open(os.path.join(DIR, name), encoding='utf-8'))
        ser = {}
        for c in d.get('last12MonthsChart') or []:
            if c.get('new') and c.get('month') in MONTHS:
                ser[f"{c['year']}-{MONTHS[c['month']]:02d}-15"] = float(c['new'])
        if len(ser) < 2:
            continue
        med = statistics.median(ser.values())
        snap = d.get('snapshot', '')
        page = 'https://bestvaluegpu.com/' + ('en-ca/' if region == 'ca' else '') + \
               f'history/new-and-used-{slug}-price-history-and-specs/'
        pts = out.setdefault(f'{region}-{SLUGS[slug]}', {})
        for day, v in ser.items():
            if not (0.5 * med <= v <= 2 * med):
                continue
            if day in pts and pts[day][3] > snap:
                continue
            pts[day] = (round(v), f'BestValueGPU：{day[:7]} Amazon 新卡月均价（存档 {snap[:8]}）', page, snap)
    return {k: {d: p[:3] for d, p in v.items()} for k, v in out.items()}


def main(do_refresh=True):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    if do_refresh:
        n = refresh()
        if n:
            fp.log(f'[BestValueGPU] 下载了 {n} 份新存档')
    allpts = load_points()
    history = fp.load_history()
    ents = history['entities']
    total = 0
    for key, days in allpts.items():
        e = ents.get(key)
        if not e:
            continue
        segs = e.get('segments', [])
        pcpp_end = max((s['to'] for s in segs if s['source'].startswith('PCPartPicker')), default='')
        meta = e.get('points_meta') or {}
        others = [d for d, m in meta.items() if m.get('source') != SOURCE]
        own = [b[0] for b in e.get('bars', []) if b[0] > pcpp_end and b[0] not in meta]
        stop = min(others + own) if (others or own) else '9999-12-31'
        # 和下一段数据至少隔开半个月，避免月均点紧贴着日数据
        pts = {d: p for d, p in days.items() if pcpp_end < d and (d < stop and (fp.dt.date.fromisoformat(stop) - fp.dt.date.fromisoformat(d)).days >= 10)}
        total += fp.set_point_source(e, SOURCE, pts)
    history.setdefault('attribution', {})['bestvaluegpu'] = 'BestValueGPU (bestvaluegpu.com), via Internet Archive snapshots'
    fp.save_history(history, quiet=True)
    fp.log(f'[BestValueGPU] 写入 {total} 个月度点（补美国 / 加拿大老型号空档）')
    return total


if __name__ == '__main__':
    fp.run_locked(main, do_refresh='--no-refresh' not in sys.argv)
