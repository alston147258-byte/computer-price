"""
数据备份：每次采集后自动运行（也可以手动 python scraper/backup.py）。

备份到哪里：
  1. scraper/backup.json 里的 target（手动指定的文件夹）；
  2. 否则自动寻找 Google Drive 桌面版的同步文件夹（各盘符下的 "My Drive" / "我的云端硬盘"）；
  3. 都找不到时，先备份到项目里的 backups/ 文件夹，并在日志里提醒。

备份内容（放在 <目标>/装机行情备份/ 下）：
  daily/history-YYYY-MM-DD.json.gz   每天一份价格历史快照，全部保留（每份约几百 KB）
  项目/                               代码、配置、走势图缓存等的镜像，只复制有变化的文件

备份前会先确认 history.json 能正常解析，避免把写坏的文件同步到云端。
"""
import datetime as dt
import gzip
import json
import os
import shutil
import string
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(ROOT, 'scraper', 'backup.json')
HISTORY = os.path.join(ROOT, 'data', 'history.json')

# 镜像到云端的项目文件（data/prices.js 可由 history.json 生成，不备份；缓存的 __pycache__ 也不要）
MIRROR = ['index.html', 'app.js', 'data.js', 'style.css', 'scraper', 'data/pcpp',
          'data/wayback_dram.json', 'data/history.json']
SKIP_DIRS = {'__pycache__'}


def log(*a):
    print(*a, flush=True)


def find_google_drive():
    roots = [f'{d}:\\' for d in string.ascii_uppercase if os.path.exists(f'{d}:\\')]
    cands = []
    for r in roots:
        cands += [os.path.join(r, 'My Drive'), os.path.join(r, '我的云端硬盘')]
    home = os.path.expanduser('~')
    cands += [os.path.join(home, 'Google Drive'), os.path.join(home, 'My Drive'), os.path.join(home, '我的云端硬盘')]
    for c in cands:
        if os.path.isdir(c):
            return c
    return None


def load_conf():
    if os.path.exists(CONF):
        with open(CONF, encoding='utf-8') as f:
            return json.load(f)
    return {}


def target_dir():
    conf = load_conf()
    folder = conf.get('folder_name', '装机行情备份')
    base = conf.get('target') or find_google_drive()
    if base and os.path.isdir(base):
        return os.path.join(base, folder), True
    return os.path.join(ROOT, 'backups'), False


def copy_if_changed(src, dst):
    if os.path.exists(dst):
        s, d = os.stat(src), os.stat(dst)
        if s.st_size == d.st_size and int(s.st_mtime) <= int(d.st_mtime):
            return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return True


def run():
    # 1. 先确认数据文件是好的
    with open(HISTORY, encoding='utf-8') as f:
        data = json.load(f)
    n_ent = len(data.get('entities', {}))
    if n_ent == 0:
        raise RuntimeError('history.json 里没有数据，跳过备份以免覆盖')

    dest, is_drive = target_dir()
    os.makedirs(os.path.join(dest, 'daily'), exist_ok=True)

    # 2. 当天快照（同一天多次运行就覆盖当天那份）
    day = dt.date.today().isoformat()
    snap = os.path.join(dest, 'daily', f'history-{day}.json.gz')
    tmp = snap + '.tmp'
    with open(HISTORY, 'rb') as src, gzip.open(tmp, 'wb', compresslevel=9) as out:
        shutil.copyfileobj(src, out)
    os.replace(tmp, snap)

    # 3. 项目镜像（只复制变化的文件）
    copied = 0
    for rel in MIRROR:
        src = os.path.join(ROOT, rel)
        if os.path.isfile(src):
            copied += copy_if_changed(src, os.path.join(dest, '项目', rel))
        elif os.path.isdir(src):
            for dirpath, dirnames, filenames in os.walk(src):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for fn in filenames:
                    s = os.path.join(dirpath, fn)
                    copied += copy_if_changed(s, os.path.join(dest, '项目', os.path.relpath(s, ROOT)))

    n_snap = len([x for x in os.listdir(os.path.join(dest, 'daily')) if x.endswith('.json.gz')])
    size_kb = os.path.getsize(snap) // 1024
    where = 'Google Drive' if is_drive else '本地 backups/（未找到 Google Drive 同步文件夹）'
    log(f'[备份] {where}：{dest}')
    log(f'[备份] 今日快照 {os.path.basename(snap)}（{size_kb} KB，{n_ent} 个条目），共 {n_snap} 份快照；项目文件更新 {copied} 个')
    if not is_drive:
        log('[备份] 提醒：安装并登录 Google Drive 桌面版后会自动改为备份到云端；也可以在 scraper/backup.json 里指定 target。')
    return dest, is_drive


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    run()
