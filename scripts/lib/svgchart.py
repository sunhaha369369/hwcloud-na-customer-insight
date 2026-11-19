"""把数据画成内联 SVG 图表片段（无 JS、无 CDN 依赖，离线可看、可打印）。

所有函数返回 <svg> 字符串，直接嵌入 HTML。配色按浅色主题（深色文字 + 浅底）。
"""

import math

from .common import clamp, fmt_money

INK = "#1f2933"
MUTED = "#667085"
GRID = "#e4e7ec"
PALETTE = ["#2b5b8c", "#3f8ecc", "#5eb0a8", "#e8a33d", "#c96a5a", "#7a6fae", "#8c9a3d", "#4f6b7a"]
TIER_COLOR = {"S": "#2b5b8c", "A": "#3f8ecc", "B": "#5eb0a8", "C": "#9aa5b1"}
LEVEL_COLOR = {"P0": "#c0392b", "P1": "#e8a33d", "P2": "#5b7c99", "A": "#2b5b8c", "B": "#3f8ecc", "C": "#9aa5b1"}


def esc(s):
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _num(v, default=0.0):
    try:
        f = float(v)
        return default if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return default


def _txt(x, y, s, size=11, fill=INK, anchor="start", weight="normal", extra=""):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}" {extra}>{esc(s)}</text>')


# ------------------------------------------------------------------ 横向条形

def hbar(items, width=680, bar_h=20, gap=12, label_w=170, value_w=110, fmt=None, unit_note=""):
    """items: [{label, value, color, note}]，按传入顺序自上而下绘制。"""
    if not items:
        return _empty(width)
    fmt = fmt or (lambda v: fmt_money(v))
    max_v = max(_num(i.get("value")) for i in items) or 1.0
    height = len(items) * (bar_h + gap) + 18 + (16 if unit_note else 0)
    plot_w = width - label_w - value_w - 12
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
             f'xmlns="http://www.w3.org/2000/svg" font-family="-apple-system,Segoe UI,Microsoft YaHei,sans-serif">']
    y = 8
    for i, it in enumerate(items):
        v = _num(it.get("value"))
        color = it.get("color") or PALETTE[i % len(PALETTE)]
        w = max(2.0, plot_w * (v / max_v))
        label = str(it.get("label", ""))[:26]
        parts.append(_txt(0, y + bar_h - 5, label, 12, INK))
        parts.append(f'<rect x="{label_w}" y="{y}" width="{plot_w}" height="{bar_h}" fill="#f2f4f7" rx="3"/>')
        parts.append(f'<rect x="{label_w}" y="{y}" width="{w:.1f}" height="{bar_h}" fill="{color}" rx="3"/>')
        parts.append(_txt(label_w + w + 7, y + bar_h - 5, fmt(v), 11, MUTED))
        note = it.get("note")
        if note:
            parts.append(_txt(width - 2, y + bar_h - 5, note, 10, MUTED, anchor="end"))
        y += bar_h + gap
    if unit_note:
        parts.append(_txt(0, height - 4, unit_note, 10, MUTED))
    parts.append("</svg>")
    return "".join(parts)


# ------------------------------------------------------------------ 折线

