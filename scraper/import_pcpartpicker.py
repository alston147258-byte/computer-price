"""
从 PCPartPicker 的"价格趋势"图表回填美国 / 加拿大显卡的历史日均价（从发布开始）。

PCPartPicker 的趋势页（pcpartpicker.com/trends/price/video-card/）每个芯片一张 PNG 图，
画的是近 18 个月每天的平均价格（黑线）。主站禁止程序访问，但：
  - 互联网档案馆存有趋势页快照，里面有当时图片的地址；
  - 图片本身放在 cdna.pcpartpicker.com/static/forever/ 下，可以直接下载。
本脚本把图片按像素读成每日价格：横向网格线 = 价格刻度（底线为 $0，顶线数值写在配置里），
纵向网格线 = 每月 1 号，黑线 = 日均价。精度约 ±$3（1 像素）。

配置：scraper/pcpp_charts.json —— 每张图：url、region、key（对应大盘 id 的后半段）、top（顶部网格线的数值）。
同一大盘有多张图时，较新的图覆盖重叠的日期。导入的K线只写在该大盘"自己采集的数据"开始之前，
并在大盘上记录 segments（哪段日期来自哪个数据源），页面上会标出数据源切换点。

用法：python scraper/import_pcpartpicker.py
"""
import datetime as dt
import json
import os
import re
import statistics
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_prices as fp  # noqa: E402

CONF = os.path.join(fp.ROOT, 'scraper', 'pcpp_charts.json')
CACHE_DIR = os.path.join(fp.ROOT, 'data', 'pcpp')
SOURCE = 'PCPartPicker 日均价（图表读数）'


def download(url):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, url.rsplit('/', 1)[-1])
    if not os.path.exists(path):
        req = urllib.request.Request(url, headers={'User-Agent': fp.UA})
        with urllib.request.urlopen(req, timeout=60) as r, open(path, 'wb') as f:
            f.write(r.read())
    return path


