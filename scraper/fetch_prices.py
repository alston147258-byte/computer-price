"""
装机行情 · 价格采集脚本（只用 Python 标准库）

每运行一次：
  1. 抓取 ZOL（中关村在线）产品列表页的"参考价" → 细节（各品牌型号）+ 大盘（中位价）
  2. 抓取 DRAMeXchange 首页的内存现货价 → 现货大盘（美元）
  3. 把今天的价格追加/合并到 data/history.json（历史从第一次运行开始积累）
  4. 生成 data/prices.js 供网页读取

同一天多次运行：开盘价保持当天第一次的值，最高/最低/收盘随之更新。

用法：
  python scraper/fetch_prices.py            # 全部采集
  python scraper/fetch_prices.py --only zol # 只采 ZOL（或 dram）
  python scraper/fetch_prices.py --dry-run  # 只打印，不写文件
"""
import argparse
import datetime as dt
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = os.path.join(ROOT, 'scraper', 'sources.json')
HISTORY = os.path.join(ROOT, 'data', 'history.json')
PRICES_JS = os.path.join(ROOT, 'data', 'prices.js')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/130.0 Safari/537.36')
REQUEST_GAP = 1.5  # 两次请求之间的间隔（秒），别给对方服务器添麻烦

# 品牌识别：产品名开头的别名 → 统一品牌名（长的别名优先匹配）
BRAND_ALIASES = {
    'msi微星': '微星', '微星': '微星', 'MSI': '微星',
    '华硕': '华硕', 'ASUS': '华硕', 'ROG': '华硕',
    '技嘉': '技嘉', 'GIGABYTE': '技嘉', 'AORUS': '技嘉',
    '七彩虹': '七彩虹', 'Colorful': '七彩虹',
    '索泰': '索泰', 'ZOTAC': '索泰',
    '影驰': '影驰', 'GALAX': '影驰',
    '耕升': '耕升', 'Gainward': '耕升',
    '映众': '映众', 'Inno3D': '映众',
    '铭瑄': '铭瑄', '盈通': '盈通', '万丽': '万丽', '瀚铠': '瀚铠',
    '蓝宝石': '蓝宝石', 'Sapphire': '蓝宝石',
    '撼讯': '撼讯', 'PowerColor': '撼讯',
    '讯景': '讯景', 'XFX': '讯景',
    '电竞叛客': '电竞叛客', '磐镭': '磐镭', '昂达': '昂达', '祥升': '祥升',
    '镭风': '镭风', '精影': '精影', '翔升': '翔升', '小影霸': '小影霸',
    'NVIDIA': 'NVIDIA', '英伟达': 'NVIDIA',
    'AMD': 'AMD', 'Intel': 'Intel', '英特尔': 'Intel',
    '金士顿': '金士顿', 'Kingston': '金士顿',
    '芝奇': '芝奇', 'G.SKILL': '芝奇', 'G.Skill': '芝奇',
    '光威': '光威', 'Gloway': '光威',
    '英睿达': '英睿达', 'Crucial': '英睿达',
    '美商海盗船': '海盗船', '海盗船': '海盗船', 'CORSAIR': '海盗船',
    '阿斯加特': '阿斯加特', 'Asgard': '阿斯加特',
    '威刚': '威刚', 'ADATA': '威刚', 'XPG': '威刚',
    '宏碁掠夺者': '宏碁掠夺者', '掠夺者': '宏碁掠夺者', '宏碁': '宏碁', 'acer': '宏碁',
    '金百达': '金百达', '十铨': '十铨', 'TEAMGROUP': '十铨', '朗科': '朗科',
    '三星': '三星', 'Samsung': '三星', 'SAMSUNG': '三星',
    '西部数据': '西部数据', 'WD': '西部数据',
    '致态': '致态', '铠侠': '铠侠', 'KIOXIA': '铠侠', '爱国者': '爱国者', '梵想': '梵想',
    '雷克沙': '雷克沙', 'Lexar': '雷克沙', '佰维': '佰维', '金泰克': '金泰克',
    '海康威视': '海康威视', '海康存储': '海康威视', '联想': '联想', '惠普': '惠普',
    '达墨': '达墨', '酷兽': '酷兽', '科赋': '科赋', '玖合': '玖合', '紫光': '紫光',
    '忆联': '忆联', '京东京造': '京东京造', '速柏': '速柏', '影驰HOF': '影驰',
    '神舟': '神舟', '致钛': '致态', '长江存储': '长江存储', '金胜维': '金胜维',
    '闪迪': '闪迪', 'SanDisk': '闪迪', '希捷': '希捷', 'Seagate': '希捷', 'PNY': 'PNY',
    '华为': '华为', '铭瑄MAXSUN': '铭瑄', 'Acer宏碁': '宏碁', 'Acer 掠夺者': '宏碁掠夺者', 'Acer': '宏碁',
    'Lexar': '雷克沙', '十铨科技': '十铨', 'AX电竞叛客': '电竞叛客', 'AX': '电竞叛客',
}
_ALIASES_SORTED = sorted(BRAND_ALIASES, key=len, reverse=True)


def log(*a):
    print(*a, flush=True)


def fetch(url, encoding, referer=None):
    headers = {
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }
    if referer:
        headers['Referer'] = referer
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode(encoding, 'replace')
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f'请求失败 {url}: {last_err}')


def split_brand(name):
    head = name.strip()
    for alias in _ALIASES_SORTED:
        if head.lower().startswith(alias.lower()):
            model = head[len(alias):].strip()
            # 去掉紧跟着的重复中文品牌（如 "msi微星" 之后）
            return BRAND_ALIASES[alias], model or head
    m = re.match(r'([一-鿿]{2,6})', head)
    if m:
        return m.group(1), head[len(m.group(1)):].strip() or head
    parts = head.split(None, 1)
    return parts[0], (parts[1] if len(parts) > 1 else head)


def tidy_model(model):
    # 去掉显卡名里冗长的 "GeForce" 等前缀，保留可辨识的部分
    model = re.sub(r'^[（(][^）)]*[）)]\s*', '', model)  # "Gloway（光威）龙武" → "龙武"
    model = re.sub(r'^(GeForce|Radeon)\s*', '', model, flags=re.I)
    return re.sub(r'\s+', ' ', model).strip()


