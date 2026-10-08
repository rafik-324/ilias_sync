"""
Icons der App.

Logo (Richtung 1b aus dem Design Handoff): das "i" als Download Symbol, Punkt oben,
Stamm als Pfeil nach unten. Die finalen SVGs liegen in ``assets/`` und werden für
App Icon und Logo in der Seitenleiste direkt gerendert. Das Tray Icon zeichnet
denselben Glyph per QPainter nach (Geometrie aus der README, viewBox 48 × 72),
weil es Zustände (Badge) und eine Pfeil Animation beim Sync braucht. Auf macOS sieht
es aus wie das App Icon (Quadrat mit Schloss, Wunsch des Nutzers 2026-10-08), auf
Windows wird der farbige Glyph verwendet.
"""

from __future__ import annotations

import sys
from enum import Enum
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer

IS_MAC = sys.platform == "darwin"

# Liegt im PyInstaller Bundle unter ilias_sync/gui/assets, __file__ zeigt dort hinein
ASSETS = Path(__file__).resolve().parent / "assets"
ASSET_FILES = ("icon_light.svg", "icon_dark.svg", "app_icon.svg", "app_icon_dark.svg",
               "app_icon_schloss.png", "app_icon_schloss_dark.png", "schloss_lines.png", "schloss_lines_dark.png")
# App Icon 5a: Glyph vor dem Mannheimer Schloss (fertig gerendert, 1024 px). Unter 32 px
# wird das Schloss zu Rauschen, dort nur der Glyph (Vorgabe aus dem Handoff)
SCHLOSS_MIN = 32

# Design Tokens aus dem Handoff (nur Logo und Akzent)
BLAU = "#2F5BD8"
BLAU_HELL = "#7D9CFF"      # Blau im dunklen Modus
INK = "#141416"
WEISS = "#FFFFFF"

FARBE_FEHLER = QColor("#d13438")
FARBE_LOGIN = QColor("#e8912d")
FARBE_PAUSE = QColor("#7a7a7a")
FARBE_PAUSE_DUNKEL = QColor("#b4b4b8")   # auf dunklem Infobereich sonst kaum sichtbar

# Pfeil Animation: 900 ms Zyklus laut README, 10 Bilder zu je 90 ms
SYNC_FRAMES = 10
SYNC_FRAME_MS = 90
_SIZES = (16, 18, 20, 22, 24, 32, 36, 44, 48, 64)


class IconState(Enum):
    READY = "ready"
    SYNCING = "syncing"
    ERROR = "error"
    LOGIN = "login"
    PAUSED = "paused"


def is_dark_mode() -> bool:
    app = QGuiApplication.instance()
    if app is None:
        return False
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def asset_path(name: str) -> Path:
    return ASSETS / name


# ---------------- SVG Logo ----------------

def _render_svg(name: str, width: int, height: int) -> QPixmap:
    renderer = QSvgRenderer(str(asset_path(name)))
    pm = QPixmap(width, height)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, width, height))
    p.end()
    return pm


