"""
导入国内"社区月度参考价"：x1shang / frocloud-DIYPCmarket（CC BY 4.0）。

作者每月人工调查国内全新硬件的在售最低参考价（考虑店铺折扣），覆盖 2025-01 至今的显卡、CPU，
以及内存 / 固态的每 GB 价格。原仓库已下线，这里使用图吧工具箱数据仓库保留的转换副本
（保留了原署名、许可和源文件校验值）：
  https://github.com/royluobox/tubatools-pc-price-data/blob/main/site/data/frocloud-monthly.json

导入方式：每月一个价格点（日期取当月 15 日；"7月下旬"特别快照取 25 日），作为单独的来源，
只写在京东好价每日数据开始之前。CPU 档位 = 当月该档各型号价格的中位数；内存 / 固态按每 GB 价折算。
"""
import json
import os
import statistics
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_prices as fp  # noqa: E402

URL = 'https://raw.githubusercontent.com/royluobox/tubatools-pc-price-data/main/site/data/frocloud-monthly.json'
PAGE = 'https://github.com/royluobox/tubatools-pc-price-data/blob/main/site/data/frocloud-monthly.json'
LOCAL = os.path.join(fp.ROOT, 'data', 'frocloud-monthly.json')
SOURCE = '社区月度参考价（frocloud，CC BY 4.0）'

# 国内大盘 → (源数据型号键列表, 换算倍数, 口径说明)
MAP = {
    '4060':    (['vga|RTX4060'], 1, None),
    '4060ti':  (['vga|RTX4060TI8GB', 'vga|RTX4060TI16GB'], 1, '8G / 16G 两款的中位数'),
    '4070':    (['vga|RTX4070'], 1, None),
    '4070s':   (['vga|RTX4070S'], 1, None),
    '4070tis': (['vga|RTX4070TIS'], 1, None),
    '4080s':   (['vga|RTX4080S'], 1, None),
    '4090':    (['vga|RTX4090D'], 1, '4090 D'),
    '5050':    (['vga|RTX5050'], 1, None),
    '5060':    (['vga|RTX5060'], 1, None),
    '5060ti':  (['vga|RTX5060TI16GB', 'vga|RTX5060TI8GB'], 1, '8G / 16G 两款的中位数'),
    '5070':    (['vga|RTX5070'], 1, None),
    '5070ti':  (['vga|RTX5070TI'], 1, None),
    '5080':    (['vga|RTX5080'], 1, None),
    '5090d':   (['vga|RTX5090D'], 1, '5090 D'),
    '6600':    (['vga|RX6600'], 1, None),
    '6650xt':  (['vga|RX6650XT'], 1, None),
    '7600':    (['vga|RX7600'], 1, None),
    '7700xt':  (['vga|RX7700XT'], 1, None),
    '7800xt':  (['vga|RX7800XT'], 1, None),
    '7900gre': (['vga|RX7900GBRE'], 1, None),
    '7900xt':  (['vga|RX7900XT'], 1, None),
    '7900xtx': (['vga|RX7900XTX'], 1, None),
    '9060xt':  (['vga|RX9060XT16GB', 'vga|RX9060XT8GB'], 1, '8G / 16G 两款的中位数'),
    '9070':    (['vga|RX9070'], 1, None),
    '9070gre': (['vga|RX9070GBRE'], 1, None),
    '9070xt':  (['vga|RX9070XT'], 1, None),
    'b580':    (['vga|ARCB580'], 1, None),
    'r5z5':    (['cpu|RYZEN59600X', 'cpu|RYZEN59500'], 1, '9600X / 9500 的中位数'),
    'r7z5':    (['cpu|RYZEN79700X', 'cpu|RYZEN79800X3D', 'cpu|RYZEN79850X3D'], 1, '9700X / 9800X3D / 9850X3D 的中位数'),
    'r9z5':    (['cpu|RYZEN99900X', 'cpu|RYZEN99950X', 'cpu|RYZEN99900X3D', 'cpu|RYZEN99950X3D'], 1, '9900X / 9950X / X3D 的中位数'),
    'u5al':    (['cpu|ULTRA5225', 'cpu|ULTRA5230', 'cpu|ULTRA5245K', 'cpu|ULTRA5250KPLUS'], 1, '225 / 230 / 245K / 250K Plus 的中位数'),
    'u7al':    (['cpu|ULTRA7265K', 'cpu|ULTRA7270KPLUS'], 1, '265K / 270K Plus 的中位数'),
    'u9al':    (['cpu|ULTRA9285K'], 1, '285K'),
    'ddr5':    (['memory|FROCLOUDDDR516GX2'], 32, '16G×2 按每 GB 价折算为 32GB'),
    'ddr4':    (['memory|FROCLOUDDDR416GX2'], 32, '16G×2 按每 GB 价折算为 32GB'),
    'ssd2t':   (['solid_state_drive|FROCLOUDPCIE402TB'], 2000, 'PCIe 4.0 2TB 按每 GB 价折算'),
}