# ---------------- ZOL ----------------
def parse_zol_list(html):
    out = []
    for li in re.split(r'<li data-follow-id="p', html)[1:]:
        pid = re.match(r'(\d+)', li)
        title = re.search(r'<h3><a href="([^"]+)"[^>]*>([^<]+)', li)
        price = re.search(r'class="price-type">([^<]+)<', li)
        if not (pid and title and price):
            continue
        p = price.group(1).strip()
        if not re.fullmatch(r'\d+(\.\d+)?', p):
            continue  # "概念产品" / "即将上市" / "停产" 等没有价格
        jd = [float(x) for x in re.findall(r'b2c-icon/jd\d*\.png" alt="">&yen;([\d.]+)', li)]
        out.append({
            'zol_id': pid.group(1),
            'name': title.group(2).strip(),
            'url': 'https://detail.zol.com.cn' + title.group(1),
            'price': float(p),
            'jd': jd,
        })
    pages = re.search(r'small-page-active"><b>\d+</b>/(\d+)', html)
    return out, int(pages.group(1)) if pages else 1


def scrape_zol(cfg, history):
    base = cfg['url'].rstrip('/') + '/'
    inc = re.compile(cfg.get('include', '.'), re.I)
    exc = re.compile(cfg['exclude'], re.I) if cfg.get('exclude') else None
    found, seen = [], set()
    total_pages = cfg.get('pages', 1)
    page = 1
    while page <= total_pages:
        url = base if page == 1 else f'{base}{page}.html'
        referer = base if page > 1 else re.sub(r'(https://[^/]+/[^/]+/).*', r'\1', base)
        html = fetch(url, 'gbk', referer)
        rows, max_page = parse_zol_list(html)
        total_pages = min(total_pages, max_page)
        for r in rows:
            if r['zol_id'] in seen:
                continue
            if not inc.search(r['name']) or (exc and exc.search(r['name'])):
                continue
            seen.add(r['zol_id'])
            found.append(r)
        page += 1
        time.sleep(REQUEST_GAP)

    # 已经在跟踪的型号优先保留，其余按列表顺序（热度）补足到 max_items
    tracked = {k for k, e in history['entities'].items() if e.get('cat') == cfg['id']}
    max_items = cfg.get('max_items', 20)
    keep = [r for r in found if f"{cfg['id']}.z{r['zol_id']}" in tracked]
    for r in found:
        if len(keep) >= max_items:
            break
        if r not in keep:
            keep.append(r)
    return keep


# ---------------- GPUPrix（美国 / 加拿大显卡） ----------------
REGION_CURRENCY = {'US': 'USD', 'CA': 'CAD'}
_BUNDLE_RE = re.compile(r'power supply|\bpsu\b|bundle|combo|desktop|gaming pc|mini pc|\bpc\b|laptop|notebook|'
                        r'legion|mobile gpu|refurb|renewed|\bused\b|open box|\s[&+]\s|\bw/', re.I)
_EN_BRANDS = {'asus': 'ASUS', 'msi': 'MSI', 'gigabyte': 'GIGABYTE', 'zotac': 'ZOTAC', 'pny': 'PNY',
              'nvidia': 'NVIDIA', 'sapphire': 'Sapphire', 'xfx': 'XFX', 'powercolor': 'PowerColor',
              'asrock': 'ASRock', 'gainward': 'Gainward', 'palit': 'Palit', 'inno3d': 'INNO3D',
              'galax': 'GALAX', 'colorful': 'Colorful', 'acer': 'Acer', 'yeston': 'Yeston', 'evga': 'EVGA',
              'maxsun': 'MAXSUN', 'aorus': 'GIGABYTE'}


GPU_CATALOG = os.path.join(ROOT, 'scraper', 'gpus.json')


def load_gpu_catalog():
    with open(GPU_CATALOG, encoding='utf-8') as f:
        return json.load(f)['gpus']


def gpu_group(g):
    return f"{g['brand']} 显卡"


def gpuprix_gpu_list():
    return [{'key': g['key'], 'slug': g['gpuprix'], 'name': g['name'], 'code': g['key'].upper(),
             'spec': g['spec'], 'include': g['match'], 'exclude': g.get('gpuprix_exclude'),
             'compare': g['key'], 'group': gpu_group(g), 'launch': g.get('launch')}
            for g in load_gpu_catalog() if g.get('gpuprix')]


def smzdm_gpu_list():
    return [{'cat': g.get('cn_key', g['key']), 'name': g.get('cn_name', g['name']), 'code': g['key'].upper(),
             'spec': g.get('cn_spec', g['spec']), 'include': g['match'], 'min_price': g.get('cn_min', 500),
             'max_price': g.get('cn_max', 1e9), 'compare': g['key'], 'group': gpu_group(g)}
            for g in load_gpu_catalog()]


def parse_gpuprix(html):
    """从型号页的 Next.js 数据里取出所有报价对象 {"store":..,"price":..,"region":..}。"""
    text = html.replace('\\"', '"').replace('\\\\', '\\')
    dec = json.JSONDecoder()
    offers, seen = [], set()
    for m in re.finditer(r'\{"store":"', text):
        try:
            obj, _ = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        if not isinstance(obj, dict) or 'price' not in obj or 'region' not in obj:
            continue
        key = obj.get('url') or (obj.get('store'), obj.get('title'), obj.get('price'))
        if key in seen:
            continue
        seen.add(key)
        offers.append(obj)
    return offers


def gpu_offer_brand(title):
    first = re.split(r'[\s\-]+', title.strip(), maxsplit=1)
    b = _EN_BRANDS.get(first[0].lower())
    if b:
        return b, (first[1] if len(first) > 1 else title)
    return first[0], (first[1] if len(first) > 1 else title)


def tidy_offer_model(title):
    t = re.sub(r'\((?:[^()]*\b(?:PCIe|GDDR|bit|DLSS|HDMI|DisplayPort|Fan)\b[^()]*)\)', '', title)
    t = re.sub(r',.*$', '', t)                          # 逗号后面多是卖点描述
    t = re.sub(r'\b(Graphics Card|Video Card|Gaming)\b', '', t, flags=re.I)
    t = re.sub(r'^(NVIDIA\s+)?(GeForce|Radeon)\s+', '', t, flags=re.I)
    t = re.sub(r'\s+', ' ', t).strip(' -·')
    return t[:70]


def fetch_fx(history):
    """记录每日汇率（1 外币 = ? 人民币），来源 frankfurter.app（欧洲央行参考汇率）。首次运行补齐 2023 年以来的历史。"""
    fx = history.setdefault('fx', {})
    if fx:
        url = 'https://api.frankfurter.app/latest?from=USD&to=CNY,CAD'
        data = json.loads(fetch(url, 'utf-8'))
        series = {data['date']: data['rates']}
    else:
        url = 'https://api.frankfurter.app/2023-01-01..?from=USD&to=CNY,CAD'
        series = json.loads(fetch(url, 'utf-8'))['rates']
    for d, r in series.items():
        cny = r['CNY']
        fx[d] = {'USD': round(cny, 4), 'CAD': round(cny / r['CAD'], 4)}
    history['fx'] = dict(sorted(fx.items()))
    last = max(history['fx'])
    return last, history['fx'][last]