def digitize(path, top_value, image_date):
    from PIL import Image  # 只有这个脚本需要 Pillow
    im = Image.open(path).convert('RGB')
    W, H = im.size
    px = im.load()

    def grid(c):  # 网格线：中灰 (150,150,150) 或年份分隔线 (80,80,80)
        return abs(c[0] - c[1]) < 6 and abs(c[1] - c[2]) < 6 and (120 < c[0] < 200 or 70 < c[0] < 90)

    def mid_gray(c):
        return abs(c[0] - c[1]) < 6 and abs(c[1] - c[2]) < 6 and 120 < c[0] < 200

    # 横向网格线（灰色；底部 $0 线被黑线盖住时按等间距补上），与刻度识别共用
    import pcpp_ocr
    hlines, _ = pcpp_ocr.find_hlines(im)
    if len(hlines) < 3:
        raise ValueError('找不到横向网格线')
    top, bottom = hlines[0], hlines[-1]
    gaps = [b - a for a, b in zip(hlines, hlines[1:])]
    if max(gaps) - min(gaps) > 2:
        raise ValueError(f'横向网格线间距不均匀：{hlines}')

    def price(y):
        return (bottom - y) / (bottom - top) * top_value

    plot_h = bottom - top
    # 绘图区左右边界 = 顶部网格线的两端
    xs = [x for x in range(W) if mid_gray(px[x, top])]
    left, right = xs[0], xs[-1]
    vcols = [x for x in range(left + 2, right - 1)
             if sum(1 for y in range(top, bottom + 1) if grid(px[x, y])) > plot_h * 0.85]
    inner = []
    for x in vcols:  # 合并相邻列
        if inner and x - inner[-1][-1] <= 1:
            inner[-1].append(x)
        else:
            inner.append([x])
    inner = [round(sum(v) / len(v)) for v in inner]
    spacing = statistics.median(b - a for a, b in zip(inner, inner[1:]))
    ppd = spacing / 30.44  # 每天多少像素（粗估）

    # 把纵线对到"每月 1 号"，再用最小二乘拟合 x = a + b * 日序号
    anchors = []
    for x in inner:
        est = image_date - dt.timedelta(days=(right - x) / ppd)
        first = est.replace(day=1)
        nxt = (first + dt.timedelta(days=32)).replace(day=1)
        cand = min((first, nxt), key=lambda d: abs((d - est).days))
        if abs((cand - est).days) <= 5:
            anchors.append((cand.toordinal(), x))
    if len(anchors) < 3:
        raise ValueError('无法对齐月份网格线')
    n = len(anchors)
    mx = sum(a for a, _ in anchors) / n
    my = sum(x for _, x in anchors) / n
    b = sum((a - mx) * (x - my) for a, x in anchors) / sum((a - mx) ** 2 for a, _ in anchors)
    a0 = my - b * mx
    resid = max(abs(x - (a0 + b * d)) for d, x in anchors)
    if resid > 3:
        raise ValueError(f'月份网格线拟合误差过大（{resid:.1f}px）')

    by_day = {}
    # 图的左边缘常有一段从 $0 拉上来的竖线和几天不稳定的起始值，跳过最左边 5 天
    left_day = dt.date.fromordinal(int((left - a0) / b)) if b else None
    for x in range(left + 2, right - 1):
        ys = [y for y in range(top + 1, bottom) if max(px[x, y]) < 50]
        if not ys or len(ys) > plot_h * 0.6:
            continue
        p = price(statistics.median(ys))
        if p < top_value * 0.03:  # 发布前那段贴着 $0 底线的线不算
            continue
        day = dt.date.fromordinal(int((x - a0) / b))
        if left_day and (day - left_day).days < 5:
            continue
        by_day.setdefault(day, []).append(p)
    series = {d.isoformat(): round(sum(v) / len(v)) for d, v in sorted(by_day.items())}
    info = {'step': top_value / (len(hlines) - 1), 'px_per_day': round(b, 3), 'fit_err_px': round(resid, 2)}
    return series, info


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    with open(CONF, encoding='utf-8') as f:
        conf = json.load(f)
    catalog = fp.load_gpu_catalog()
    launch_dates = dict(conf.get('launch', {}))
    meta = {k: v for k, v in conf.get('meta', {}).items() if not k.startswith('_')}
    for g in catalog:
        if g.get('launch'):
            launch_dates.setdefault(g['key'], g['launch'])
        meta.setdefault(g['key'], {'group': fp.gpu_group(g), 'name': g['name'], 'code': g['key'].upper(),
                                   'spec': g['spec'], 'compare': g['key']})
    history = fp.load_history()
    ents = history['entities']

    # 顶部刻度：配置里没填的，用已确认刻度的图做字形模板自动识别
    import pcpp_ocr
    known = [(download(c['url']), c['top']) for c in conf['charts'] if c.get('top')]
    tpl = pcpp_ocr.load_templates(os.path.join(CACHE_DIR, 'ocr_templates.json'), known)

    merged = {}  # cid -> {date: price}
    failed = []
    conf_changed = False
    charts = sorted(conf['charts'], key=lambda c: c['url'].rsplit('/', 1)[-1])  # 文件名以日期开头
    for c in charts:  # 较新的图后处理、覆盖重叠日期
        m = re.search(r'/(\d{4})\.(\d{2})\.(\d{2})\.', c['url'])
        img_date = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        try:
            path = download(c['url'])
        except Exception as e:
            failed.append(f"{c['region']}-{c['key']} {c['url'].rsplit('/', 1)[-1][:40]}：下载失败 {e}")
            continue
        top = c.get('top')
        if not top:
            top = pcpp_ocr.read_top(path, tpl)
            if top:  # 识别结果写回配置，下次不用再识别
                c['top'], c['top_auto'] = top, True
                conf_changed = True
        if not top:
            failed.append(f"{c['region']}-{c['key']} {c['url'].rsplit('/', 1)[-1][:40]}：刻度识别失败，需人工填写 top")
            continue
        try:
            series, info = digitize(path, top, img_date)
        except ValueError as e:
            failed.append(f"{c['region']}-{c['key']} {c['url'].rsplit('/', 1)[-1][:40]}：{e}")
            continue
        launch = launch_dates.get(c['key'])
        if launch:
            series = {d: p for d, p in series.items() if d >= launch}
        cid = f"{c['region'].lower()}-{c['key']}"
        merged.setdefault(cid, {}).update(series)
        first, last = (min(series), max(series)) if series else ('-', '-')
        fp.log(f"{cid:12} {img_date}  {len(series):3} 天  {first} → {last}  顶部 {top}"
               f"{'' if c.get('top') else '（自动识别）'}  拟合误差 {info['fit_err_px']}px")

    for cid, series in merged.items():
        e = ents.get(cid)
        if not e:
            region, key = cid.split('-', 1)
            m = meta.get(key)
            if not m:
                fp.log(f'{cid}: 大盘还不存在，配置里也没有 meta，跳过')
                continue
            e = ents[cid] = dict(m, kind='index', region=region.upper(), index_mode='median', decimals=0,
                                 currency={'US': 'USD', 'CA': 'CAD'}[region.upper()],
                                 source=SOURCE, bars=[],
                                 url=f"https://{'ca.' if region == 'ca' else ''}pcpartpicker.com/trends/")
            fp.log(f'{cid}: 新建大盘（{m["name"]}）')
            if cid not in history.setdefault('order', []):
                history['order'].append(cid)
        segs = [s for s in e.get('segments', []) if s['source'] != SOURCE]
        own = [b for b in e.get('bars', []) if not any(s['from'] <= b[0] <= s['to'] for s in e.get('segments', [])
                                                          if s['source'] == SOURCE)]
        own_start = own[0][0] if own else '9999-12-31'
        days = sorted(d for d in series if d < own_start)
        bars, prev = [], None
        for d in days:
            p = series[d]
            o = prev if prev is not None else p
            bars.append([d, o, max(o, p), min(o, p), p])
            prev = p
        # 注意：不把自采第一天的开盘价接到历史最后一天——两个来源口径不同、中间可能隔了很久，
        # 页面会在数据源切换处画分隔线，并且不跨来源计算日涨跌。
        e['bars'] = bars + own
        if days:
            segs.append({'from': days[0], 'to': days[-1], 'source': SOURCE})
        e['segments'] = sorted(segs, key=lambda s: s['from'])
        fp.log(f'{cid}: 写入 {len(bars)} 天历史（{days[0] if days else "-"} → {days[-1] if days else "-"}），'
               f'之后接 {len(own)} 天自采数据')

    history.setdefault('backfill', {})['pcpartpicker'] = {'source': SOURCE, 'charts': len(conf['charts'])}
    fp.save_history(history)
    if conf_changed:
        with open(CONF, 'w', encoding='utf-8') as f:
            json.dump(conf, f, ensure_ascii=False, indent=2)
    if failed:
        fp.log(f'有 {len(failed)} 张图没有导入：')
        for f in failed:
            fp.log('  ' + f)


if __name__ == '__main__':
    fp.run_locked(main)
