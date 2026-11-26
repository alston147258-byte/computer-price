"""
导入 Pricify 每周美国零售价（CC BY 4.0，署名 "Pricify (pricify.ai)"）：
https://github.com/pricify-ai/pc-hardware-prices

Pricify 每周发布美国各零售商自营价格（不含第三方卖家）的汇总：显卡按型号、CPU 按型号、
内存 / 固态按容量档位。本脚本取每周的"典型价"（typical_price_end），作为一个周数据点
（日期取该周最后一天），写入美国的大盘：
  - 只写在 PCPartPicker 历史结束之后、每日现价开始之前（不和已有日线交错）；
  - 美国内存 / CPU / 固态没有每日现价来源，所以以后每周都会自动往后延伸。
已下载的周数据缓存在 data/pricify/，每天只下载新出的周。
"""
import json
import os
import re
import statistics
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_prices as fp  # noqa: E402

REPO_API = 'https://api.github.com/repos/pricify-ai/pc-hardware-prices/contents/data'
RAW = 'https://raw.githubusercontent.com/pricify-ai/pc-hardware-prices/main/data'
CACHE = os.path.join(fp.ROOT, 'data', 'pricify')
SITES = ['rtx.live', 'vga.live', 'cpu.live', 'rams.live', 'ssd.live']
SOURCE = 'Pricify 周数据（CC BY 4.0）'
LINK = 'https://pricify.ai'
PRO = re.compile(r'\bPRO\b|RTX A\d|Ada\b|Quadro|FirePro|Instinct|L40|Workstation|laptop', re.I)
# 内存 / 固态：容量档位（如 "32GB DDR5"）混了各种频率和单条，和我们的规格不一致；
# 改从单品列表里挑规格完全一致的商品，取典型价的中位数
SPECS = {
    'ddr5': ('rams.live', [r'DDR5', r'\(2x16GB\)', r'6000'], 'DDR5-6000 2x16GB'),
    'ddr4': ('rams.live', [r'DDR4', r'\(2x16GB\)', r'3200'], 'DDR4-3200 2x16GB'),
    'ssd2t': ('ssd.live', [r'\b2TB\b', r'NVMe'], '2TB NVMe'),
}
SPEC_EXCLUDE = re.compile(r'portable|external|laptop|SODIMM|enclosure', re.I)


def get_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'pc-parts-price-history/1.0'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def sync():
    """下载缓存里还没有的周。返回 [(周, {站点: 数据})]，按时间排序。"""
    os.makedirs(CACHE, exist_ok=True)
    weeks = []
    for y in get_json(REPO_API):
        if y['type'] != 'dir':
            continue
        for w in get_json(f"{REPO_API}/{y['name']}"):
            if w['type'] == 'dir':
                weeks.append(f"{y['name']}-{w['name']}")
    for wk in sorted(weeks):
        year, week = wk.split('-')
        for site in SITES:
            path = os.path.join(CACHE, wk, f'{site}.json')
            if os.path.exists(path):
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            try:
                data = get_json(f'{RAW}/{year}/{week}/json/{site}.json')
            except Exception as e:
                fp.log(f'[Pricify] {wk} {site} 下载失败：{e}')
                continue
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
    out = []
    for wk in sorted(os.listdir(CACHE)):
        docs = {}
        for site in SITES:
            p = os.path.join(CACHE, wk, f'{site}.json')
            if os.path.exists(p):
                with open(p, encoding='utf-8') as f:
                    docs[site] = json.load(f)
        if docs:
            out.append((wk, docs))
    return out


def pick_gpu(models, g):
    pat = re.compile(g['match'], re.I)
    cands = [m for m in models if pat.search(m['name']) and not PRO.search(m['name']) and m.get('typical_price_end')]
    if not cands:
        return None
    if '16G' in g['spec'] or '16GB' in g['spec']:
        sixteen = [m for m in cands if '16GB' in m['name']]
        if sixteen:
            cands = sixteen
    return min(cands, key=lambda m: len(m['name']))


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    weeks = sync()
    with open(fp.SOURCES, encoding='utf-8') as f:
        sz = json.load(f)['smzdm']
    cpu_items = next((f['items'] for f in fp.smzdm_feeds(sz) if f['key'] == 'cpu'), [])
    catalog = fp.load_gpu_catalog()

    pts = {}  # 'us-xxx' -> {date: (price, kind, link)}
    for wk, docs in weeks:
        models = []
        for site in ('rtx.live', 'vga.live'):
            models += docs.get(site, {}).get('models', [])
        end = (docs.get('rtx.live') or next(iter(docs.values())))['to']
        label = f"Pricify {wk} 周典型价（美国各零售商自营价）"
        for g in catalog:
            m = pick_gpu(models, g)
            if m:
                pts.setdefault(f"us-{g['key']}", {})[end] = (round(m['typical_price_end']), f"{label}：{m['name']}", LINK)
        cpus = docs.get('cpu.live', {}).get('models', [])
        for it in cpu_items:
            pat = re.compile(it['include'] if isinstance(it['include'], str) else it['include'][0], re.I)
            vals = [m['typical_price_end'] for m in cpus if pat.search(m['name']) and m.get('typical_price_end')]
            if vals:
                pts.setdefault(f"us-{it['cat']}", {})[end] = (round(statistics.median(vals)), f"{label}：同档 {len(vals)} 款中位数", LINK)
        for key, (site, pats, desc) in SPECS.items():
            vals = [x['typical_price_end'] for x in docs.get(site, {}).get('products', [])
                    if all(re.search(q, x['name'], re.I) for q in pats) and not SPEC_EXCLUDE.search(x['name'])
                    and x.get('typical_price_end')]
            if len(vals) >= 3:
                pts.setdefault(f'us-{key}', {})[end] = (round(statistics.median(vals)),
                                                        f'{label}：{desc} {len(vals)} 款中位数', LINK)

    history = fp.load_history()
    ents = history['entities']
    total, touched = 0, []
    for cid, p in pts.items():
        e = ents.get(cid)
        if not e:
            continue
        # 只补 PCPartPicker 历史之后的空白，不和已有日线交错
        pcpp_end = max((s['to'] for s in e.get('segments', []) if s['source'].startswith('PCPartPicker')), default='')
        p = {d: v for d, v in p.items() if d > pcpp_end}
        n = fp.set_point_source(e, SOURCE, p)
        if n:
            total += n
            touched.append(cid)
    history.setdefault('attribution', {})['pricify'] = 'Pricify (pricify.ai), CC BY 4.0'
    fp.save_history(history, quiet=True)
    fp.log(f'[Pricify] 共 {len(weeks)} 周（{weeks[0][0] if weeks else "-"} → {weeks[-1][0] if weeks else "-"}），'
           f'写入 {total} 个周数据点，涉及 {len(touched)} 个美国大盘')
    return total


if __name__ == '__main__':
    fp.run_locked(main)
