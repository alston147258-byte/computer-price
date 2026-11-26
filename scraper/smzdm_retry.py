"""
国内京东好价的"开机期间自动重试"（计划任务每 30 分钟运行一次）。

什么值得买大约十几个请求就会弹验证码，所以把请求分散到一整天 / 一整晚：
  1. 补今天：今天还没成功采到的分类（例如 19:00 那次被验证码挡住），先补上，并更新网站；
  2. 补历史：剩余的请求额度用来往回翻页（每个分类最多能翻到约 4–5 个月前），
     下次从上次停下的页数继续（会往回重叠 1 页，防止新爆料把页面往后推造成漏页）；
  3. 一旦遇到验证码立即停止，并暂停 retry_backoff_min 分钟后再试。不尝试绕过。

每次最多 retry_budget 个请求，间隔 retry_gap 秒（见 sources.json 的 smzdm）。
运行记录写在 data/retry.log，进度在 data/smzdm_retry_state.json。
"""
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_prices as fp  # noqa: E402

STATE = os.path.join(fp.ROOT, 'data', 'smzdm_retry_state.json')
PRIORITY = ['xianka', 'neicun', 'cpu', 'gutaiyingpan']  # 补历史的先后顺序


def load_state():
    if os.path.exists(STATE):
        with open(STATE, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_state(s):
    tmp = STATE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def recompute(conf, feed, history):
    ds = [d for d in history.get('deals', {}).values() if d.get('feed', 'xianka') == feed['key']]
    if not ds:
        return
    first = dt.date.fromtimestamp(min(d['ts'] for d in ds)) + dt.timedelta(days=conf.get('window_days', 7))
    days, d = [], first
    while d <= dt.date.today():
        days.append(d.isoformat())
        d += dt.timedelta(days=1)
    fp.apply_smzdm(conf, feed, history, days)


def run():
    state = load_state()
    now = time.time()
    if state.get('blocked_until', 0) > now:
        left = int((state['blocked_until'] - now) / 60)
        fp.log(f'[重试] 上次遇到验证码，还要等 {left} 分钟，本次跳过')
        return
    with open(fp.SOURCES, encoding='utf-8') as f:
        conf = json.load(f)['smzdm']
    feeds = {f['key']: f for f in fp.smzdm_feeds(conf)}
    # 自适应额度：没被拦就每轮多 1 个请求（最多 retry_budget_max），被拦就减半
    budget = state.get('budget', conf.get('retry_budget', 4))
    budget_start = budget
    gap = conf.get('retry_gap', 40)
    history = fp.load_history()
    today = dt.date.today().isoformat()
    health = history.get('health', {})
    new_today, backfilled, blocked = [], set(), None
    today_key = None  # 正在补"今天"的分类（被挡时记到它的状态里）

    def one_request():
        nonlocal budget
        budget -= 1
        if budget >= 0:
            time.sleep(gap)

    try:
        # 1. 补今天还没成功的分类
        for key, feed in feeds.items():
            if budget <= 0:
                break
            if health.get(f'smzdm-{key}', {}).get('last_ok', '').startswith(today):
                continue
            today_key = key
            n = fp.fetch_smzdm_deals(conf, feed, history, pages=1, gap=0)
            today_key = None
            fp.apply_smzdm(conf, feed, history, [today])
            fp.record_health(history, f'smzdm-{key}', True)
            new_today.append(f'{key}(+{n})')
            one_request()

        # 2. 往回补历史
        bf = state.setdefault('backfill', {})
        for key in PRIORITY:
            feed = feeds.get(key)
            if not feed:
                continue
            s = bf.setdefault(key, {'next': 2, 'done': False})
            while budget > 0 and not s['done']:
                pg = max(2, s['next'] - 1) if s.get('overlap', True) else s['next']
                s['overlap'] = False
                rows = fp.parse_smzdm_list(fp.fetch(f"{feed['url']}p{pg}/", 'utf-8', referer=feed['url']))
                one_request()
                if not rows:
                    s['done'] = True
                    fp.log(f'[重试] {key} 已翻到列表末尾（第 {pg} 页），历史补完')
                    break
                store = history.setdefault('deals', {})
                for r in rows:
                    if r['price'] and r['id'] not in store and fp.smzdm_matches(feed, r['title']):
                        r['feed'] = key
                        store[r['id']] = r
                oldest = dt.datetime.fromtimestamp(min(r['ts'] for r in rows))
                s.update({'next': pg + 1, 'oldest': f'{oldest:%Y-%m-%d}'})
                backfilled.add(key)
                if (dt.datetime.now() - oldest).days > 400:
                    s['done'] = True
            if s.get('done') is False and budget <= 0:
                s['overlap'] = True  # 下次从上一页开始，重叠一页防漏
            if budget <= 0:
                break
    except fp.SiteBlocked as e:
        blocked = str(e)
        if today_key:
            fp.record_health(history, f'smzdm-{today_key}', False, blocked)
            fp.save_history(history, quiet=True)
        state['blocked_until'] = now + conf.get('retry_backoff_min', 90) * 60
        state['blocks'] = state.get('blocks', 0) + 1
        fp.log(f'[重试] 遇到验证码，暂停 {conf.get("retry_backoff_min", 90)} 分钟（不会绕过）')
    except Exception as e:
        fp.log(f'[重试] 出错：{e}')

    if blocked:
        state['budget'] = max(2, budget_start // 2)
    elif budget <= 0:  # 额度用完且没被拦：下次加 1
        state['budget'] = min(conf.get('retry_budget_max', 10), budget_start + 1)
    for key in backfilled:
        recompute(conf, feeds[key], history)
    if new_today or backfilled:
        fp.save_history(history, quiet=True)
        if backfilled:
            import import_cn_reports  # 月度参考价 / 报道点只保留在京东好价开始之前
            import_cn_reports.refresh_all()
    state['last_run'] = dt.datetime.now().strftime('%Y-%m-%d %H:%M')
    save_state(state)

    prog = '，'.join(f"{k}→第{v.get('next')}页({v.get('oldest', '-')}{'，完成' if v.get('done') else ''})"
                    for k, v in state.get('backfill', {}).items())
    fp.log(f"[重试] 本轮 {budget_start} 个请求额度，下轮 {state.get('budget', budget_start)}；"
           f"今天补到：{', '.join(new_today) or '无'}；历史进度：{prog or '未开始'}"
           + (f'；{blocked}' if blocked else ''))

    # 不在这里发布：补到的数据只写进 history.json，等每天 19:00 的采集统一发布一次（省 Netlify 额度）
    if False:
        try:
            import build_site
            import deploy_netlify
            build_site.build()
            if deploy_netlify.deploy():
                state['last_deploy'] = now
                save_state(state)
        except Exception as e:
            fp.log(f'[重试] 发布失败：{e}')


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == '--log':
        path = os.path.join(fp.ROOT, sys.argv[2] if len(sys.argv) > 2 else 'data/retry.log')
        sys.stdout = sys.stderr = open(path, 'a', encoding='utf-8')
    fp.log(f'===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} 国内重试 =====')
    if not fp.acquire_lock(wait_seconds=0):
        fp.log('[重试] 另一个采集任务正在运行，本次跳过')
        return
    fp.keep_awake(True)
    try:
        run()
    except Exception:
        import traceback
        fp.log(traceback.format_exc())
    finally:
        fp.release_lock()
        if len(sys.argv) > 1 and sys.argv[1] == '--log':  # 计划任务运行时才自动睡眠
            fp.maybe_sleep()
        fp.keep_awake(False)


if __name__ == '__main__':
    main()
