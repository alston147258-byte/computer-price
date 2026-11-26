"""
把 scraper/cn_gpu_reports.json 里整理的国内显卡历史数据点（媒体报道）写进国内显卡大盘。

- 每个点一根K线（开=高=低=收=报道价格），并在大盘上记一段 segments（来源：媒体报道整理），
  以及 points_meta（每个点的口径和出处链接，页面悬停时显示）。
- 只写在"自己采集的京东好价数据"开始之前；重复运行会先清掉上次导入的报道点再重写。
- 第一次运行时顺带清理旧的 ZOL 参考价显卡数据（大盘K线和 ZOL 型号）。

用法：python scraper/import_cn_reports.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_prices as fp  # noqa: E402

CONF = os.path.join(fp.ROOT, 'scraper', 'cn_gpu_reports.json')
FROCLOUD = '社区月度参考价（frocloud，CC BY 4.0）'


def refresh_all():
    """先导入社区月度参考价，再导入媒体报道点（报道点只补月度数据覆盖不到的时间）。"""
    import import_frocloud
    import_frocloud.main()
    main()


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    with open(CONF, encoding='utf-8') as f:
        conf = json.load(f)
    with open(fp.SOURCES, encoding='utf-8') as f:
        sz = json.load(f)['smzdm']
    meta = {g['cat']: g for g in fp.smzdm_gpu_list()}
    src = conf['source']
    history = fp.load_history()
    ents = history['entities']

    for cid, points in conf['points'].items():
        e = ents.setdefault(cid, {})
        if e.get('source') == 'ZOL 参考价':  # 旧的 ZOL 参考价数据：不代表市场，清掉
            e['bars'] = []
            e['segments'] = []
            for k in [k for k, v in ents.items() if v.get('cat') == cid and '.z' in k]:
                del ents[k]
            fp.log(f'{cid}: 已清除旧的 ZOL 参考价数据')
        g = meta.get(cid, {})
        e.update({'kind': 'index', 'region': 'CN', 'group': g.get('group', '显卡'), 'name': g.get('name', cid),
                  'code': g.get('code', cid), 'spec': g.get('spec', ''), 'compare': g.get('compare'),
                  'index_mode': 'median', 'currency': 'CNY', 'decimals': 0, 'url': sz['url'],
                  'source': f"京东好价（什么值得买，{sz.get('window_days', 7)}日中位）"})
        # 社区月度参考价覆盖的时间段内，口径一致的月度数据优先，不再插入口径不一的报道点
        froc = sorted(d for d, m in (e.get('points_meta') or {}).items() if m.get('source') == FROCLOUD)
        if froc:
            points = [p for p in points if not (froc[0] <= p['date'] <= froc[-1])]
        n = fp.set_point_source(e, src, {p['date']: (p['price'], p['kind'], p['url']) for p in points})
        fp.log(f'{cid}: 写入 {n} 个报道数据点')

    fp.save_history(history)


if __name__ == '__main__':
    fp.run_locked(main)
