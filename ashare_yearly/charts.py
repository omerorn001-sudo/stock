"""内嵌 SVG 图表（无第三方绘图依赖）。

提供：折线图、多系列归一化对比图、K 线图（含成交量）、分时图。
A 股习惯：红涨绿跌。
"""

from __future__ import annotations

import html
from typing import Any, Mapping, Sequence

UP = "#d92b2b"
DOWN = "#12a05c"
FLAT = "#8a8a8a"
GRID = "#ececec"
AXIS_TEXT = "#8a8a8a"
INK = "#2b2b2b"
BLUE = "#2f6fd0"
PALETTE = ("#d92b2b", "#2f6fd0", "#e08b17", "#12a05c", "#8e44ad", "#00838f", "#c2185b", "#5d4037")

Point = tuple[str, float]


def _esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def _num(value: float, digits: int = 2) -> str:
    text = f"{value:,.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _scale(value: float, lo: float, hi: float, out_lo: float, out_hi: float) -> float:
    if hi == lo:
        return (out_lo + out_hi) / 2
    return out_lo + (value - lo) * (out_hi - out_lo) / (hi - lo)


def _bounds(values: Sequence[float], pad_ratio: float = 0.06) -> tuple[float, float]:
    lo, hi = min(values), max(values)
    if lo == hi:
        delta = abs(lo) * 0.02 or 1.0
        return lo - delta, hi + delta
    pad = (hi - lo) * pad_ratio
    return lo - pad, hi + pad


def _svg(width: int, height: int, body: str, title: str = "") -> str:
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" '
        f'height="{height}" preserveAspectRatio="xMidYMid meet" font-family="-apple-system,BlinkMacSystemFont,'
        f'\'Segoe UI\',\'PingFang SC\',\'Microsoft YaHei\',sans-serif" role="img">'
    )
    caption = f'<title>{_esc(title)}</title>' if title else ""
    return f"{head}{caption}{body}</svg>"


def empty_chart(message: str = "无数据", width: int = 880, height: int = 200) -> str:
    """数据缺失时的占位图（不编造数据，只说明原因）。"""
    body = (
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fafafa" stroke="{GRID}"/>'
        f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" font-size="13" fill="{AXIS_TEXT}">'
        f"{_esc(message)}</text>"
    )
    return _svg(width, height, body, message)


def _axes(
    *,
    left: float,
    right: float,
    top: float,
    bottom: float,
    lo: float,
    hi: float,
    labels: Sequence[str],
    y_ticks: int = 4,
    y_suffix: str = "",
) -> str:
    parts: list[str] = []
    for i in range(y_ticks + 1):
        value = lo + (hi - lo) * i / y_ticks
        y = _scale(value, lo, hi, bottom, top)
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>')
        parts.append(
            f'<text x="{left - 6}" y="{y + 3.5:.1f}" text-anchor="end" font-size="10" fill="{AXIS_TEXT}">'
            f"{_num(value)}{_esc(y_suffix)}</text>"
        )
    if labels:
        count = len(labels)
        ticks = 5 if count >= 5 else count
        for i in range(ticks):
            idx = round(i * (count - 1) / max(1, ticks - 1)) if ticks > 1 else 0
            x = _scale(idx, 0, max(1, count - 1), left, right)
            anchor = "start" if i == 0 else ("end" if i == ticks - 1 else "middle")
            parts.append(
                f'<text x="{x:.1f}" y="{bottom + 15:.1f}" text-anchor="{anchor}" font-size="10" '
                f'fill="{AXIS_TEXT}">{_esc(labels[idx])}</text>'
            )
    return "".join(parts)


