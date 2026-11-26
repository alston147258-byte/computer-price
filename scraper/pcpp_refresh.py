"""
自动追更 PCPartPicker 价格走势（每天采集时运行）。

互联网档案馆会不定期给 PCPartPicker 的走势页存档（内存页大约每几天到两周一次）。
本脚本检查美国 / 加拿大的显卡、内存、CPU、硬盘走势页有没有新存档；有的话从存档页里
找出新图片地址（图片本身在 PCPartPicker 的 CDN 上），追加到 pcpp_charts.json，
再调用 import_pcpartpicker 导入（顶部刻度自动识别）。

这样美国的内存 / CPU / 固态等没有每日现价来源的品类，也会随存档持续往后延伸（会滞后一到两周）。
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

CONF = os.path.join(fp.ROOT, 'scraper', 'pcpp_charts.json')
STATE = os.path.join(fp.ROOT, 'data', 'pcpp_refresh_state.json')
PAGES = [(region, host, kind)
         for region, host in (('US', 'pcpartpicker.com'), ('CA', 'ca.pcpartpicker.com'))
         for kind in ('video-card', 'memory', 'cpu', 'internal-hard-drive')]
# 非显卡图名 → 大盘 key
OTHER = [
    (r'\.ram\.ddr5\.6000\.2x16384\.', 'ddr5'),
    (r'\.ram\.ddr4\.3200\.2x16384\.', 'ddr4'),
    (r'\.storage\.ssdm2nvme\.2000\.', 'ssd2t'),
    (r'\.cpu\.zen_5\.ryzen-5\.am5\.', 'r5z5'),
    (r'\.cpu\.zen_5\.ryzen-7\.am5\.', 'r7z5'),
    (r'\.cpu\.zen_5\.ryzen-9\.am5\.', 'r9z5'),
    (r'\.cpu\.arrow_lake\.core-ultra-5\.lga1851\.', 'u5al'),
    (r'\.cpu\.arrow_lake\.core-ultra-7\.lga1851\.', 'u7al'),
    (r'\.cpu\.arrow_lake\.core-ultra-9\.lga1851\.', 'u9al'),
]
IMG_RE = re.compile(r'//cdna\.pcpartpicker\.com/static/forever/images/trends/[^"\'\s]+\.png')


def get(url, timeout=120):
    """archive.org 的 id_ 模式会原样返回当初的 gzip 压缩内容，需要自己解压。"""
    req = urllib.request.Request(url, headers={'User-Agent': 'pc-parts-price-history/1.0 (personal research)'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    if data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    return data.decode('utf-8', 'replace')


def chart_key(name, gpu_by_slug):
    m = re.search(r'gpu\.chipset\.([a-z0-9\-]+)\.[0-9a-f]{32}\.png$', name)
    if m:
        return gpu_by_slug.get(m.group(1))
    for pat, key in OTHER:
        if re.search(pat, name):
            return key
    return None


def refresh(max_snaps_per_page=2):
    state = {}
    if os.path.exists(STATE):
        with open(STATE, encoding='utf-8') as f:
            state = json.load(f)
    with open(CONF, encoding='utf-8') as f:
        conf = json.load(f)
    have = {c['url'] for c in conf['charts']}
    gpu_by_slug = {g['pcpp']: g['key'] for g in fp.load_gpu_catalog() if g.get('pcpp')}

    added, checked = [], 0
    for region, host, kind in PAGES:
        page_id = f'{host}/{kind}'
        since = state.get(page_id, '20260901000000')
        try:
            rows = json.loads(get(f'http://web.archive.org/cdx/search/cdx?url={host}/trends/price/{kind}/'
                                  f'&output=json&fl=timestamp,original&filter=statuscode:200&from={since}'))[1:]
        except Exception as e:
            fp.log(f'[PCPP追更] {page_id} 查询存档失败：{e}')
            continue
        rows = [r for r in rows if r[0] > since]
        checked += 1
        for ts, orig in rows[-max_snaps_per_page:]:  # 只看最新的几个快照
            try:
                html = get(f'http://web.archive.org/web/{ts}id_/{orig}')
            except Exception as e:
                fp.log(f'[PCPP追更] {page_id} {ts} 读取失败：{e}')
                continue
            imgs = sorted(set('https:' + u for u in IMG_RE.findall(html)))
            if not imgs:
                continue  # 有的快照是验证页，没有图
            for u in imgs:
                key = chart_key(u.rsplit('/', 1)[-1], gpu_by_slug)
                if key and u not in have:
                    conf['charts'].append({'region': region, 'key': key, 'top': None, 'url': u})
                    have.add(u)
                    added.append(f'{region}-{key}')
            state[page_id] = ts
            time.sleep(3)
        time.sleep(2)

    with open(STATE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    if added:
        with open(CONF, 'w', encoding='utf-8') as f:
            json.dump(conf, f, ensure_ascii=False, indent=2)
        fp.log(f'[PCPP追更] 发现 {len(added)} 张新走势图：{", ".join(sorted(set(added)))}，开始导入')
        import import_pcpartpicker
        import_pcpartpicker.main()
    else:
        fp.log(f'[PCPP追更] 检查了 {checked} 个走势页，没有新存档')
    return len(added)


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    fp.run_locked(refresh)