def line(series, xlabels, width=680, height=230, y_fmt=None, y_min=None, y_max=None,
         area=False, show_legend=True, zero_line=False, markers=True):
    """series: [{name, values:[...], color, dash}]，values 与 xlabels 等长（None 断点）。"""
    y_fmt = y_fmt or (lambda v: f"{v:,.0f}")
    pad_l, pad_r, pad_t, pad_b = 56, 12, 16 if not show_legend else 34, 30
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    all_v = [v for s in series for v in s.get("values", []) if v is not None]
    if not all_v:
        return _empty(width)
    lo = min(all_v) if y_min is None else y_min
    hi = max(all_v) if y_max is None else y_max
    if hi - lo < 1e-9:
        hi = lo + 1
    if y_min is None:
        lo = min(lo, 0) if lo > 0 and (lo - (hi - lo) * 0.1) < 0 else lo - (hi - lo) * 0.08
    hi = hi + (hi - lo) * 0.10
    span = hi - lo or 1.0

    def X(i):
        n = max(1, len(xlabels) - 1)
        return pad_l + plot_w * (i / n)

    def Y(v):
        return pad_t + plot_h * (1 - (v - lo) / span)

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
             f'xmlns="http://www.w3.org/2000/svg" font-family="-apple-system,Segoe UI,Microsoft YaHei,sans-serif">']
    # 网格与 Y 轴刻度
    for k in range(4):
        v = lo + span * k / 3
        y = Y(v)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" stroke="{GRID}"/>')
        parts.append(_txt(pad_l - 6, y + 3.5, y_fmt(v), 9.5, MUTED, anchor="end"))
    # X 轴标签（最多 12 个，避免拥挤）
    step = max(1, math.ceil(len(xlabels) / 12))
    for i, lb in enumerate(xlabels):
        if i % step == 0 or i == len(xlabels) - 1:
            parts.append(_txt(X(i), height - pad_b + 14, lb, 9.5, MUTED, anchor="middle"))
    if zero_line and lo < 0 < hi:
        parts.append(f'<line x1="{pad_l}" y1="{Y(0):.1f}" x2="{width - pad_r}" y2="{Y(0):.1f}" '
                     f'stroke="{MUTED}" stroke-dasharray="3 3"/>')
    # 序列
    for si, s in enumerate(series):
        color = s.get("color") or PALETTE[si % len(PALETTE)]
        seg, pts = [], []
        for i, v in enumerate(s.get("values", [])):
            if v is None:
                if len(pts) > 1:
                    seg.append(pts)
                pts = []
                continue
            pts.append((X(i), Y(v)))
        if len(pts) > 1:
            seg.append(pts)
        for pl in seg:
            d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pl)
            if area:
                area_d = (f"M{pl[0][0]:.1f},{Y(lo):.1f} L" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pl)
                          + f" L{pl[-1][0]:.1f},{Y(lo):.1f} Z")
                parts.append(f'<path d="{area_d}" fill="{color}" opacity="0.10"/>')
            parts.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="2" '
                         f'stroke-linejoin="round" stroke-linecap="round"'
                         + (f' stroke-dasharray="{s["dash"]}"' if s.get("dash") else "") + "/>")
            if markers:
                for x, y in pl:
                    parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.2" fill="#fff" stroke="{color}" '
                                 f'stroke-width="1.6"/>')
    # 图例
    if show_legend:
        lx = pad_l
        for si, s in enumerate(series):
            color = s.get("color") or PALETTE[si % len(PALETTE)]
            parts.append(f'<rect x="{lx}" y="8" width="9" height="9" rx="2" fill="{color}"/>')
            name = str(s.get("name", ""))[:16]
            parts.append(_txt(lx + 13, 16, name, 10, MUTED))
            lx += 24 + len(name) * 6.4
    parts.append("</svg>")
    return "".join(parts)


# ------------------------------------------------------------------ 气泡散点