def scrape_gpuprix(conf, history, day, log_prefix='[GPUPrix]'):
    ents = history['entities']
    order = []
    for g in conf['gpus']:
        inc = re.compile(g.get('include', '.'), re.I)
        exc = re.compile(g['exclude'], re.I) if g.get('exclude') else None
        html = fetch(f"https://gpuprix.com/us/gpus/{g['slug']}", 'utf-8')
        offers = parse_gpuprix(html)
        time.sleep(REQUEST_GAP)
        for region in conf['regions']:
            cid = f"{region.lower()}-{g['key']}"
            order.append(cid)
            cur = REGION_CURRENCY[region]
            ok = [o for o in offers
                  if o.get('region') == region and o.get('currency') == cur and o.get('inStock')
                  and str(o.get('condition', 'new')).lower() == 'new'
                  and isinstance(o.get('price'), (int, float)) and o['price'] > 0
                  and inc.search(o.get('title', '')) and not (exc and exc.search(o.get('title', '')))
                  and not _BUNDLE_RE.search(o.get('title', ''))]
            if not ok:
                log(f"{log_prefix} {region} {g['name']}: 没有有货的全新报价")
                continue
            idx = ents.setdefault(cid, {})
            idx.update({'kind': 'index', 'region': region, 'group': g.get('group', '显卡'), 'name': g['name'], 'code': g['code'],
                        'spec': g['spec'], 'compare': g.get('compare'), 'source': 'GPUPrix 各商店报价',
                        'url': f"https://gpuprix.com/{region.lower()}/gpus/{g['slug']}",
                        'currency': cur, 'decimals': 0, 'index_mode': 'median'})
            prices = [o['price'] for o in ok]
            # 跟踪的报价：已跟踪的优先，其余按价格从低到高补足
            tracked = {k for k, e in ents.items() if e.get('cat') == cid}
            ok.sort(key=lambda o: o['price'])

            def oid(o):
                return f"{cid}.o{abs(hash_url(o.get('url') or o.get('title'))):x}"
            keep = [o for o in ok if oid(o) in tracked]
            for o in ok:
                if len(keep) >= conf.get('max_items', 20):
                    break
                if o not in keep:
                    keep.append(o)
            for o in keep:
                brand, rest = gpu_offer_brand(o['title'])
                it = ents.setdefault(oid(o), {})
                it.update({'kind': 'item', 'cat': cid, 'region': region, 'brand': brand,
                           'model': f"{tidy_offer_model(rest)} · {o.get('store', '')}",
                           'fullname': o['title'], 'url': o.get('url', ''), 'store': o.get('store'),
                           'currency': cur, 'decimals': 0, 'seen': day})
                merge_price(it, day, r2(o['price']))
            value = r2(statistics.median(prices))
            merge_price(idx, day, value)
            log(f"{log_prefix} {region} {g['name']}: 有货 {len(ok)} 条，跟踪 {len(keep)} 条，中位价 {cur} {value}")
    return order


# ---------------- 什么值得买：京东好价（国内显卡） ----------------
class SiteBlocked(Exception):
    """对方返回了验证码/拦截页。按规矩停下，不尝试绕过。"""


_SMZDM_ITEM = re.compile(r'<li class="J_feed_za" timesort="(\d+)" articleid="\d+_(\d+)"[\s\S]*?</li>')
_SMZDM_EXCLUDE = re.compile(r'主机|整机|笔记本|台式|电脑|二手|拆机|准系统|显卡坞|扩展坞|套装|套餐|\+|＋|租赁|服务器', re.I)


def smzdm_brand(raw):
    """'ASUS/华硕' → '华硕'；'七彩虹' → '七彩虹'。"""
    parts = [p.strip() for p in raw.split('/') if p.strip()]
    for p in parts:
        if re.search(r'[一-鿿]', p):
            return BRAND_ALIASES.get(p, p)
    return BRAND_ALIASES.get(parts[0], parts[0]) if parts else '其他'


def parse_smzdm_list(html):
    if 'TencentCaptcha' in html or 'WafCaptcha' in html:
        raise SiteBlocked('什么值得买返回了验证码页面')
    import html as htmlmod
    out = []
    for m in _SMZDM_ITEM.finditer(html):
        li = m.group(0)
        title = re.search(r'<h5 class="feed-ver-title">\s*<a[^>]*>\s*([^<]+?)\s*</a>', li)
        gtm = re.search(r"gtmAddToCart\(\{'name':'(?:[^'\\]|\\.)*','id':'\d+','price':'([\d.]*)','brand':'([^']*)'\s*,'mall':'([^']*)'", li)
        show = re.search(r'class="z-highlight[^"]*">\s*([^<]+?)\s*<', li)
        price = None
        if gtm and gtm.group(1):
            price = float(gtm.group(1))
        elif show:
            pm = re.search(r'([\d.]+)\s*元', show.group(1))
            price = float(pm.group(1)) if pm else None
        out.append({'ts': int(m.group(1)), 'id': m.group(2),
                    'title': htmlmod.unescape(title.group(1)) if title else '',
                    'price': price, 'brand': smzdm_brand(gtm.group(2)) if gtm else '其他',
                    'mall': gtm.group(3) if gtm else '', 'show': show.group(1) if show else ''})
    return out


def smzdm_feeds(conf):
    """什么值得买的各个分类频道：显卡（型号来自 gpus.json）+ sources.json 里配置的内存 / 固态 / CPU。"""
    feeds = [{'key': 'xianka', 'url': conf['url'], 'pages': conf.get('pages', 6),
              'exclude': _SMZDM_EXCLUDE.pattern, 'items': conf.get('gpus') or smzdm_gpu_list()}]
    for f in conf.get('feeds', []):
        feeds.append(f)
    return feeds


def _as_list(x):
    return x if isinstance(x, list) else [x]


def _compiled(feed):
    """把一个分类的匹配规则编译好（缓存在 feed 上）。"""
    if '_c' not in feed:
        feed['_c'] = {
            'exclude': re.compile(feed['exclude'], re.I) if feed.get('exclude') else None,
            'items': [(it, [re.compile(p, re.I) for p in _as_list(it['include'])],
                       re.compile(it['exclude'], re.I) if it.get('exclude') else None) for it in feed['items']],
        }
    return feed['_c']


