"""
把 site/ 自动部署到 Netlify（每次采集后运行；也可手动 python scraper/deploy_netlify.py）。

需要：Windows 用户环境变量 NETLIFY_AUTH_TOKEN（Netlify 个人访问令牌）。
令牌只从环境变量读取，不写进任何文件（scraper/ 会被备份到 Google Drive）。
站点在 scraper/netlify.json 里指定（默认 computer-price.netlify.app），站点 ID 会自动查询。

做法：调用 Netlify API，把 site/ 打成 zip 一次性上传（POST /sites/{id}/deploys），
等待处理完成后检查状态。没有设置令牌时直接跳过，不影响采集。
"""
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_DIR = os.path.join(ROOT, 'site')
CONF = os.path.join(ROOT, 'scraper', 'netlify.json')
API = 'https://api.netlify.com/api/v1'


def log(*a):
    print(*a, flush=True)


def api(method, path, token, body=None, content_type='application/json'):
    req = urllib.request.Request(API + path, data=body, method=method, headers={
        'Authorization': f'Bearer {token}',
        'Content-Type': content_type,
        'User-Agent': 'pc-parts-price-deploy/1.0',
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        msg = e.read().decode('utf-8', 'replace')[:300]
        if e.code == 401:
            raise RuntimeError('令牌无效或已过期（401），请重新生成并更新环境变量 NETLIFY_AUTH_TOKEN')
        raise RuntimeError(f'Netlify API {e.code}: {msg}')


def zip_site():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for dirpath, _, files in os.walk(SITE_DIR):
            for fn in files:
                if fn.endswith('.tmp'):
                    continue
                full = os.path.join(dirpath, fn)
                z.write(full, os.path.relpath(full, SITE_DIR).replace(os.sep, '/'))
    return buf.getvalue()


def deploy():
    token = os.environ.get('NETLIFY_AUTH_TOKEN', '').strip()
    if not token:
        log('[部署] 未设置 NETLIFY_AUTH_TOKEN，跳过自动部署')
        return None
    if not os.path.exists(os.path.join(SITE_DIR, 'index.html')):
        raise RuntimeError('site/ 还没有生成')
    conf = {}
    if os.path.exists(CONF):
        with open(CONF, encoding='utf-8') as f:
            conf = json.load(f)
    site = conf.get('site', 'computer-price.netlify.app')

    info = api('GET', f'/sites/{site}', token)          # 站点名（xxx.netlify.app）可直接当 ID 用
    site_id, url = info['id'], info.get('ssl_url') or info.get('url')
    data = zip_site()
    d = api('POST', f'/sites/{site_id}/deploys', token, body=data, content_type='application/zip')
    dep_id = d['id']
    for _ in range(60):                                  # 最多等 2 分钟
        if d.get('state') in ('ready', 'error'):
            break
        time.sleep(2)
        d = api('GET', f'/deploys/{dep_id}', token)
    if d.get('state') != 'ready':
        raise RuntimeError(f"部署没有完成，状态 {d.get('state')}：{d.get('error_message') or ''}")
    log(f'[部署] 已发布到 {url}（{len(data) // 1024} KB）')
    return url


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    deploy()
