"""
回填国内的京东好价历史（什么值得买「发现」各分类列表往回翻页，最多能翻到约 4–5 个月前）。

- 分类：显卡 + sources.json 里 smzdm.feeds（内存 / 固态 / CPU）。
- 翻页间隔默认 15 秒；对方一旦返回验证码页面就立即停止全部抓取（不绕过），已抓到的部分照样保存。
- 抓到的爆料（只保存和型号匹配的）存在 history.json 的 deals 里；然后按天重算每个条目的 7 日京东好价中位数。
- 最后重跑 import_cn_reports，让显卡的媒体报道点只保留在京东好价数据开始之前。

用法：python scraper/backfill_smzdm.py [--pages 260] [--gap 15] [--feeds xianka,neicun,gutaiyingpan,cpu]
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_prices as fp  # noqa: E402
import import_cn_reports  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pages', type=int, default=260)
    ap.add_argument('--gap', type=float, default=15.0)
    ap.add_argument('--feeds', default='')
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    with open(fp.SOURCES, encoding='utf-8') as f:
        conf = json.load(f)['smzdm']
    feeds = fp.smzdm_feeds(conf)
    if args.feeds:
        want = set(args.feeds.split(','))
        feeds = [f for f in feeds if f['key'] in want]
    # 抓取期间只写单独的暂存文件，最后再读取最新的 history.json 合并（期间日常采集写入的内容不会被覆盖）
    stash_path = os.path.join(fp.ROOT, 'data', 'smzdm_backfill_deals.json')
    store = {}
    if os.path.exists(stash_path):
        with open(stash_path, encoding='utf-8') as f:
            store = json.load(f)

    def save_stash():
        tmp = stash_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(store, f, ensure_ascii=False)
        os.replace(tmp, stash_path)

    blocked = None
    for feed in feeds:
        kept = 0
        for pg in range(1, args.pages + 1):
            url = feed['url'] + ('' if pg == 1 else f'p{pg}/')
            try:
                rows = fp.parse_smzdm_list(fp.fetch(url, 'utf-8', referer=feed['url']))
            except fp.SiteBlocked as e:
                blocked = f"{feed['key']} 第 {pg} 页：{e}"
                break
            except Exception as e:
                fp.log(f"{feed['key']} 第 {pg} 页失败：{e}")
                time.sleep(args.gap)
                continue
            if not rows:
                fp.log(f"{feed['key']} 第 {pg} 页没有内容，列表到底了")
                break
            for r in rows:
                if r['price'] and r['id'] not in store and fp.smzdm_matches(feed, r['title']):
                    r['feed'] = feed['key']
                    store[r['id']] = r
                    kept += 1
            oldest = dt.datetime.fromtimestamp(min(r['ts'] for r in rows))
            if pg % 10 == 0 or pg == 1:
                fp.log(f"{feed['key']} 第 {pg} 页：最早 {oldest:%Y-%m-%d %H:%M}，本分类新增 {kept} 条")
                save_stash()  # 中途保存，防止中断白跑（重跑时会接着用）
            time.sleep(args.gap)
        fp.log(f"{feed['key']} 完成，新增 {kept} 条匹配的爆料")
        if blocked:
            break

    save_stash()
    if blocked:
        fp.log(f'停止：{blocked}（不会绕过验证码，已抓到的部分照常保存）')

    history = fp.load_history()  # 读取最新的数据再合并
    deals = history.setdefault('deals', {})
    for k, v in store.items():
        deals.setdefault(k, v)
    # 按天重算每个分类的 7 日中位数
    for feed in fp.smzdm_feeds(conf):
        ds = [d for d in history.get('deals', {}).values() if d.get('feed', 'xianka') == feed['key']]
        if not ds:
            continue
        first = dt.date.fromtimestamp(min(d['ts'] for d in ds))
        start = first + dt.timedelta(days=conf.get('window_days', 7))  # 前 window 天数据不完整
        days, d = [], start
        while d <= dt.date.today():
            days.append(d.isoformat())
            d += dt.timedelta(days=1)
        fp.apply_smzdm(conf, feed, history, days)
        fp.log(f"{feed['key']}：按天重算 {days[0] if days else '-'} → {days[-1] if days else '-'}")
    fp.save_history(history)
    import_cn_reports.refresh_all()


if __name__ == '__main__':
    fp.run_locked(main)