def line_chart(
    points: Sequence[Point],
    *,
    width: int = 880,
    height: int = 260,
    title: str = "",
    color: str | None = None,
    area: bool = True,
    y_suffix: str = "",
    empty_message: str = "无数据",
) -> str:
    """折线图。``points`` 为 (标签, 数值) 序列。"""
    if len(points) < 2:
        return empty_chart(empty_message, width, min(height, 200))
    values = [p[1] for p in points]
    labels = [p[0] for p in points]
    lo, hi = _bounds(values)
    left, right, top, bottom = 58.0, float(width - 14), 26.0, float(height - 24)
    stroke = color or (UP if values[-1] >= values[0] else DOWN)

    coords = [
        (
            _scale(i, 0, len(values) - 1, left, right),
            _scale(v, lo, hi, bottom, top),
        )
        for i, v in enumerate(values)
    ]
    path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    body = [f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fff"/>']
    body.append(_axes(left=left, right=right, top=top, bottom=bottom, lo=lo, hi=hi, labels=labels, y_suffix=y_suffix))
    if area:
        gid = f"g{abs(hash((title, len(points)))) % 100000}"
        body.append(
            f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" stop-color="{stroke}" stop-opacity="0.22"/>'
            f'<stop offset="100%" stop-color="{stroke}" stop-opacity="0.02"/></linearGradient></defs>'
        )
        body.append(
            f'<path d="{path} L{coords[-1][0]:.1f},{bottom:.1f} L{coords[0][0]:.1f},{bottom:.1f} Z" '
            f'fill="url(#{gid})" stroke="none"/>'
        )
    body.append(f'<path d="{path}" fill="none" stroke="{stroke}" stroke-width="1.6"/>')
    if title:
        body.append(f'<text x="{left}" y="16" font-size="12" fill="{INK}">{_esc(title)}</text>')
    last_x, last_y = coords[-1]
    body.append(f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2.6" fill="{stroke}"/>')
    body.append(
        f'<text x="{right}" y="16" text-anchor="end" font-size="11" fill="{stroke}">'
        f"{_esc(labels[-1])} {_num(values[-1])}{_esc(y_suffix)}</text>"
    )
    return _svg(width, height, "".join(body), title)


def multi_line_chart(
    series: Mapping[str, Sequence[Point]],
    *,
    width: int = 880,
    height: int = 320,
    title: str = "",
    normalize: bool = True,
    empty_message: str = "无数据",
) -> str:
    """多系列对比图。``normalize=True`` 时换算为以首日=100 的相对走势。"""
    usable = {name: list(pts) for name, pts in series.items() if len(pts) >= 2}
    if not usable:
        return empty_chart(empty_message, width, min(height, 200))

    prepared: dict[str, list[float]] = {}
    labels: list[str] = []
    for name, pts in usable.items():
        values = [p[1] for p in pts]
        if normalize and values[0]:
            values = [v / values[0] * 100 for v in values]
        prepared[name] = values
        if len(pts) > len(labels):
            labels = [p[0] for p in pts]

    flat = [v for values in prepared.values() for v in values]
    lo, hi = _bounds(flat)
    left, right, top, bottom = 58.0, float(width - 14), 40.0, float(height - 24)
    body = [f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fff"/>']
    body.append(
        _axes(
            left=left,
            right=right,
            top=top,
            bottom=bottom,
            lo=lo,
            hi=hi,
            labels=labels,
            y_suffix="" if not normalize else "",
        )
    )
    if normalize and lo <= 100 <= hi:
        y100 = _scale(100, lo, hi, bottom, top)
        body.append(
            f'<line x1="{left}" y1="{y100:.1f}" x2="{right}" y2="{y100:.1f}" stroke="{FLAT}" '
            f'stroke-width="1" stroke-dasharray="4 3"/>'
        )

    legend_x = left
    for i, (name, values) in enumerate(prepared.items()):
        color = PALETTE[i % len(PALETTE)]
        coords = [
            (_scale(j, 0, len(values) - 1, left, right), _scale(v, lo, hi, bottom, top))
            for j, v in enumerate(values)
        ]
        path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        body.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.4" opacity="0.92"/>')
        label = f"{name} {_num(values[-1] - 100, 1)}%" if normalize else f"{name} {_num(values[-1])}"
        body.append(f'<rect x="{legend_x}" y="12" width="9" height="9" fill="{color}"/>')
        body.append(f'<text x="{legend_x + 13}" y="20.5" font-size="11" fill="{INK}">{_esc(label)}</text>')
        legend_x += 13 + 7.4 * len(label) + 12
        if legend_x > right - 80:
            legend_x = left
    if title:
        body.append(f'<text x="{right}" y="20.5" text-anchor="end" font-size="11" fill="{AXIS_TEXT}">{_esc(title)}</text>')
    return _svg(width, height, "".join(body), title)


def candle_chart(
    bars: Sequence[Mapping[str, Any]],
    *,
    width: int = 880,
    height: int = 340,
    title: str = "",
    volume: bool = True,
    empty_message: str = "无 K 线数据",
) -> str:
    """K 线图（下方可选成交量副图）。

    ``bars`` 元素需包含 ``date/open/high/low/close``，``volume`` 可选。
    """
    rows = [
        b
        for b in bars
        if all(b.get(k) is not None for k in ("open", "high", "low", "close"))
    ]
    if not rows:
        return empty_chart(empty_message, width, min(height, 200))

    labels = [str(b.get("date") or b.get("time") or "") for b in rows]
    highs = [float(b["high"]) for b in rows]
    lows = [float(b["low"]) for b in rows]
    lo, hi = _bounds(lows + highs, 0.04)

    left, right, top = 58.0, float(width - 14), 26.0
    vol_height = 58.0 if volume and any(b.get("volume") for b in rows) else 0.0
    bottom = float(height - 24) - vol_height
    body = [f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fff"/>']
    body.append(_axes(left=left, right=right, top=top, bottom=bottom, lo=lo, hi=hi, labels=labels))

    count = len(rows)
    step = (right - left) / max(1, count)
    body_width = max(1.4, min(9.0, step * 0.62))
    for i, bar in enumerate(rows):
        open_, close_ = float(bar["open"]), float(bar["close"])
        x = left + step * (i + 0.5)
        color = UP if close_ >= open_ else DOWN
        y_high = _scale(float(bar["high"]), lo, hi, bottom, top)
        y_low = _scale(float(bar["low"]), lo, hi, bottom, top)
        y_open = _scale(open_, lo, hi, bottom, top)
        y_close = _scale(close_, lo, hi, bottom, top)
        y_top, y_bot = min(y_open, y_close), max(y_open, y_close)
        tooltip = (
            f"{labels[i]} 开{_num(open_)} 高{_num(float(bar['high']))} "
            f"低{_num(float(bar['low']))} 收{_num(close_)}"
        )
        body.append(f"<g><title>{_esc(tooltip)}</title>")
        body.append(
            f'<line x1="{x:.1f}" y1="{y_high:.1f}" x2="{x:.1f}" y2="{y_low:.1f}" stroke="{color}" stroke-width="1"/>'
        )
        body.append(
            f'<rect x="{x - body_width / 2:.1f}" y="{y_top:.1f}" width="{body_width:.1f}" '
            f'height="{max(1.0, y_bot - y_top):.1f}" fill="{color}" stroke="{color}"/>'
        )
        body.append("</g>")

    if vol_height:
        volumes = [float(b.get("volume") or 0) for b in rows]
        vmax = max(volumes) or 1.0
        vol_top = bottom + 16
        vol_bottom = float(height - 8)
        body.append(
            f'<line x1="{left}" y1="{vol_bottom:.1f}" x2="{right}" y2="{vol_bottom:.1f}" stroke="{GRID}"/>'
        )
        for i, bar in enumerate(rows):
            value = float(bar.get("volume") or 0)
            x = left + step * (i + 0.5)
            bar_height = (value / vmax) * (vol_bottom - vol_top)
            color = UP if float(bar["close"]) >= float(bar["open"]) else DOWN
            body.append(
                f'<rect x="{x - body_width / 2:.1f}" y="{vol_bottom - bar_height:.1f}" '
                f'width="{body_width:.1f}" height="{max(0.6, bar_height):.1f}" fill="{color}" opacity="0.55"/>'
            )
        body.append(f'<text x="{left - 6}" y="{vol_top + 8:.1f}" text-anchor="end" font-size="9" fill="{AXIS_TEXT}">量</text>')

    if title:
        body.append(f'<text x="{left}" y="16" font-size="12" fill="{INK}">{_esc(title)}</text>')
    return _svg(width, height, "".join(body), title)


def intraday_chart(
    points: Sequence[Point],
    prev_close: float | None = None,
    *,
    width: int = 880,
    height: int = 210,
    title: str = "",
    empty_message: str = "无分时数据",
) -> str:
    """分时图：以前收盘为基准线，红涨绿跌。"""
    if len(points) < 2:
        return empty_chart(empty_message, width, min(height, 180))
    values = [p[1] for p in points]
    labels = [p[0] for p in points]
    reference = prev_close if prev_close else values[0]
    span = max(abs(max(values) - reference), abs(min(values) - reference)) or (abs(reference) * 0.01 or 1.0)
    lo, hi = reference - span * 1.15, reference + span * 1.15
    left, right, top, bottom = 58.0, float(width - 52), 24.0, float(height - 22)
    color = UP if values[-1] >= reference else DOWN

    coords = [
        (_scale(i, 0, len(values) - 1, left, right), _scale(v, lo, hi, bottom, top))
        for i, v in enumerate(values)
    ]
    path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    y_ref = _scale(reference, lo, hi, bottom, top)
    body = [f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fff"/>']
    body.append(_axes(left=left, right=right, top=top, bottom=bottom, lo=lo, hi=hi, labels=labels, y_ticks=4))
    body.append(
        f'<line x1="{left}" y1="{y_ref:.1f}" x2="{right}" y2="{y_ref:.1f}" stroke="{FLAT}" '
        f'stroke-width="1" stroke-dasharray="4 3"/>'
    )
    body.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.5"/>')
    pct = (values[-1] / reference - 1) * 100 if reference else 0.0
    body.append(
        f'<text x="{right + 6}" y="{coords[-1][1] + 3.5:.1f}" font-size="10" fill="{color}">'
        f"{_num(pct, 2)}%</text>"
    )
    if title:
        body.append(f'<text x="{left}" y="15" font-size="11" fill="{INK}">{_esc(title)}</text>')
    return _svg(width, height, "".join(body), title)