def smzdm_matches(feed, title):
    """返回标题匹配到的条目（可能多个）；被排除的标题返回 []。"""
    c = _compiled(feed)
    if c['exclude'] and c['exclude'].search(title):
        return []
    out = []
    for it, incs, exc in c['items']:
        if all(p.search(title) for p in incs) and not (exc and exc.search(title)):
            out.append(it)
    return out


def fetch_smzdm_deals(conf, feed, history, pages=None, gap=None):
    """抓取一个分类的好价列表，和我们型号匹配的爆料存进 history['deals']（按文章 id 去重）。
    遇到验证码抛 SiteBlocked。"""
    store = history.setdefault('deals', {})
    key = feed['key']
    newest = max((d['ts'] for d in store.values() if d.get('feed', 'xianka') == key), default=0)
    pages = pages or feed.get('pages', 6)
    gap = conf.get('gap', 6) if gap is None else gap
    added = 0
    for pg in range(1, pages + 1):
        url = feed['url'] + ('' if pg == 1 else f'p{pg}/')
        rows = parse_smzdm_list(fetch(url, 'utf-8', referer=feed['url']))
        if not rows:
            break
        for r in rows:
            if r['id'] in store or not r['price'] or not smzdm_matches(feed, r['title']):
                continue
            r['feed'] = key
            store[r['id']] = r
            added += 1
        if newest and min(r['ts'] for r in rows) < newest - 86400:  # 已经接上上次抓到的位置
            break
        time.sleep(gap)
    # 只保留近 400 天的爆料，控制文件大小
    cutoff = time.time() - 400 * 86400
    history['deals'] = {k: v for k, v in store.items() if v['ts'] >= cutoff}
    return added


def smzdm_index_day(conf, feed, history, day):
    """计算某一天（含）之前 window 天内、各条目的京东好价中位数，以及各品牌中位数。"""
    end = dt.datetime.strptime(day, '%Y-%m-%d') + dt.timedelta(days=1)
    start = end - dt.timedelta(days=conf.get('window_days', 7))
    lo, hi = start.timestamp(), end.timestamp()
    malls = tuple(conf.get('malls', ['京东']))
    result = {}
    for d in history.get('deals', {}).values():
        if d.get('feed', 'xianka') != feed['key'] or not (lo <= d['ts'] < hi):
            continue
        if not d['mall'].startswith(malls) or '全球购' in d['mall']:
            continue
        hits = smzdm_matches(feed, d['title'])
        # 标题同时匹配多个条目（如 "RTX 5070Ti 5080 5090D 魔鹰"、"9600X/9700X 可选"），分不清价格对应哪款，不用
        if len(hits) != 1:
            continue
        it = hits[0]
        if not (it.get('min_price', 0) <= d['price'] <= it.get('max_price', 1e9)):
            continue
        result.setdefault(it['cat'], []).append(d)
    out = {}
    for cat, ds in result.items():
        by_brand = {}
        for d in ds:
            by_brand.setdefault(d['brand'], []).append(d)
        out[cat] = (ds, by_brand)
    return out


def apply_smzdm(conf, feed, history, days):
    ents = history['entities']
    for day in days:
        res = smzdm_index_day(conf, feed, history, day)
        for g in feed['items']:
            cid = g['cat']
            if cid not in res:
                continue
            hits, by_brand = res[cid]
            idx = ents.setdefault(cid, {})
            if idx.get('source') == 'ZOL 参考价':  # 旧的 ZOL 参考价数据不代表市场，换源时清掉
                idx['bars'], idx['segments'] = [], []
                for k in [k for k, v in ents.items() if v.get('cat') == cid and '.z' in k]:
                    del ents[k]
            idx.update({'kind': 'index', 'region': 'CN', 'group': g.get('group', '显卡'), 'name': g['name'],
                        'code': g.get('code', cid.upper()), 'spec': g['spec'], 'compare': g.get('compare', cid),
                        'index_mode': 'median', 'samples': len(hits),
                        'source': f"京东好价（什么值得买，{conf.get('window_days', 7)}日中位）",
                        'url': feed['url'], 'currency': 'CNY', 'decimals': 0})
            merge_price(idx, day, r2(statistics.median(d['price'] for d in hits)))
            for brand, ds in by_brand.items():
                iid = f"{cid}.j{hash_url(brand):x}"
                it = ents.setdefault(iid, {})
                latest = max(ds, key=lambda d: d['ts'])
                it.update({'kind': 'item', 'cat': cid, 'region': 'CN', 'brand': brand,
                           'model': f"京东好价 · 近{conf.get('window_days', 7)}日 {len(ds)} 条",
                           'fullname': latest['title'], 'url': f"https://www.smzdm.com/p/{latest['id']}/",
                           'currency': 'CNY', 'decimals': 0, 'seen': day})
                merge_price(it, day, r2(statistics.median(d['price'] for d in ds)))


# ---------------- Best Buy 加拿大（内存 / 固态 / CPU） ----------------
def scrape_bestbuy_ca(conf, history, day):
    """用 bestbuy.ca 站内搜索接口取现价。大盘 = 符合条件商品现价的中位数；细节 = 各商品。"""
    import urllib.parse
    ents = history['entities']
    order = []
    for c in conf['items']:
        cid = f"ca-{c['key']}"
        order.append(cid)
        url = 'https://www.bestbuy.ca/api/v2/json/search?' + urllib.parse.urlencode(
            {'query': c['query'], 'lang': 'en-CA', 'pageSize': 48})
        data = json.loads(fetch(url, 'utf-8', referer='https://www.bestbuy.ca/'))
        time.sleep(conf.get('gap', 3))
        excl = re.compile(c.get('exclude', conf.get('exclude', '^$')), re.I)
        ok = []
        for p in data.get('products', []):
            name, price = p.get('name', ''), p.get('salePrice') or 0
            if not price or not (c.get('min_price', 0) <= price <= c.get('max_price', 1e9)):
                continue
            if not all(re.search(i, name, re.I) for i in c['include']) or excl.search(name):
                continue
            if p.get('isMarketplace') and not conf.get('include_marketplace', True):
                continue
            ok.append(p)
        if not ok:
            log(f"[BestBuy.ca] {c['key']}: 没有符合条件的商品")
            continue
        idx = ents.setdefault(cid, {})
        idx.update({k: v for k, v in c.get('meta', {}).items()})
        idx.update({'kind': 'index', 'region': 'CA', 'currency': 'CAD', 'decimals': 0, 'index_mode': 'median',
                    'source': 'Best Buy 加拿大（含第三方卖家）', 'url': 'https://www.bestbuy.ca/'})
        tracked = {k for k, e in ents.items() if e.get('cat') == cid}
        ok.sort(key=lambda p: p['salePrice'])
        keep = [p for p in ok if f"{cid}.bb{p['sku']}" in tracked]
        for p in ok:
            if len(keep) >= conf.get('max_items', 20):
                break
            if p not in keep:
                keep.append(p)
        for p in keep:
            iid = f"{cid}.bb{p['sku']}"
            first, _, rest = p['name'].partition(' ')
            it = ents.setdefault(iid, {})
            it.update({'kind': 'item', 'cat': cid, 'region': 'CA', 'brand': first,
                       'model': (rest[:70] + (' · 第三方' if p.get('isMarketplace') else ' · Best Buy 自营')),
                       'fullname': p['name'], 'url': 'https://www.bestbuy.ca' + p.get('productUrl', ''),
                       'store': 'bestbuy.ca', 'currency': 'CAD', 'decimals': 0, 'seen': day})
            merge_price(it, day, r2(p['salePrice']))
        value = r2(statistics.median(p['salePrice'] for p in ok))
        merge_price(idx, day, value)
        own = sum(1 for p in ok if not p.get('isMarketplace'))
        log(f"[BestBuy.ca] {c['key']}: {len(ok)} 件（自营 {own}），中位价 C${value}")
    return order


