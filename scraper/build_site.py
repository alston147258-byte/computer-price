"""
生成可以直接发布到 Netlify 的静态网站目录 site/（每次采集后自动运行，也可手动运行）。

site/ 里只有网页需要的文件：index.html、app.js、data.js、style.css、data/prices.js，
以及 Netlify 的 _headers（让数据文件不被浏览器长期缓存）。
数据文件会去掉页面用不到的部分（原始爆料记录、运行记录），减小体积。

发布：把 site 文件夹拖到 Netlify 的 Deploys 页面；或用 Netlify CLI：
  netlify deploy --prod --dir=site
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, 'site')
HISTORY = os.path.join(ROOT, 'data', 'history.json')
FILES = ['index.html', 'app.js', 'data.js', 'style.css']
DROP_KEYS = ['deals', 'runs', 'backfill']          # 页面用不到
DROP_ENTITY_KEYS = ['jd', 'seen']

HEADERS = """/*
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin

/data/prices.js
  Cache-Control: public, max-age=0, must-revalidate

/*.js
  Cache-Control: public, max-age=0, must-revalidate

/*.css
  Cache-Control: public, max-age=0, must-revalidate
"""


def build():
    with open(HISTORY, encoding='utf-8') as f:
        data = json.load(f)
    for k in DROP_KEYS:
        data.pop(k, None)
    for e in data.get('entities', {}).values():
        for k in DROP_ENTITY_KEYS:
            e.pop(k, None)

    os.makedirs(os.path.join(SITE, 'data'), exist_ok=True)
    for fn in FILES:
        shutil.copy2(os.path.join(ROOT, fn), os.path.join(SITE, fn))
    # 早期估算区间（scraper/build_estimates.py 生成，手工维护，不随每日采集变化）
    est = os.path.join(ROOT, 'data', 'estimates.js')
    if os.path.exists(est):
        shutil.copy2(est, os.path.join(SITE, 'data', 'estimates.js'))
    out = os.path.join(SITE, 'data', 'prices.js')
    tmp = out + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('window.PRICE_DATA = ')
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
        f.write(';\n')
    os.replace(tmp, out)
    with open(os.path.join(SITE, '_headers'), 'w', encoding='utf-8') as f:
        f.write(HEADERS)
    size = os.path.getsize(out) // 1024
    print(f'[网站] 已生成 site/（数据 {size} KB）', flush=True)
    return SITE


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    build()
