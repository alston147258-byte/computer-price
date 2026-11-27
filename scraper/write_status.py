"""
生成仓库首页 README.md：采集进度一览（每次云端任务提交数据前运行）。
在 GitHub 仓库首页（手机也行）就能看到最近一次采集时间、各数据源状态、京东回填进度。
"""
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY = os.path.join(ROOT, 'data', 'history.json')
RETRY = os.path.join(ROOT, 'data', 'smzdm_retry_state.json')
OUT = os.path.join(ROOT, 'README.md')

SITE = 'https://computer-price.netlify.app'
ACTIONS = 'https://github.com/alston147258-byte/computer-price/actions'

NAMES = {
    'smzdm-xianka': '国内显卡（京东好价）', 'smzdm-neicun': '国内内存（京东好价）',
    'smzdm-gutaiyingpan': '国内固态（京东好价）', 'smzdm-cpu': '国内 CPU（京东好价）',
    'gpuprix': '美国 / 加拿大显卡（GPUPrix）', 'bestbuy-ca': '加拿大内存 / 固态 / CPU（Best Buy）',
    'dramexchange': '全球内存现货（DRAMeXchange）', 'fx': '汇率', 'pcpartpicker-archive': 'PCPartPicker 存档',
    'frocloud': '国内月度参考价（frocloud）', 'pricify': '美国周价（Pricify）', 'techfuel': '美国显卡（TechFuelHQ）',
    'cfm': '国内渠道价（CFM）', 'netlify': '网站发布', 'backup': 'Google Drive 备份',
}
CHANNELS = {'xianka': '显卡', 'neicun': '内存', 'gutaiyingpan': '固态', 'cpu': 'CPU'}


def load(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def main():
    h = load(HISTORY, {})
    r = load(RETRY, {})
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    runs = h.get('runs') or []
    lines = [
        '# 装机行情 · 采集进度',
        '',
        f'网站：[{SITE}]({SITE})　·　任务运行记录：[Actions]({ACTIONS})　·　方法说明：[数据来源与方法.md](数据来源与方法.md)',
        '',
        f'> 本页由云端任务自动生成，最后生成于 **{now}**（温哥华时间）。',
        '',
        '| 项目 | 时间 |',
        '|---|---|',
        f"| 最近一次每日采集（每天约 19:00，顺带发布网站） | {runs[-1] if runs else '—'} |",
        f"| 最近一次国内重试 / 京东回填（每 30 分钟） | {r.get('last_run', '—')} |",
        f"| 网站数据时间 | {h.get('updated', '—')} |",
        '',
        '## 京东历史回填',
        '',
        '| 频道 | 已回填到 | 状态 |',
        '|---|---|---|',
    ]
    for ch, st in (r.get('backfill') or {}).items():
        lines.append(f"| {CHANNELS.get(ch, ch)} | {st.get('oldest', '—')} | {'已到头' if st.get('done') else '进行中'} |")
    lines += ['', '## 数据源状态', '', '| 数据源 | 上次成功 | 状态 |', '|---|---|---|']
    for k, v in sorted((h.get('health') or {}).items(), key=lambda kv: NAMES.get(kv[0], kv[0])):
        fails = v.get('fails') or 0
        state = '正常' if not fails else f"连续失败 {fails} 次：{v.get('last_error_msg', '')}".strip('：')
        lines.append(f"| {NAMES.get(k, k)} | {v.get('last_ok', '—')} | {state} |")

    ents = [e for e in (h.get('entities') or {}).values() if e.get('kind') == 'index' and e.get('bars')]
    lines += ['', '## 覆盖', '', '| 地区 | 大盘数 | 最早数据 | 最新数据 |', '|---|---|---|---|']
    for reg, label in (('CN', '国内'), ('US', '美国'), ('CA', '加拿大'), ('GLOBAL', '全球现货')):
        es = [e for e in ents if (e.get('region') or ('GLOBAL' if 'DRAMeXchange' in str(e.get('group')) else 'CN')) == reg]
        if es:
            lines.append(f"| {label} | {len(es)} | {min(e['bars'][0][0] for e in es)} | {max(e['bars'][-1][0] for e in es)} |")
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print(f'[进度] 已写 README.md（{now}）')


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    main()