def hash_url(s):
    # 稳定的短哈希（Python 自带 hash() 每次运行会变）
    return int.from_bytes(__import__('hashlib').md5(s.encode('utf-8')).digest()[:6], 'big')


# ---------------- DRAMeXchange ----------------
_MONTHS = {m: i for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], 1)}
_UPDATE_RE = re.compile(r'Last\s*Update:\s*([A-Z][a-z]{2})[a-z]*\.?\s*(\d{1,2}),?\s+(\d{4})')


def dram_key(name):
    """条目名归一化：去掉空白、统一大小写（老页面写成 "(2Gx8)3200"，新页面是 "(2Gx8) 3200"）。"""
    return re.sub(r'\s+', '', name).lower()


def dram_keys(cfg):
    """配置里的 match 可以是一个名字，也可以是别名列表（DRAMeXchange 改过条目名）。"""
    m = cfg['match']
    return [dram_key(x) for x in (m if isinstance(m, list) else [m])]


def dram_row(rows, cfg):
    for k in dram_keys(cfg):
        if k in rows:
            return rows[k]
    return None


def parse_dram(html):
    """解析 DRAMeXchange 首页的现货表。返回 {归一化名: {...,'date': 'YYYY-MM-DD'}}。
    date 取该表自己的 "Last Update" 日期（DRAM 颗粒每日更新，内存条/闪存每周更新）。"""
    marks = []
    for m in _UPDATE_RE.finditer(html):
        mon = _MONTHS.get(m.group(1))
        if mon:
            marks.append((m.start(), f'{int(m.group(3)):04d}-{mon:02d}-{int(m.group(2)):02d}'))
    rows = {}
    pat = re.compile(
        r'<a[^>]*href="/Price/[^"]*"[^>]*>\s*<img[^>]*>\s*([^<]+?)\s*</a>\s*</td>\s*'
        r'<td[^>]*>([\d.]+)</td>\s*<td[^>]*>([\d.]+)</td>\s*'
        r'<td[^>]*>([\d.]+)</td>\s*<td[^>]*>([\d.]+)</td>\s*<td[^>]*>([\d.]+)</td>\s*'
        r'<td[^>]*>[\s\S]*?(-?[\d.]+)\s*%', re.I)
    for m in pat.finditer(html):
        name = re.sub(r'\s+', ' ', m.group(1)).strip()
        key = dram_key(name)
        if key in rows:
            continue
        date = None
        for pos, d in marks:
            if pos < m.start():
                date = d
            else:
                break
        rows[key] = {
            'name': name,
            'date': date,
            'session_high': float(m.group(4)),
            'session_low': float(m.group(5)),
            'avg': float(m.group(6)),
            'change_pct': float(m.group(7)),
        }
    return rows


# ---------------- 历史合并 ----------------
LEGACY_POINT_SOURCE = '媒体报道整理（口径不一）'  # 早期导入的报道点没有记录 source


def set_point_source(entity, source, points):
    """写入一个"单点数据"来源（媒体报道、社区月度参考价等）。
    points: {日期: (价格, 口径说明, 出处链接)}。每个点在 points_meta 里单独记录来源；
    只写在本条目"自己每日采集的数据"开始之前；同一天已有其他来源的点时不覆盖。
    返回实际写入的点数。"""
    meta = entity.setdefault('points_meta', {})
    mine = {d for d, m in meta.items() if m.get('source', LEGACY_POINT_SOURCE) == source}
    bars = [b for b in entity.get('bars', []) if b[0] not in mine]
    for d in mine:
        meta.pop(d, None)
    pcpp_like = [s for s in entity.get('segments', []) if not any(
        m.get('source', LEGACY_POINT_SOURCE) == s['source'] for m in meta.values()) and s['source'] != source]
    own = [b for b in bars if b[0] not in meta and not any(s['from'] <= b[0] <= s['to'] for s in pcpp_like)]
    own_start = own[0][0] if own else '9999-12-31'
    n = 0
    for d in sorted(points):
        if d >= own_start or d in meta:
            continue
        p, kind, url = points[d]
        bars.append([d, p, p, p, p])
        meta[d] = {'kind': kind, 'url': url, 'source': source}
        n += 1
    bars.sort(key=lambda b: b[0])
    entity['bars'] = bars
    days = sorted(d for d, m in meta.items() if m.get('source', LEGACY_POINT_SOURCE) == source)
    segs = [s for s in entity.get('segments', []) if s['source'] != source]
    if days:
        segs.append({'from': days[0], 'to': days[-1], 'source': source})
    entity['segments'] = sorted(segs, key=lambda s: s['from'])
    return n


def in_segment(entity, day):
    """day 是否落在某个外部历史来源（segments）的区间里。"""
    return any(s['from'] <= day <= s['to'] for s in entity.get('segments', []))


