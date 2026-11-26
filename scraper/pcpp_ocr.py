"""
PCPartPicker 走势图纵坐标刻度的自动识别（字形模板匹配，只用 Pillow）。

原理：图上的刻度文字（如 "$1200"）字体、字号固定。用已经人工确认过刻度的图，
把每条横向网格线左侧的标签切成单个字形，记下对应字符，作为模板；
新图的标签逐字形和模板比对（像素差最小者），拼出数字。
识别出的整列刻度必须是 0 起步的等差数列，否则判为失败，交给人工填写。
"""
import json
import os

from PIL import Image

DARK = 150  # 亮度低于它算作文字像素（再高会把相邻字符的抗锯齿像素连成一块）


def _gray(px, x, y):
    r, g, b = px[x, y][:3]
    return (r * 299 + g * 587 + b * 114) // 1000


def find_hlines(im):
    W, H = im.size
    px = im.load()

    def mid_gray(c):
        return abs(c[0] - c[1]) < 6 and abs(c[1] - c[2]) < 6 and 120 < c[0] < 200

    def black(c):
        return max(c[:3]) < 50

    def merge(rows):
        out = []
        for y in rows:
            if out and y - out[-1][-1] <= 1:
                out[-1].append(y)
            else:
                out.append([y])
        return [round(sum(v) / len(v)) for v in out]

    # 网格线只认灰色的；长时间水平不动的黑色价格线不能当成网格线
    gray = merge([y for y in range(2, H - 2) if sum(1 for x in range(W) if mid_gray(px[x, y])) > W * 0.4])
    hl = gray
    if len(gray) >= 2:
        diffs = sorted(b - a for a, b in zip(gray, gray[1:]))
        step = diffs[len(diffs) // 2]
        # 底部 $0 线可能被"发布前贴着 0 的黑线"整条盖住：按等间距补上
        cand = merge([y for y in range(2, H - 2)
                      if sum(1 for x in range(W) if mid_gray(px[x, y]) or black(px[x, y])) > W * 0.5])
        for y in cand:
            if abs(y - (gray[-1] + step)) <= 2:
                hl = gray + [y]
                break
    xs = [x for x in range(W) if mid_gray(px[x, hl[0]])] if hl else []
    left = xs[0] if xs else 50
    return hl, left


def label_glyphs(im, y, x_max):
    """切出 y 行左侧标签的字形，返回 [(宽, 高, 位图字节串), ...]（从左到右）。"""
    px = im.load()
    y0, y1 = max(0, y - 8), min(im.size[1], y + 9)
    X0 = 3  # 图片最左边有一像素深灰边框，跳过
    cols = [False] * X0
    for x in range(X0, max(X0, x_max - 3)):
        cols.append(any(_gray(px, x, yy) < DARK for yy in range(y0, y1)))
    glyphs, start = [], None
    for x, on in enumerate(cols + [False]):
        if on and start is None:
            start = x
        elif not on and start is not None:
            bits = ''.join('1' if _gray(px, xx, yy) < DARK else '0'
                           for yy in range(y0, y1) for xx in range(start, x))
            glyphs.append((x - start, y1 - y0, bits))
            start = None
    return glyphs


def _dist(a, b):
    if a[0] != b[0] or a[1] != b[1]:
        return 10 ** 9
    return sum(1 for p, q in zip(a[2], b[2]) if p != q)


def build_templates(known):
    """known: [(图片路径, 顶部刻度值), ...] → {字符: [字形, ...]}"""
    tpl = {}
    for path, top in known:
        im = Image.open(path).convert('RGB')
        hl, left = find_hlines(im)
        n = len(hl)
        if n < 3:
            continue
        for k, y in enumerate(reversed(hl)):  # 从 $0 往上
            value = top * k / (n - 1)
            if value != int(value):
                continue
            text = f'${int(value)}'
            g = label_glyphs(im, y, left)
            if len(g) != len(text):
                continue  # 字形粘连等情况，跳过
            for ch, gl in zip(text, g):
                lst = tpl.setdefault(ch, [])
                if all(_dist(gl, t) > 0 for t in lst):
                    lst.append(gl)
    return tpl


def read_label(glyphs, tpl, max_err=0.12):
    out = ''
    for gl in glyphs:
        best, best_d = None, 10 ** 9
        for ch, lst in tpl.items():
            for t in lst:
                d = _dist(gl, t)
                if d < best_d:
                    best, best_d = ch, d
        if best is None or best_d > max_err * gl[0] * gl[1]:
            return None
        out += best
    return out


def read_top(path, tpl):
    """识别一张图的顶部刻度。成功返回数值，失败返回 None。"""
    im = Image.open(path).convert('RGB')
    hl, left = find_hlines(im)
    n = len(hl)
    if n < 3:
        return None
    values = []
    for y in reversed(hl):
        s = read_label(label_glyphs(im, y, left), tpl)
        if not s or not s.startswith('$') or not s[1:].isdigit():
            return None
        values.append(int(s[1:]))
    step = values[1] - values[0]
    if values[0] != 0 or step <= 0 or any(values[i] != i * step for i in range(n)):
        return None
    return values[-1]


def load_templates(cache_path, known):
    if os.path.exists(cache_path):
        with open(cache_path, encoding='utf-8') as f:
            data = json.load(f)
        return {k: [tuple(g) for g in v] for k, v in data.items()}
    tpl = build_templates(known)
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(tpl, f)
    return tpl
