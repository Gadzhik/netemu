"""Device icons drawn with QPainter (no image files -> easy cross-platform packaging)."""
from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QIcon, QPainter, QPainterPath,
                           QPen, QPixmap, QPolygonF)

VENDOR_COLORS = {
    "cisco": QColor("#1b6aa5"),
    "mikrotik": QColor("#3d4a57"),
    "juniper": QColor("#2e7d32"),
    "huawei": QColor("#c62828"),
    "extreme": QColor("#6a1b9a"),
    "fortinet": QColor("#ee3124"),
    "linux": QColor("#d35400"),
    "generic": QColor("#4f5bbd"),
    "isp": QColor("#2b8fd6"),
}

ICON_W, ICON_H = 64, 56

# ---------------------------------------------------------------------------------------------
# Icon style "A / Classic" (chosen by the user from the icon gallery): network-diagram shapes,
# body colour = vendor.  Drawn as SVG and rendered with QtSvg, so the canvas, the palette and the
# gallery page use exactly the same artwork.
# ---------------------------------------------------------------------------------------------


def _shade(hex_color: str, f: float) -> str:
    """f > 0 mixes with white, f < 0 with black."""
    n = int(hex_color[1:], 16)
    r, g, b = n >> 16, (n >> 8) & 255, n & 255
    t = 255 if f > 0 else 0
    k = abs(f)
    r, g, b = (round(c + (t - c) * k) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def _arrow(x1, y1, x2, y2, s, w=2.4) -> str:
    a = math.atan2(y2 - y1, x2 - x1)
    h = 4.2
    p1 = (x2 - h * math.cos(a - 0.6), y2 - h * math.sin(a - 0.6))
    p2 = (x2 - h * math.cos(a + 0.6), y2 - h * math.sin(a + 0.6))
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{s}" stroke-width="{w}" stroke-linecap="round"/>'
            f'<path d="M{p1[0]:.1f} {p1[1]:.1f} L{x2} {y2} L{p2[0]:.1f} {p2[1]:.1f}" fill="none" stroke="{s}" '
            f'stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round"/>')