def claim_day(entity, day):
    """自己采集的数据要写入 day：如果这一天属于外部历史来源，删掉那根历史K线并收缩该来源的区间。"""
    meta = entity.get('points_meta') or {}
    if day in meta:  # 这一天原来是某个单点来源的点：让位给自采数据
        meta.pop(day)
        entity['bars'] = [b for b in entity.get('bars', []) if b[0] != day]
        return
    segs = entity.get('segments')
    if not segs:
        return
    # 单点来源（报道、月度参考价）的区间只是显示用，按点判断来源，不需要收缩
    point_sources = {m.get('source', LEGACY_POINT_SOURCE) for m in meta.values()}
    daily_segs = [s for s in segs if s['source'] not in point_sources and s['from'] <= day <= s['to']]
    if not daily_segs:
        return
    entity['bars'] = [b for b in entity.get('bars', []) if b[0] != day]
    for s in daily_segs:
        inside = [b[0] for b in entity['bars'] if s['from'] <= b[0] < day]
        if inside:
            s['to'] = max(inside)
        else:
            segs.remove(s)


def merge_price(entity, day, price):
    """把一个价格并入当天的K线：开盘=前收盘（首日为当价），高/低随多次采集更新。
    前一根K线来自外部历史来源时，不跨来源衔接开盘价。"""
    claim_day(entity, day)
    bars = entity.setdefault('bars', [])
    # 找到 day 应在的位置（回填历史时 day 可能早于已有数据）
    i = len(bars)
    while i > 0 and bars[i - 1][0] > day:
        i -= 1
    if i > 0 and bars[i - 1][0] == day:
        b = bars[i - 1]
        b[2] = max(b[2], price)
        b[3] = min(b[3], price)
        b[4] = price
    else:
        prev = bars[i - 1] if i > 0 else None
        prev_foreign = prev and (prev[0] in (entity.get('points_meta') or {}) or in_segment(entity, prev[0]))
        o = prev[4] if prev and not prev_foreign else price
        bars.insert(i, [day, o, max(o, price), min(o, price), price])


def set_bar(entity, day, o, h, l, c):
    """写入/覆盖某一天的K线，保持按日期排序（回填历史时日期不一定在最后）。"""
    bars = entity.setdefault('bars', [])
    bar = [day, o, max(h, o, c), min(l, o, c), c]
    for i in range(len(bars) - 1, -1, -1):
        if bars[i][0] == day:
            bars[i] = bar
            return
        if bars[i][0] < day:
            bars.insert(i + 1, bar)
            return
    bars.insert(0, bar)


DRAM_GROUP = '现货 · DRAMeXchange'


def dram_entity(ents, cfg):
    e = ents.setdefault(cfg['id'], {})
    e.update({'kind': 'index', 'region': 'GLOBAL', 'group': DRAM_GROUP, 'name': cfg['name'], 'code': cfg['code'],
              'spec': cfg['spec'], 'source': 'DRAMeXchange 现货均价', 'currency': 'USD', 'decimals': 3,
              'url': 'https://www.dramexchange.com/'})
    return e


def dram_bar(row):
    """开盘由涨跌幅反推（= 上一期均价），高/低 = Session High/Low，收盘 = Session Average。"""
    c = row['avg']
    o = c / (1 + row['change_pct'] / 100) if row['change_pct'] > -100 else c
    return round(o, 3), row['session_high'], row['session_low'], c


def load_history():
    if os.path.exists(HISTORY):
        with open(HISTORY, encoding='utf-8') as f:
            return json.load(f)
    return {'version': 1, 'entities': {}, 'runs': []}


def save_history(history, quiet=False):
    os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
    tmp = HISTORY + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, HISTORY)
    with open(PRICES_JS, 'w', encoding='utf-8') as f:
        f.write('// 由 scraper/fetch_prices.py 自动生成，请勿手改\n')
        f.write('window.PRICE_DATA = ')
        json.dump(history, f, ensure_ascii=False, separators=(',', ':'))
        f.write(';\n')
    if not quiet:
        log(f'已写入 {os.path.relpath(HISTORY, ROOT)} 与 {os.path.relpath(PRICES_JS, ROOT)}')


LOCK = os.path.join(ROOT, 'data', '.run.lock')


def acquire_lock(wait_seconds=0):
    """同一时间只允许一个采集 / 重试任务写数据。锁超过 1 小时视为上次异常退出遗留，自动清掉。"""
    deadline = time.time() + wait_seconds
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f'{os.getpid()} {dt.datetime.now():%Y-%m-%d %H:%M:%S}'.encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(LOCK) > 3600:
                    os.remove(LOCK)
                    continue
            except FileNotFoundError:
                continue
            if time.time() >= deadline:
                return False
            time.sleep(15)


def release_lock():
    try:
        os.remove(LOCK)
    except FileNotFoundError:
        pass


def keep_awake(on=True):
    """采集期间不让系统睡眠（被定时唤醒时，Windows 默认 2 分钟没人操作就会再睡过去）。"""
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:
        pass


def user_idle_minutes():
    import ctypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
        return 0
    return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 60000


def maybe_sleep():
    """采集完让电脑睡眠，等下一次计划任务再唤醒。只在你一段时间没碰电脑时才睡，
    你正在用电脑（有键盘鼠标操作）时绝不睡。开关和等待时间在 sources.json 的 "power" 里。"""
    try:
        conf = json.load(open(os.path.join(ROOT, 'scraper', 'sources.json'), encoding='utf-8')).get('power', {})
        if not conf.get('auto_sleep'):
            return
        idle = user_idle_minutes()
        need = conf.get('idle_minutes', 20)
        if idle < need:
            log(f'[睡眠] 你 {idle:.0f} 分钟前还在用电脑（要求 {need} 分钟没操作），不睡眠')
            return
        time.sleep(5)
        if os.path.exists(LOCK):  # 另一个采集任务刚接上，让它跑完再说
            log('[睡眠] 另一个采集任务正在运行，不睡眠')
            return
        log(f'[睡眠] 已 {idle:.0f} 分钟没有操作，进入睡眠，等下次计划任务唤醒')
        try:
            sys.stdout.flush()
        except Exception:
            pass
        keep_awake(False)
        import ctypes
        ctypes.windll.powrprof.SetSuspendState(False, False, False)  # 睡眠（不是休眠）
    except Exception as e:
        log(f'[睡眠] 失败：{e}')


def run_locked(fn, *args, **kwargs):
    """单独运行某个导入脚本时用：先拿到锁（最多等 20 分钟），防止和计划任务同时写 history.json。
    （被每日采集调用时外层已经持锁，直接调用函数本身即可。）"""
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    if not acquire_lock(wait_seconds=1200):
        log('另一个采集任务一直在运行，本次跳过')
        return None
    try:
        return fn(*args, **kwargs)
    finally:
        release_lock()