def download():
    try:
        req = urllib.request.Request(URL, headers={'User-Agent': fp.UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        json.loads(data)  # 确认是完整的 JSON 再覆盖本地副本
        with open(LOCAL, 'wb') as f:
            f.write(data)
    except Exception as e:
        if not os.path.exists(LOCAL):
            raise
        fp.log(f'[社区月度] 下载失败，用本地副本：{e}')
    with open(LOCAL, encoding='utf-8') as f:
        return json.load(f)


def month_points(d):
    """{型号键: {日期: 价格}}，月份取 15 日，"YYYY-MM-下旬" 快照取 25 日。"""
    out = {}
    for m in d['models']:
        s = out.setdefault(m['modelKey'], {})
        for h in m['history']:
            try:
                s[h['month'] + '-15'] = float(h['priceCny'])
            except (TypeError, ValueError):
                pass
    for sp in d.get('specialSnapshots', []):
        if sp.get('month', '').endswith('下旬'):
            try:
                out.setdefault(sp['modelKey'], {})[sp['month'][:7] + '-25'] = float(sp['priceCny'])
            except (TypeError, ValueError):
                pass
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    d = download()
    series = month_points(d)
    with open(fp.SOURCES, encoding='utf-8') as f:
        sz = json.load(f)['smzdm']
    meta = {}
    for feed in fp.smzdm_feeds(sz):
        for it in feed['items']:
            meta[it['cat']] = it
    history = fp.load_history()
    ents = history['entities']
    total = 0
    for cid, (keys, mult, note) in MAP.items():
        days = sorted({day for k in keys for day in series.get(k, {})})
        pts = {}
        for day in days:
            vals = [series[k][day] for k in keys if day in series.get(k, {})]
            if not vals:
                continue
            price = round(statistics.median(vals) * mult)
            kind = '社区月度最低参考价（作者人工调查，日期取月中）' if day.endswith('-15') else '社区特别快照（7 月下旬）'
            if note:
                kind += f'；{note}'
            pts[day] = (price, kind, PAGE)
        if not pts:
            continue
        e = ents.get(cid)
        if e is None:  # 国内还没有京东数据的型号：按配置新建大盘
            g = meta.get(cid)
            if not g:
                continue
            e = ents[cid] = {'kind': 'index', 'region': 'CN', 'group': g.get('group', '显卡'), 'name': g['name'],
                             'code': g.get('code', cid.upper()), 'spec': g['spec'], 'compare': g.get('compare', cid),
                             'index_mode': 'median', 'currency': 'CNY', 'decimals': 0, 'url': PAGE,
                             'source': f"京东好价（什么值得买，{sz.get('window_days', 7)}日中位）", 'bars': []}
            if cid not in history.setdefault('order', []):
                history['order'].append(cid)
        n = fp.set_point_source(e, SOURCE, pts)
        total += n
    history.setdefault('attribution', {})['frocloud'] = d.get('attribution')
    fp.save_history(history, quiet=True)
    fp.log(f"[社区月度] 数据截至 {d.get('dataThrough')}，写入 {total} 个月度价格点")
    return total


if __name__ == '__main__':
    fp.run_locked(main)
