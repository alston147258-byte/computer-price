"""
从互联网档案馆（Wayback Machine）回填 DRAMeXchange 现货价历史。

DRAMeXchange 的历史图表只对付费会员开放，但 archive.org 几乎每 1–2 天就存一次它的首页快照。
本脚本逐个下载这些快照，解析首页现货表（与日常采集同一套解析），按表内 "Last Update" 日期
还原出每一天的 Session High / Low / Average，写入 data/history.json。

- 解析结果缓存在 data/wayback_dram.json，中断后重跑会跳过已处理的快照。
- 请求间隔默认 4 秒，避免触发 archive.org 限流；首次全量大约需要 30–40 分钟。

用法：
  python scraper/backfill_dramexchange.py                 # 默认从 2023-01-01 开始
  python scraper/backfill_dramexchange.py --since 2024-06-01
  python scraper/backfill_dramexchange.py --merge-only    # 不下载，只用缓存重建
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_prices as fp  # noqa: E402

CACHE = os.path.join(fp.ROOT, 'data', 'wayback_dram.json')
CDX = ('http://web.archive.org/cdx/search/cdx?url=dramexchange.com/&output=json'
       '&fl=timestamp,original&filter=statuscode:200&filter=mimetype:text/html&collapse=timestamp:8&from={since}')


def http_get(url, timeout=60):
    last = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'pc-parts-price-history/1.0 (personal research)'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                # archive.org 的 id_ 模式会原样返回当初的 gzip 压缩内容，需要自己解压
                return __import__('gzip').decompress(data) if data[:2] == b'\x1f\x8b' else data
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                raise
            wait = 30 * (attempt + 1) if e.code == 429 else 5 * (attempt + 1)
            fp.log(f'  HTTP {e.code}，{wait} 秒后重试')
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f'请求失败 {url}: {last}')


def load_cache():
    if os.path.exists(CACHE):
        with open(CACHE, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_cache(cache):
    tmp = CACHE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, CACHE)


def fetch_snapshot(ts, original, gap):
    url = f'http://web.archive.org/web/{ts}id_/{original}'
    try:
        html = http_get(url, timeout=45).decode('utf-8', 'replace')
        rows = fp.parse_dram(html)
        if not rows:  # archive.org 偶尔返回不完整的页面，当作失败，下次重跑会再试
            rows = {'_error': f'页面里没有现货表（{len(html)} 字节）'}
    except Exception as e:
        rows = {'_error': str(e)}
    time.sleep(gap)
    return ts, rows


def download(since, gap, workers):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    cache = load_cache()
    # 之前失败的快照重新尝试
    cache = {k: v for k, v in cache.items() if v and '_error' not in v}
    snaps = json.loads(http_get(CDX.format(since=since.replace('-', '')), timeout=120))[1:]
    todo = [s for s in snaps if s[0] not in cache]
    fp.log(f'存档快照 {len(snaps)} 个（{snaps[0][0][:8]} → {snaps[-1][0][:8]}），'
           f'已缓存 {len(snaps) - len(todo)} 个，待下载 {len(todo)} 个（{workers} 路并发）')
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(fetch_snapshot, ts, orig, gap) for ts, orig in todo]
        for n, fut in enumerate(as_completed(futs), 1):
            ts, rows = fut.result()
            cache[ts] = rows
            if '_error' in rows:
                fp.log(f'[{n}/{len(todo)}] {ts[:8]}  失败：{rows["_error"]}')
            else:
                dates = sorted({r['date'] for r in rows.values() if r.get('date')})
                fp.log(f'[{n}/{len(todo)}] {ts[:8]}  {len(rows)} 个条目  报价日 {", ".join(dates[-2:]) or "?"}')
            if n % 10 == 0:
                save_cache(cache)
    save_cache(cache)
    return cache


def rebuild(cache):
    """用缓存的全部快照 + 当前页面，重建配置中各 DRAM 现货大盘的K线。"""
    with open(fp.SOURCES, encoding='utf-8') as f:
        sources = json.load(f)
    history = fp.load_history()
    ents = history['entities']

    # 当前页面也算一个快照，保证最新一天在内
    try:
        live = fp.parse_dram(fp.fetch('https://www.dramexchange.com/', 'utf-8'))
        cache = dict(cache, **{'99999999999999': live})
    except Exception as e:
        fp.log(f'读取当前页面失败（只用存档）：{e}')

    for cfg in sources['dram']:
        by_date = {}
        for ts in sorted(cache):  # 同一报价日以较晚的快照为准
            row = fp.dram_row(cache[ts], cfg) if isinstance(cache[ts], dict) and '_error' not in cache[ts] else None
            if row and row.get('date') and row.get('avg'):
                by_date[row['date']] = row
        e = fp.dram_entity(ents, cfg)
        e['bars'] = []
        for d in sorted(by_date):
            fp.set_bar(e, d, *fp.dram_bar(by_date[d]))
        if e['bars']:
            fp.log(f"{cfg['name']}: {len(e['bars'])} 根K线，{e['bars'][0][0]} → {e['bars'][-1][0]}，"
                   f"{e['bars'][0][4]} → {e['bars'][-1][4]} 美元")
        else:
            fp.log(f"{cfg['name']}: 存档中没有找到 \"{cfg['match']}\"")
        if cfg['id'] not in history.setdefault('order', []):
            history['order'].append(cfg['id'])

    history.setdefault('backfill', {})['dramexchange'] = {
        'source': 'web.archive.org 快照', 'snapshots': len(cache)}
    fp.save_history(history)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2023-01-01')
    ap.add_argument('--gap', type=float, default=2.0, help='每路请求之间的秒数')
    ap.add_argument('--workers', type=int, default=3, help='并发下载数（别太大，archive.org 会限流）')
    ap.add_argument('--merge-only', action='store_true')
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    cache = load_cache() if args.merge_only else download(args.since, args.gap, args.workers)
    rebuild(cache)


if __name__ == '__main__':
    fp.run_locked(main)