def record_health(history, source, ok, msg=''):
    """记录每个数据源最近一次成功 / 失败，页面据此提示"某数据源已连续失败 N 次"。"""
    h = history.setdefault('health', {}).setdefault(source, {'fails': 0})
    now = dt.datetime.now().strftime('%Y-%m-%d %H:%M')
    if ok:
        h.update({'last_ok': now, 'fails': 0})
        h.pop('last_error_msg', None)
    else:
        h.update({'last_error': now, 'last_error_msg': msg[:200], 'fails': h.get('fails', 0) + 1})


def prev_close(entity, day):
    """严格早于 day 的最近一根K线的收盘价。"""
    for b in reversed(entity.get('bars', [])):
        if b[0] < day:
            return b[4]
    return None


def chain_index(idx, items, day):
    """链式指数：今日点数 = 昨日点数 × 各型号(今日价/昨日价)的平均值；首日 = 1000。"""
    base = prev_close(idx, day)
    if base is None:
        return 1000.0
    ratios = []
    for it in items:
        pc = prev_close(it, day)
        if pc:
            ratios.append(it['bars'][-1][4] / pc)
    return base * (sum(ratios) / len(ratios)) if ratios else base


def r2(x, nd=0):
    return round(x, nd) if nd else int(round(x))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', choices=['zol', 'smzdm', 'gpuprix', 'bestbuy', 'dram', 'fx'])
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--log', help='把输出追加到这个文件（计划任务用 pythonw 运行时没有控制台）')
    args = ap.parse_args()
    if args.log:
        path = args.log if os.path.isabs(args.log) else os.path.join(ROOT, args.log)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        sys.stdout = sys.stderr = open(path, 'a', encoding='utf-8')
        log(f'===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} 开始采集 =====')
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    keep_awake(True)
    if not args.dry_run and not acquire_lock(wait_seconds=1200):  # 重试任务正在跑时最多等 20 分钟
        log('另一个采集任务一直在运行，本次跳过')
        return 3
    try:
        return run(args)
    except Exception:
        import traceback
        log(traceback.format_exc())
        return 2
    finally:
        if not args.dry_run:
            release_lock()
            if args.log and args.only is None:  # 只有计划任务（带 --log 的完整采集）才自动睡眠
                maybe_sleep()
        keep_awake(False)