def classic_svg(kind: str, color: str) -> str:
    """SVG (64x64 viewBox) of one device in the classic style."""
    lite, dark, W = _shade(color, 0.35), _shade(color, -0.25), "#ffffff"
    if kind == "router":
        body = (f'<defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="{color}"/>'
                f'<stop offset="1" stop-color="{dark}"/></linearGradient></defs>'
                f'<path d="M8 24v16c0 4.4 10.7 8 24 8s24-3.6 24-8V24" fill="url(#g)"/>'
                f'<ellipse cx="32" cy="24" rx="24" ry="8" fill="{lite}"/>'
                + _arrow(27, 21, 20, 18.5, W, 2) + _arrow(37, 27, 44, 29.5, W, 2)
                + _arrow(22, 28, 28, 26, W, 2) + _arrow(42, 20, 36, 22, W, 2))
    elif kind in ("switch", "l3switch"):
        body = (f'<path d="M6 26l10-8h42l-10 8z" fill="{lite}"/><path d="M6 26h42v16H6z" fill="{color}"/>'
                f'<path d="M48 26l10-8v16l-10 8z" fill="{dark}"/>'
                + _arrow(14, 31, 34, 31, W, 2) + _arrow(40, 37, 20, 37, W, 2))
        if kind == "l3switch":
            # "L3" lettering on the top face (drawn as strokes: QtSvg text is unreliable at icon size)
            body += (f'<path d="M24.2 19.6v4.4h3.6" fill="none" stroke="{dark}" stroke-width="1.5" '
                     f'stroke-linecap="round" stroke-linejoin="round"/>'
                     f'<path d="M30.4 19.6h3.4l-1.9 2h1.9v2.4h-3.4" fill="none" stroke="{dark}" stroke-width="1.5" '
                     f'stroke-linecap="round" stroke-linejoin="round"/>')
    elif kind == "ap":
        body = (f'<path d="M12 40l8-6h36l-8 6z" fill="{lite}"/><path d="M12 40h36v10H12z" fill="{color}"/>'
                f'<path d="M48 40l8-6v10l-8 6z" fill="{dark}"/>'
                f'<path d="M30 34V18" stroke="{dark}" stroke-width="2.6" stroke-linecap="round"/>'
                f'<circle cx="30" cy="16" r="2.6" fill="{color}"/>'
                f'<path d="M22 12a11 11 0 0 1 16 0M18 7a17 17 0 0 1 24 0" fill="none" stroke="{color}" '
                f'stroke-width="2.4" stroke-linecap="round"/>'
                + "".join(f'<circle cx="{x}" cy="45" r="1.5" fill="{W}"/>' for x in (18, 24)))
    elif kind == "laptop":
        body = (f'<path d="M15 14h34v24H15z" fill="{dark}"/><path d="M18 17h28v18H18z" fill="{color}"/>'
                f'<path d="M8 40h48l-5 8H13z" fill="{dark}"/>'
                f'<path d="M26 26a8 8 0 0 1 12 0M29 29a4 4 0 0 1 6 0" fill="none" stroke="{W}" stroke-width="1.8" '
                f'stroke-linecap="round"/><circle cx="32" cy="32" r="1.4" fill="{W}"/>')
    elif kind == "cloud":
        body = (f'<path d="M16 46h34a10 10 0 0 0 1-20 14 14 0 0 0-26-5 11 11 0 0 0-9 25z" fill="{color}"/>'
                f'<path d="M25 21a14 14 0 0 1 26 5" fill="none" stroke="{lite}" stroke-width="3" stroke-linecap="round"/>'
                + _arrow(24, 36, 40, 36, W, 2) + _arrow(42, 31, 26, 31, W, 2))
    elif kind == "firewall":                      # brick wall with a flame-orange top
        bricks = "".join(f'<path d="M{x} {y}h{w}" stroke="{W}" stroke-width="1.6"/>'
                         for x, y, w in ((14, 30, 36), (14, 38, 36), (14, 46, 36)))
        joints = "".join(f'<path d="M{x} {y}v8" stroke="{W}" stroke-width="1.6"/>'
                         for x, y in ((26, 22), (40, 22), (20, 30), (33, 30), (46, 30), (26, 38), (40, 38),
                                      (20, 46), (33, 46), (46, 46)))
        body = (f'<path d="M14 22h36v32H14z" fill="{color}"/><path d="M50 22l6-5v32l-6 5z" fill="{dark}"/>'
                f'<path d="M14 22l6-5h36l-6 5z" fill="{lite}"/>{bricks}{joints}')
    elif kind == "iot":
        body = (f'<circle cx="32" cy="34" r="14" fill="{color}"/><circle cx="32" cy="34" r="5" fill="{W}"/>'
                f'<path d="M20 16a17 17 0 0 1 24 0M24 21a11 11 0 0 1 16 0" fill="none" stroke="{dark}" '
                f'stroke-width="2.6" stroke-linecap="round"/>')
    elif kind == "wlc":
        body = (f'<path d="M8 32l8-6h40l-8 6z" fill="{lite}"/><path d="M8 32h40v14H8z" fill="{color}"/>'
                f'<path d="M48 32l8-6v14l-8 6z" fill="{dark}"/>'
                f'<path d="M20 22a13 13 0 0 1 18 0M24 26a7 7 0 0 1 10 0" fill="none" stroke="{color}" stroke-width="2.4" '
                f'stroke-linecap="round"/>' + "".join(f'<circle cx="{x}" cy="39" r="1.6" fill="{W}"/>' for x in (14, 20, 26)))
    elif kind == "phone":
        body = (f'<path d="M14 44l6-22h28l6 22z" fill="{color}"/><path d="M14 44h40v6H14z" fill="{dark}"/>'
                f'<rect x="26" y="25" width="16" height="7" rx="1" fill="{W}"/>'
                f'<path d="M10 20c0-5 6-8 10-8l3 4-3 5c-1 3 1 8 3 10l-2 4c-6-2-11-8-11-15z" fill="{dark}"/>'
                + "".join(f'<circle cx="{x}" cy="{y}" r="1.5" fill="{W}"/>' for x in (28, 34, 40) for y in (36, 40)))
    elif kind == "hub":
        body = (f'<path d="M8 30l8-6h40l-8 6z" fill="{lite}"/><path d="M8 30h40v10H8z" fill="{color}"/>'
                f'<path d="M48 30l8-6v10l-8 6z" fill="{dark}"/>'
                + "".join(f'<circle cx="{x + 2}" cy="35" r="1.8" fill="{W}"/>' for x in (14, 22, 30, 38)))
    elif kind == "server":
        body = (f'<path d="M18 12l8-5h20l-8 5z" fill="{lite}"/><path d="M18 12h20v44H18z" fill="{color}"/>'
                f'<path d="M38 12l8-5v44l-8 5z" fill="{dark}"/>'
                + "".join(f'<path d="M21 {y}h10" stroke="{W}" stroke-width="2.2" stroke-linecap="round"/>'
                          f'<circle cx="34.5" cy="{y}" r="1.5" fill="{W}"/>' for y in (20, 30, 40)))
    else:   # pc / pclinux
        if kind == "pclinux":
            screen = (f'<path d="M21 23l5 4-5 4" fill="none" stroke="{W}" stroke-width="2.2" stroke-linecap="round" '
                      f'stroke-linejoin="round"/><path d="M29 32h11" stroke="{W}" stroke-width="2.2" stroke-linecap="round"/>')
        else:
            screen = (f'<rect x="20" y="19" width="22" height="14" rx="1.2" fill="none" stroke="{W}" stroke-width="1.8"/>'
                      f'<path d="M20 23h22" stroke="{W}" stroke-width="1.8"/>'
                      f'<path d="M24 27.5h10" stroke="{W}" stroke-width="1.6" stroke-linecap="round"/>')
        body = (f'<rect x="12" y="12" width="40" height="28" rx="2.5" fill="{dark}"/>'
                f'<rect x="15" y="15" width="34" height="22" rx="1.5" fill="{color}"/>{screen}'
                f'<path d="M28 40h8l2 7H26z" fill="{dark}"/><rect x="20" y="47" width="24" height="4" rx="2" fill="{color}"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">{body}</svg>'


def icon_kind(category: str, vendor: str) -> str:
    if category == "router":
        return "router"
    if category == "switch":
        return "l3switch" if vendor == "extreme" else "switch"
    if category == "pc":
        return "pclinux" if vendor == "linux" else "pc"
    if category == "cloud":
        return "cloud"
    if category == "wireless":
        return "ap"
    return category if category in ("hub", "server", "phone") else "switch"


@lru_cache(maxsize=64)
def device_pixmap(category: str, vendor: str, scale: int = 2, kind: str | None = None) -> QPixmap:
    from PySide6.QtSvg import QSvgRenderer
    pm = QPixmap(ICON_W * scale, ICON_H * scale)
    pm.fill(Qt.transparent)
    color = VENDOR_COLORS.get(vendor, VENDOR_COLORS["generic"]).name()
    renderer = QSvgRenderer(QByteArray(classic_svg(kind or icon_kind(category, vendor), color).encode()))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(scale, scale)
    side = min(ICON_W, ICON_H)
    renderer.render(p, QRectF((ICON_W - side) / 2, (ICON_H - side) / 2, side, side))
    p.end()
    pm.setDevicePixelRatio(scale)
    return pm


# Application icon (gallery variant 1, "Topology"): three nodes joined by links with green port dots.
def _app_icon_svg() -> str:
    dots = "".join(f'<circle cx="{x}" cy="{y}" r="2.2" fill="#3fe0a4"/>'
                   for x, y in ((26, 22), (38, 22), (24.5, 30), (39.5, 30), (29, 39), (35, 39)))
    nodes = "".join(f'<circle cx="{x}" cy="{y}" r="7" fill="#fff"/><circle cx="{x}" cy="{y}" r="3" fill="#0f5a48"/>'
                    for x, y in ((20, 22), (44, 22), (32, 44)))
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect x="4" y="4" width="56" height="56" rx="14" fill="#0f5a48"/>'
            '<rect x="4" y="4" width="56" height="28" rx="14" fill="#13705a"/>'
            '<path d="M20 22L44 22M20 22L32 44M44 22L32 44" stroke="#bfe9da" stroke-width="2.6"/>'
            + dots + nodes + '</svg>')