def scatter(points, width=680, height=300, x_label="", y_label="", x_fmt=None, y_fmt=None,
            quadrants=True, median_lines=True, split_x=None, split_y=None, quad_labels=None,
            y_range=None, label_limit=14, bubble_scale=1.0):
    """气泡矩阵。默认按中位数切四象限；给了 split_x / split_y 就用指定阈值（更适合"价值×风险"）。

    quad_labels: 4 个字符串，顺序为 [右上, 右下, 左上, 左下]。
    """
    if not points:
        return _empty(width)
    x_fmt = x_fmt or (lambda v: f"{v:,.0f}")
    y_fmt = y_fmt or (lambda v: f"{v:+.0%}")
    pad_l, pad_r, pad_t, pad_b = 64, 22, 20, 42
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    xs = [_num(p["x"]) for p in points]
    ys = [_num(p["y"]) for p in points]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    if x_max - x_min < 1e-9:
        x_max = x_min + 1
    if y_max - y_min < 1e-9:
        y_max = y_min + 0.1
    xr = x_max - x_min
    yr = y_max - y_min
    x0, x1 = x_min - xr * 0.12, x_max + xr * 0.12
    if x_min >= 0:
        x0 = max(0.0, x0)                      # 消费额不会为负，坐标轴不要画到 0 以下
    if y_range:
        y0, y1 = float(y_range[0]), float(y_range[1])
    else:
        y0, y1 = y_min - yr * 0.18, y_max + yr * 0.18
    size_max = max([_num(p.get("size"), 1) for p in points] + [1])

    def X(v):
        return pad_l + plot_w * ((v - x0) / (x1 - x0))

    def Y(v):
        return pad_t + plot_h * (1 - (v - y0) / (y1 - y0))

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
             f'xmlns="http://www.w3.org/2000/svg" font-family="-apple-system,Segoe UI,Microsoft YaHei,sans-serif">']
    parts.append(f'<rect x="{pad_l}" y="{pad_t}" width="{plot_w}" height="{plot_h}" fill="#fbfcfd" '
                 f'stroke="{GRID}"/>')

    if median_lines:
        import statistics as _st
        mx = split_x if split_x is not None else _st.median(xs)
        my = split_y if split_y is not None else _st.median(ys)
        parts.append(f'<line x1="{X(mx):.1f}" y1="{pad_t}" x2="{X(mx):.1f}" y2="{pad_t + plot_h}" '
                     f'stroke="#cfd6df" stroke-dasharray="5 4"/>')
        parts.append(f'<line x1="{pad_l}" y1="{Y(my):.1f}" x2="{pad_l + plot_w}" y2="{Y(my):.1f}" '
                     f'stroke="#cfd6df" stroke-dasharray="5 4"/>')
        if split_x is not None:
            parts.append(_txt(X(mx), pad_t + plot_h + 15, x_fmt(mx), 9, MUTED, anchor="middle"))
        if split_y is not None:
            parts.append(_txt(pad_l - 6, Y(my) + 3.5, y_fmt(my), 9, MUTED, anchor="end"))
        if quadrants:
            q = quad_labels or [("高价值·高增长", pad_l + plot_w - 10, pad_t + 17, "end"),
                                ("高价值·待激活", pad_l + plot_w - 10, pad_t + plot_h - 9, "end"),
                                ("低价值·高增长", pad_l + 10, pad_t + 17, "start"),
                                ("低价值·低增长", pad_l + 10, pad_t + plot_h - 9, "start")]
            for text, tx, ty, anc in q:
                parts.append(_txt(tx, ty, text, 9.5, "#aeb6c2", anchor=anc, weight="600"))

    # 只给气泡最大的若干个加标签，避免重叠；其余靠表格查
    order = sorted(range(len(points)), key=lambda i: -_num(points[i].get("size"), 1))
    labelled = set(order[:label_limit])
    for i, p in enumerate(points):
        cx, cy = X(_num(p["x"])), Y(_num(p["y"]))
        r = (5 + 13 * math.sqrt(_num(p.get("size"), 1) / size_max)) * bubble_scale
        color = p.get("color", PALETTE[0])
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{color}" '
                     f'fill-opacity="0.50" stroke="{color}" stroke-width="1.3"/>')
        if i in labelled:
            label = str(p.get("label", ""))[:12]
            ty = cy - r - 5 if cy - r - 5 > pad_t + 10 else cy + r + 13
            parts.append(_txt(cx, ty, label, 9.5, INK, anchor="middle", weight="600",
                              extra='stroke="#ffffff" stroke-width="2.6" paint-order="stroke"'))
    parts.append(_txt(pad_l + plot_w / 2, height - 8, x_label, 10, MUTED, anchor="middle"))
    parts.append(_txt(14, pad_t + plot_h / 2, y_label, 10, MUTED, anchor="middle",
                      extra=f'transform="rotate(-90 14 {pad_t + plot_h / 2:.1f})"'))
    parts.append(_txt(pad_l - 6, height - pad_b + 15, x_fmt(x0), 9, MUTED, anchor="middle"))
    parts.append(_txt(pad_l + plot_w, height - pad_b + 15, x_fmt(x1), 9, MUTED, anchor="middle"))
    parts.append("</svg>")
    return "".join(parts)


# ------------------------------------------------------------------ 排名斜率图