def run(args):

    with open(SOURCES, encoding='utf-8') as f:
        sources = json.load(f)

    # 先追更 PCPartPicker 走势（它自己读写 history.json，所以放在本次加载数据之前）
    pcpp_status = None
    if args.only is None:
        try:
            import pcpp_refresh
            pcpp_refresh.refresh()
            pcpp_status = (True, '')
        except Exception as e:
            pcpp_status = (False, str(e))
            log(f'[PCPP追更] 失败 {e}')

    # 美国 Pricify 周数据（每周一更新）：补 PCPartPicker 之后的空白，也是美国内存 / CPU / 固态的持续来源
    pricify_status = None
    if args.only is None:
        try:
            import import_pricify
            import_pricify.main()
            pricify_status = (True, '')
        except Exception as e:
            pricify_status = (False, str(e))
            log(f'[Pricify] 失败 {e}')

    # TechFuelHQ（CC BY 4.0）：补美国显卡 PCPartPicker 结束到 Pricify 开始之间的空档
    techfuel_status = None
    if args.only is None:
        try:
            import import_techfuel
            import_techfuel.main()
            techfuel_status = (True, '')
        except Exception as e:
            techfuel_status = (False, str(e))
            log(f'[TechFuel] 失败 {e}')
        # The Desk Brief 加拿大价格指数（存档，本地缓存）：补加拿大 7 月下旬到 9 月下旬
        try:
            import import_deskbrief
            import_deskbrief.main()
        except Exception as e:
            log(f'[DeskBrief] 失败 {e}')
        # BestValueGPU 月均价（只读档案馆存档，每周查一次有没有新存档）：补美国 / 加拿大老型号
        try:
            import import_bestvaluegpu
            import_bestvaluegpu.main()
        except Exception as e:
            log(f'[BestValueGPU] 失败 {e}')

    # 国内渠道市场周报价（CFM），每周更新，每天检查
    cfm_status = None
    if args.only is None:
        try:
            import cfm_channel
            cfm_channel.run()
            cfm_status = (True, '')
        except Exception as e:
            cfm_status = (False, str(e))
            log(f'[CFM] 失败 {e}')

    # 国内社区月度参考价（上游每月更新）+ 媒体报道点
    froc_status = None
    if args.only is None:
        try:
            import import_cn_reports
            import_cn_reports.refresh_all()
            froc_status = (True, '')
        except Exception as e:
            froc_status = (False, str(e))
            log(f'[社区月度] 失败 {e}')

    history = load_history()
    ents = history['entities']
    if pcpp_status:
        record_health(history, 'pcpartpicker-archive', *pcpp_status)
    if froc_status:
        record_health(history, 'frocloud', *froc_status)
    if pricify_status:
        record_health(history, 'pricify', *pricify_status)
    if techfuel_status:
        record_health(history, 'techfuel', *techfuel_status)
    if cfm_status:
        record_health(history, 'cfm', *cfm_status)

    now = dt.datetime.now()
    day = now.strftime('%Y-%m-%d')
    order = []  # 本次配置里大盘的顺序
    errors = []
    if pcpp_status and not pcpp_status[0]:
        errors.append(f'pcpp追更: {pcpp_status[1]}')

    if args.only in (None, 'zol'):
        for cfg in sources['zol']:
            order.append(cfg['id'])
            try:
                rows = scrape_zol(cfg, history)
            except Exception as e:  # 单个品类失败不影响其他品类
                errors.append(f"{cfg['id']}: {e}")
                log(f"[ZOL] {cfg['name']}: 失败 {e}")
                continue
            mode = cfg.get('index_mode', 'median')
            idx = ents.setdefault(cfg['id'], {})
            idx.update({k: cfg[k] for k in ('group', 'name', 'code', 'spec')})
            idx.update({'kind': 'index', 'region': 'CN', 'compare': cfg.get('compare'),
                        'source': 'ZOL 参考价', 'url': cfg['url'], 'index_mode': mode,
                        'currency': 'PTS' if mode == 'points' else 'CNY', 'decimals': 1 if mode == 'points' else 0})
            prices, today_items = [], []
            for r in rows:
                iid = f"{cfg['id']}.z{r['zol_id']}"
                brand, model = split_brand(r['name'])
                it = ents.setdefault(iid, {})
                it.update({'kind': 'item', 'cat': cfg['id'], 'region': 'CN', 'brand': brand, 'model': tidy_model(model),
                           'fullname': r['name'], 'url': r['url'], 'currency': 'CNY', 'decimals': 0,
                           'jd': r['jd'], 'seen': day})
                merge_price(it, day, r2(r['price']))
                prices.append(r['price'])
                today_items.append(it)
            if prices:
                if mode == 'points':
                    value = round(chain_index(idx, today_items, day), 1)
                else:
                    value = r2(statistics.median(prices))
                merge_price(idx, day, value)
                log(f"[ZOL] {cfg['name']}: {len(rows)} 款，{'指数' if mode == 'points' else '中位价'} {value}")
            else:
                log(f"[ZOL] {cfg['name']}: 没有匹配的型号")
    else:
        order.extend(c['id'] for c in sources['zol'])

    sz = sources.get('smzdm')
    if sz and 'gpus' not in sz:
        sz['gpus'] = smzdm_gpu_list()
    if sources.get('gpuprix') and 'gpus' not in sources['gpuprix']:
        sources['gpuprix']['gpus'] = gpuprix_gpu_list()
    if sz:
        feeds = smzdm_feeds(sz)
        for feed in feeds:
            order.extend(g['cat'] for g in feed['items'] if g['cat'] not in order)
        if args.only in (None, 'smzdm'):
            for feed in feeds:
                try:
                    n = fetch_smzdm_deals(sz, feed, history)
                    apply_smzdm(sz, feed, history, [day])
                    got = [g for g in feed['items'] if ents.get(g['cat'], {}).get('bars')
                           and ents[g['cat']]['bars'][-1][0] == day]
                    log(f"[京东好价·{feed['key']}] 新增爆料 {n} 条；有价格 {len(got)}/{len(feed['items'])} 项："
                        + '，'.join(f"{g['name']} ¥{ents[g['cat']]['bars'][-1][4]}" for g in got))
                    record_health(history, f"smzdm-{feed['key']}", True)
                except SiteBlocked as e:
                    errors.append(f"smzdm-{feed['key']}: {e}")
                    record_health(history, f"smzdm-{feed['key']}", False, str(e))
                    log(f'[京东好价] 停止：{e}。今天国内这几类不更新（不会绕过验证码）。')
                    break
                except Exception as e:
                    errors.append(f"smzdm-{feed['key']}: {e}")
                    record_health(history, f"smzdm-{feed['key']}", False, str(e))
                    log(f"[京东好价·{feed['key']}] 失败 {e}")
                time.sleep(sz.get('gap', 8))

    gp = sources.get('gpuprix')
    if gp:
        if args.only in (None, 'gpuprix'):
            try:
                order.extend(scrape_gpuprix(gp, history, day))
                record_health(history, 'gpuprix', True)
            except Exception as e:
                errors.append(f'gpuprix: {e}')
                record_health(history, 'gpuprix', False, str(e))
                log(f'[GPUPrix] 失败 {e}')
        else:
            order.extend(f"{r.lower()}-{g['key']}" for g in gp['gpus'] for r in gp['regions'])

    bb = sources.get('bestbuy_ca')
    if bb:
        if args.only in (None, 'bestbuy'):
            try:
                order.extend(scrape_bestbuy_ca(bb, history, day))
                record_health(history, 'bestbuy-ca', True)
            except Exception as e:
                errors.append(f'bestbuy.ca: {e}')
                record_health(history, 'bestbuy-ca', False, str(e))
                log(f'[BestBuy.ca] 失败 {e}')
        else:
            order.extend(f"ca-{c['key']}" for c in bb['items'])

    if args.only in (None, 'fx'):
        try:
            d, r = fetch_fx(history)
            log(f"[汇率] {d}: 1 USD = {r['USD']} CNY，1 CAD = {r['CAD']} CNY")
            record_health(history, 'fx', True)
        except Exception as e:
            errors.append(f'fx: {e}')
            record_health(history, 'fx', False, str(e))
            log(f'[汇率] 失败 {e}')

    if args.only in (None, 'dram'):
        try:
            html = fetch('https://www.dramexchange.com/', 'utf-8')
            rows = parse_dram(html)
            for cfg in sources['dram']:
                order.append(cfg['id'])
                r = dram_row(rows, cfg)
                if not r:
                    errors.append(f"{cfg['id']}: 页面上没找到 {cfg['match']}")
                    log(f"[DRAM] {cfg['name']}: 未找到条目 \"{cfg['match']}\"")
                    continue
                e = dram_entity(ents, cfg)
                set_bar(e, r['date'] or day, *dram_bar(r))
                log(f"[DRAM] {cfg['name']}: 均价 ${r['avg']}  ({r['change_pct']:+.2f}%)  报价日 {r['date']}")
            record_health(history, 'dramexchange', bool(rows), '' if rows else '页面上没有现货表')
        except Exception as e:
            errors.append(f'dram: {e}')
            record_health(history, 'dramexchange', False, str(e))
            log(f'[DRAM] 失败 {e}')
    else:
        order.extend(c['id'] for c in sources['dram'])

    history['order'] = order
    history['updated'] = now.strftime('%Y-%m-%d %H:%M:%S')
    history['runs'].append(history['updated'])
    history['runs'] = history['runs'][-500:]

    if args.dry_run:
        log('（dry-run，未写入文件）')
        return 1 if errors else 0

    save_history(history)
    # 按最新数据重算估算区间（新数据填上的月份去掉灰带，末尾的空档往后延）
    try:
        import build_estimates
        build_estimates.main()
    except Exception as e:
        log(f'[估算] 失败 {e}')
    # 更新可发布的网站目录 site/
    try:
        import build_site
        build_site.build()
    except Exception as e:
        errors.append(f'网站: {e}')
        log(f'[网站] 生成失败 {e}')
    else:
        try:
            import deploy_netlify
            if deploy_netlify.deploy():
                record_health(history, 'netlify', True)
        except Exception as e:
            errors.append(f'部署: {e}')
            record_health(history, 'netlify', False, str(e))
            log(f'[部署] 失败 {e}')
    # 采集完自动备份（备份失败不影响采集结果）
    # 在 GitHub Actions 里运行时跳过：数据每次都提交进仓库，仓库本身就是备份，云端也没有 Google Drive
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        history.get('health', {}).pop('backup', None)
    else:
        try:
            import backup
            _, to_drive = backup.run()
            record_health(history, 'backup', to_drive, '' if to_drive else '没找到 Google Drive，只备份到了本地')
        except Exception as e:
            errors.append(f'备份: {e}')
            record_health(history, 'backup', False, str(e))
            log(f'[备份] 失败 {e}')
    save_history(history, quiet=True)  # 把部署 / 备份的结果也记下来（下次页面显示）
    if errors:
        log('部分失败：\n  ' + '\n  '.join(errors))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