def app_pixmap(size: int) -> QPixmap:
    from PySide6.QtSvg import QSvgRenderer
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(_app_icon_svg().encode())).render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(app_pixmap(size))
    return icon


def device_icon(category: str, vendor: str, kind: str | None = None) -> QIcon:
    return QIcon(device_pixmap(category, vendor, kind=kind))


def tool_icon(kind: str) -> QIcon:
    """Small toolbar icons."""
    s = 2
    pm = QPixmap(24 * s, 24 * s)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(s, s)
    pen = QPen(QColor("#37474f"), 2)
    p.setPen(pen)
    if kind == "note":
        p.setBrush(QColor("#fff7c2"))
        p.setPen(QPen(QColor("#c9a227"), 1.5))
        p.drawRect(QRectF(4, 4, 16, 16))
        p.setPen(QPen(QColor("#6d5d1c"), 1.4))
        for y in (9, 13, 17):
            p.drawLine(QPointF(7, y), QPointF(17, y))
        p.end()
        pm.setDevicePixelRatio(s)
        return QIcon(pm)
    if kind == "frame":
        p.setBrush(QColor(30, 136, 229, 40))
        pen = QPen(QColor("#1e88e5"), 1.8, Qt.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(QRectF(3, 5, 18, 14), 3, 3)
        p.end()
        pm.setDevicePixelRatio(s)
        return QIcon(pm)
    if kind == "select":
        p.setBrush(QColor("#eceff1"))
        p.drawPolygon(QPolygonF([QPointF(6, 3), QPointF(6, 19), QPointF(10, 15),
                                 QPointF(13, 21), QPointF(15, 20), QPointF(12, 14), QPointF(18, 14)]))
    elif kind == "cable":
        p.setPen(QPen(QColor("#90a4ae"), 2.4))
        path = QPainterPath(QPointF(3, 20))
        path.cubicTo(QPointF(10, 2), QPointF(14, 22), QPointF(21, 4))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(QPen(QColor("#37474f"), 1))
        p.setBrush(QColor("#ffb300"))
        p.drawRect(QRectF(1, 18, 5, 5))
        p.drawRect(QRectF(18, 1, 5, 5))
    elif kind == "delete":
        p.setPen(QPen(QColor("#c62828"), 3))
        p.drawLine(QPointF(5, 5), QPointF(19, 19))
        p.drawLine(QPointF(19, 5), QPointF(5, 19))
    elif kind == "pdu":
        p.setBrush(QColor("#f8bbd0"))
        p.drawRect(QRectF(3, 6, 18, 12))
        p.drawPolyline(QPolygonF([QPointF(3, 6), QPointF(12, 13), QPointF(21, 6)]))
    elif kind == "realtime":
        p.setBrush(QColor("#e3f2fd"))
        p.drawEllipse(QRectF(3, 3, 18, 18))
        p.drawLine(QPointF(12, 12), QPointF(12, 6))
        p.drawLine(QPointF(12, 12), QPointF(16, 14))
    elif kind == "simulation":
        p.setBrush(QColor("#fff3e0"))
        p.drawRoundedRect(QRectF(2, 5, 20, 14), 3, 3)
        p.setBrush(QColor("#ef6c00"))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(9, 8), QPointF(9, 16), QPointF(16, 12)]))
    p.end()
    pm.setDevicePixelRatio(s)
    return QIcon(pm)