def rank_slope(items, width=1000, row_h=32, left_title="按声量", right_title="按影响",
               left_fmt=None, right_fmt=None):
    """两列排名对比：items=[{label, left, right, note}]。

    专治「最吵的问题 ≠ 最贵的问题」——线往右上走代表影响被低估，往右下走代表声量被高估。
    """
    if not items:
        return _empty(width)
    left_fmt = left_fmt or (lambda v: f"#{int(v)}")
    right_fmt = right_fmt or (lambda v: f"#{int(v)}")
    n = len(items)
    top, bot = 34, 16
    height = top + (n - 1) * row_h + bot
    x_l, x_r = width * 0.60, width * 0.80
    rows = sorted(items, key=lambda i: _num(i.get("left"), 99))
    idx = {id(it): k for k, it in enumerate(rows)}
    max_rank = max([_num(i.get("left"), 1) for i in items] + [_num(i.get("right"), 1) for i in items])
    span = (max_rank - 1) * row_h

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
             f'xmlns="http://www.w3.org/2000/svg" font-family="-apple-system,Segoe UI,Microsoft YaHei,sans-serif">']
    # 交替行底纹：既是视觉引导，也让左侧留白不像"没画完"
    for k in range(n):
        if k % 2 == 0:
            parts.append(f'<rect x="0" y="{top + k * row_h - row_h / 2:.1f}" width="{width}" '
                         f'height="{row_h}" fill="#fafbfd"/>')
    parts.append(_txt(x_l, 14, left_title, 10.5, MUTED, anchor="middle", weight="600"))
    parts.append(_txt(x_r, 14, right_title, 10.5, MUTED, anchor="middle", weight="600"))
    parts.append(f'<line x1="{x_l}" y1="{top - 12}" x2="{x_l}" y2="{top + span + 10}" stroke="{GRID}"/>')
    parts.append(f'<line x1="{x_r}" y1="{top - 12}" x2="{x_r}" y2="{top + span + 10}" stroke="{GRID}"/>')

    for it in rows:
        k = idx[id(it)]
        y1 = top + k * row_h
        r = _num(it.get("right"), 99)
        y2 = top + (r - 1) * row_h if r <= max_rank else y1
        gap = _num(it.get("left"), 0) - r
        color = "#e0a13c" if gap > 0 else ("#3f8ecc" if gap < 0 else "#c9d0d9")
        parts.append(f'<line x1="{x_l}" y1="{y1:.1f}" x2="{x_r}" y2="{y2:.1f}" stroke="{color}" '
                     f'stroke-width="{2.4 if gap else 1.2}" stroke-linecap="round" opacity="0.9"/>')
        parts.append(f'<circle cx="{x_l}" cy="{y1:.1f}" r="3.8" fill="#fff" stroke="{color}" stroke-width="2"/>')
        parts.append(f'<circle cx="{x_r}" cy="{y2:.1f}" r="3.8" fill="{color}"/>')
        parts.append(_txt(4, y1 + 4, str(it.get("label", ""))[:16], 11.8, INK))
        parts.append(_txt(x_l - 11, y1 + 4, left_fmt(_num(it.get("left"))), 10, MUTED, anchor="end"))
        parts.append(_txt(x_r + 13, y2 + 4, right_fmt(r), 10.5, INK, anchor="start", weight="600"))

    parts.append("</svg>")
    return "".join(parts)


# ------------------------------------------------------------------ 迷你条形（表内用）

def mini_bar(value, max_value, width=54, height=6, color=PALETTE[1]):
    """表格单元格里的微型条，比数字更容易横向比较。"""
    w = 0 if max_value <= 0 else max(2.0, width * _num(value) / max_value)
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'style="vertical-align:middle"><rect width="{width}" height="{height}" fill="#eef1f5" rx="3"/>'
            f'<rect width="{w:.1f}" height="{height}" fill="{color}" rx="3"/></svg>')


# ------------------------------------------------------------------ 迷你走势

def sparkline(values, width=96, height=24, color="#2b5b8c", zero=True):
    vals = [_num(v, None) for v in values]
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return f'<svg width="{width}" height="{height}"></svg>'
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        hi = lo + 1
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        x = 2 + (width - 4) * i / (n - 1)
        y = 2 + (height - 4) * (1 - (v - lo) / (hi - lo))
        pts.append(f"{x:.1f},{y:.1f}")
    last_color = "#2f9e44" if vals[-1] >= vals[0] else "#c0392b"
    stroke = color if not zero else (color if vals[-1] >= vals[0] else "#c96a5a")
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'xmlns="http://www.w3.org/2000/svg" style="vertical-align:middle">'
            f'<polyline points="{" ".join(pts)}" fill="none" stroke="{stroke}" stroke-width="1.6" '
            f'stroke-linejoin="round"/></svg>')


def _empty(width=680):
    return (f'<svg viewBox="0 0 {width} 40" width="100%" xmlns="http://www.w3.org/2000/svg">'
            f'<text x="{width / 2}" y="24" font-size="11" fill="{MUTED}" text-anchor="middle">'
            f'（无可绘制的数据）</text></svg>')