@lru_cache(maxsize=None)
def app_icon(dark: bool = False) -> QIcon:
    """App Icon für Fenster, Dialoge, QApplication und Dock: ab 32 px mit Schloss, darunter nur der Glyph."""
    svg = "app_icon_dark.svg" if dark else "app_icon.svg"
    schloss = QPixmap(str(asset_path("app_icon_schloss_dark.png" if dark else "app_icon_schloss.png")))
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256, 512, 1024):
        if size < SCHLOSS_MIN or schloss.isNull():
            icon.addPixmap(_render_svg(svg, size, size))
        else:
            icon.addPixmap(schloss.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
    return icon


def brand_pixmap(height: int, dark: bool | None = None) -> QPixmap:
    """Glyph für die Seitenleiste (Seitenverhältnis 2:3), scharf auf Retina."""
    if dark is None:
        dark = is_dark_mode()
    ratio = 2
    width = round(height * 48 / 72)
    pm = _render_svg("icon_dark.svg" if dark else "icon_light.svg", width * ratio, height * ratio)
    pm.setDevicePixelRatio(ratio)
    return pm


def brand_app_pixmap(size: int, dark: bool | None = None) -> QPixmap:
    """App Icon mit Schloss für die Seitenleiste (Wunsch des Nutzers 2026-10-08), scharf auf Retina."""
    if dark is None:
        dark = is_dark_mode()
    ratio = 2
    img = QPixmap(str(asset_path("app_icon_schloss_dark.png" if dark else "app_icon_schloss.png")))
    pm = img.scaled(size * ratio, size * ratio, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
    pm.setDevicePixelRatio(ratio)
    return pm


# ---------------- Tray Glyph ----------------

def _smooth(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def sync_pose(frame: int) -> tuple[float, float, float]:
    """(Pfeil y, Pfeil Deckkraft, Punkt y) für ein Bild der Animation, in viewBox Einheiten.

    Pfeil: fährt nach unten aus (12, ausblenden), springt nach oben (−14) und fährt ein.
    Punkt: hüpft synchron (−5, dann +1, dann Ruhe). Werte aus der README.
    """
    t = (frame % SYNC_FRAMES) / SYNC_FRAMES
    if t <= 0.4:
        u = _smooth(t / 0.4)
        arrow_y, arrow_op = 12 * u, 1 - u
    else:
        u = _smooth((t - 0.41) / 0.59)
        arrow_y, arrow_op = -14 * (1 - u), u
    if t <= 0.3:
        dot_y = -5 * _smooth(t / 0.3)
    elif t <= 0.55:
        dot_y = -5 + 6 * _smooth((t - 0.3) / 0.25)
    else:
        dot_y = 1 - _smooth((t - 0.55) / 0.45)
    return arrow_y, arrow_op, dot_y


# Standbild für den Sync, wenn "Bewegung reduzieren" aktiv ist: Pfeil halb ausgefahren
STATIC_SYNC_POSE = (6.0, 0.55, -2.0)


def _draw_glyph(p: QPainter, dot: QColor, arrow: QColor, pose: tuple[float, float, float] = (0, 1, 0)) -> None:
    """Glyph im 48 × 72 Koordinatensystem (Geometrie aus assets/icon_light.svg)."""
    arrow_y, arrow_op, dot_y = pose
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(dot)
    p.drawEllipse(QPointF(24, 10 + dot_y), 8, 8)
    if arrow_op <= 0.01:
        return
    color = QColor(arrow)
    color.setAlphaF(color.alphaF() * arrow_op)
    p.setPen(QPen(color, 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(24, 26 + arrow_y)
    path.lineTo(24, 60 + arrow_y)
    path.moveTo(12, 48 + arrow_y)
    path.lineTo(24, 60 + arrow_y)
    path.lineTo(36, 48 + arrow_y)
    p.drawPath(path)


def _draw_badge(p: QPainter, size: int, state: IconState, fill: QColor) -> None:
    """Zustand unten rechts, in Form unterscheidbar (auch einfarbig in der macOS Menüleiste):
    Fehler = voller Punkt, Login nötig = Ring, Pausiert = zwei Balken."""
    center = QPointF(size * 0.78, size * 0.76)
    r = size * 0.2
    p.save()
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(Qt.GlobalColor.black))
    p.drawEllipse(center, r + size * 0.08, r + size * 0.08)
    p.restore()
    if state == IconState.ERROR:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawEllipse(center, r, r)
    elif state == IconState.LOGIN:
        w = max(1.5, size * 0.09)
        p.setPen(QPen(fill, w))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(center, r - w / 2, r - w / 2)
    elif state == IconState.PAUSED:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        bw, bh = r * 0.62, r * 1.9
        p.drawRoundedRect(QRectF(center.x() - r * 0.85, center.y() - bh / 2, bw, bh), bw / 3, bw / 3)
        p.drawRoundedRect(QRectF(center.x() + r * 0.23, center.y() - bh / 2, bw, bh), bw / 3, bw / 3)


@lru_cache(maxsize=None)
def _schloss_lines(dark: bool, width: int) -> QImage:
    """Schloss Linien, vorher glatt verkleinert (drawImage allein wirkt bei kleinen Größen pixelig)."""
    img = QImage(str(asset_path("schloss_lines_dark.png" if dark else "schloss_lines.png")))
    return img.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)


def _render_app_style(size: int, state: IconState, frame: int, dark: bool, animate: bool) -> QPixmap:
    """Menüleiste im Stil des App Icons (Wunsch des Nutzers 2026-10-08): abgerundetes Quadrat,
    Schloss Linien, farbiger Glyph. Maße laut Handoff relativ zu 128 × 128."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    u = size / 128
    square = QPainterPath()
    square.addRoundedRect(QRectF(0, 0, size, size), 32 * u, 32 * u)
    p.save()
    p.setClipPath(square)   # Pfeil der Animation und Schloss werden am Rand abgeschnitten
    p.fillPath(square, QColor(INK) if dark else QColor(WEISS))
    if size >= 24:          # darunter wären die Linien nur Rauschen
        p.setOpacity(0.40 if dark else 0.45)
        p.drawImage(QPointF(-56 * u, 46 * u), _schloss_lines(dark, round(240 * u)))
        p.setOpacity(1.0)
    pose = (0.0, 1.0, 0.0)
    if state == IconState.SYNCING:
        pose = sync_pose(frame) if animate else STATIC_SYNC_POSE
    p.translate(42 * u, 22 * u)
    p.scale(44 * u / 48, 66 * u / 72)
    dot, arrow = (QColor(BLAU_HELL), QColor(WEISS)) if dark else (QColor(BLAU), QColor(INK))
    _draw_glyph(p, dot, arrow, pose)
    p.restore()
    if state in (IconState.ERROR, IconState.LOGIN, IconState.PAUSED):
        pause = FARBE_PAUSE_DUNKEL if dark else FARBE_PAUSE
        fill = {IconState.ERROR: FARBE_FEHLER, IconState.LOGIN: FARBE_LOGIN, IconState.PAUSED: pause}[state]
        _draw_badge(p, size, state, fill)
    p.end()
    return pm


def _render(size: int, state: IconState, frame: int, template: bool, dark: bool, animate: bool) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    if template:
        dot = arrow = QColor(Qt.GlobalColor.black)
    elif dark:
        dot, arrow = QColor(BLAU_HELL), QColor(WEISS)
    else:
        dot, arrow = QColor(BLAU), QColor(INK)

    pose = (0.0, 1.0, 0.0)
    if state == IconState.SYNCING:
        pose = sync_pose(frame) if animate else STATIC_SYNC_POSE

    badge = state in (IconState.ERROR, IconState.LOGIN, IconState.PAUSED)
    scale = size * 0.94 / 72
    x = (size - 48 * scale) / 2 - (size * 0.1 if badge else 0)
    p.save()
    p.translate(x, size * 0.03)
    p.scale(scale, scale)
    _draw_glyph(p, dot, arrow, pose)
    p.restore()

    if badge:
        if template:
            fill = QColor(Qt.GlobalColor.black)
        else:
            pause = FARBE_PAUSE_DUNKEL if dark else FARBE_PAUSE
            fill = {IconState.ERROR: FARBE_FEHLER, IconState.LOGIN: FARBE_LOGIN, IconState.PAUSED: pause}[state]
        _draw_badge(p, size, state, fill)
    p.end()
    return pm


@lru_cache(maxsize=None)
def _tray_icon_cached(state: IconState, frame: int, app_style: bool, dark: bool, animate: bool) -> QIcon:
    icon = QIcon()
    for size in _SIZES:
        if app_style:
            icon.addPixmap(_render_app_style(size, state, frame, dark, animate))
        else:
            icon.addPixmap(_render(size, state, frame, False, dark, animate))
    return icon


def tray_icon(state: IconState, frame: int = 0, dark: bool | None = None, animate: bool = True) -> QIcon:
    """Icon für Tray bzw. Menüleiste. macOS: im Stil des App Icons (farbig, kein Template Image),
    Windows: farbiger Glyph. Bilder werden zwischengespeichert, damit die Animation keine
    Dauerlast erzeugt."""
    if dark is None:
        dark = is_dark_mode()
    return _tray_icon_cached(state, frame % SYNC_FRAMES, IS_MAC, dark, animate)


# ---------------- Navigation und Status (modernes Layout) ----------------

def _line_pen(color: QColor, width: float = 1.8) -> QPen:
    return QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)


def _draw_nav(p: QPainter, name: str, color: QColor) -> None:
    """Linien Icons im 24er Koordinatensystem."""
    p.setPen(_line_pen(color))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if name == "overview":
        path = QPainterPath()
        path.moveTo(4, 11)
        path.lineTo(12, 4.5)
        path.lineTo(20, 11)
        p.drawPath(path)
        p.drawRoundedRect(QRectF(6.5, 10, 11, 9.5), 1.5, 1.5)
    elif name == "courses":
        path = QPainterPath()
        path.moveTo(3.5, 7)
        path.lineTo(3.5, 18)
        path.quadTo(3.5, 19.5, 5, 19.5)
        path.lineTo(19, 19.5)
        path.quadTo(20.5, 19.5, 20.5, 18)
        path.lineTo(20.5, 9.5)
        path.quadTo(20.5, 8, 19, 8)
        path.lineTo(11.5, 8)
        path.lineTo(9.5, 5.5)
        path.lineTo(5, 5.5)
        path.quadTo(3.5, 5.5, 3.5, 7)
        p.drawPath(path)
    elif name == "preview":
        path = QPainterPath()
        path.moveTo(2.5, 12)
        path.quadTo(12, 3, 21.5, 12)
        path.quadTo(12, 21, 2.5, 12)
        p.drawPath(path)
        p.drawEllipse(QPointF(12, 12), 3, 3)
    elif name == "settings":
        for y, x in ((7, 15), (12, 8), (17, 13)):
            p.drawLine(QPointF(4, y), QPointF(20, y))
            p.setBrush(color)
            p.drawEllipse(QPointF(x, y), 2.2, 2.2)
            p.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "log":
        p.drawRoundedRect(QRectF(5, 3.5, 14, 17), 2, 2)
        for y in (8.5, 12, 15.5):
            p.drawLine(QPointF(8.5, y), QPointF(15.5, y))


def _nav_pixmap(name: str, color: QColor, size: int) -> QPixmap:
    ratio = 2
    pm = QPixmap(size * ratio, size * ratio)
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24, size / 24)
    _draw_nav(p, name, color)
    p.end()
    return pm


def nav_icon(name: str, normal: str, selected: str, size: int = 18) -> QIcon:
    """Navigations Icon mit eigener Farbe für den ausgewählten Zustand."""
    icon = QIcon()
    icon.addPixmap(_nav_pixmap(name, QColor(normal), size), QIcon.Mode.Normal)
    icon.addPixmap(_nav_pixmap(name, QColor(selected), size), QIcon.Mode.Selected)
    icon.addPixmap(_nav_pixmap(name, QColor(selected), size), QIcon.Mode.Active)
    return icon


def status_badge(state: IconState, fg: str, bg: str, size: int = 44, frame: int = 0) -> QPixmap:
    """Rundes Status Symbol für die Übersicht (Hintergrund soft, Zeichen farbig)."""
    ratio = 2
    pm = QPixmap(size * ratio, size * ratio)
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24, size / 24)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(bg))
    p.drawEllipse(QRectF(0, 0, 24, 24))
    color = QColor(fg)
    p.setPen(_line_pen(color, 2.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if state == IconState.READY:
        path = QPainterPath()
        path.moveTo(7.5, 12.5)
        path.lineTo(10.5, 15.5)
        path.lineTo(16.5, 9)
        p.drawPath(path)
    elif state == IconState.SYNCING:
        # rotierender Bogen
        p.drawArc(QRectF(7, 7, 10, 10), (90 - frame * 45) * 16, 270 * 16)
    elif state == IconState.ERROR:
        p.drawLine(QPointF(12, 7.5), QPointF(12, 13))
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(12, 16.3), 1.3, 1.3)
    elif state == IconState.LOGIN:
        p.drawRoundedRect(QRectF(8, 11, 8, 6.5), 1.2, 1.2)
        p.drawArc(QRectF(9.5, 6.5, 5, 8), 0, 180 * 16)
    elif state == IconState.PAUSED:
        p.drawLine(QPointF(10, 8.5), QPointF(10, 15.5))
        p.drawLine(QPointF(14, 8.5), QPointF(14, 15.5))
    p.end()
    return pm


def dot(color: str, size: int = 8) -> QPixmap:
    ratio = 2
    pm = QPixmap(size * ratio, size * ratio)
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(0, 0, size, size))
    p.end()
    return pm
