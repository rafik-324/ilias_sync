"""
Icons der App, per QPainter erzeugt (Platzhalter bis finale Icons existieren).

Motiv: Pfeil nach unten in eine Ablage (Download). Zustände werden über ein
Abzeichen unten rechts dargestellt. Auf macOS entstehen einfarbige Template
Images (setIsMask), die das System an hell und dunkel anpasst. Auf Windows
werden farbige Varianten für hellen und dunklen Infobereich erzeugt.
"""

from __future__ import annotations

import sys
from enum import Enum

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap

IS_MAC = sys.platform == "darwin"

UNI_BLAU = QColor("#003056")
FARBE_FEHLER = QColor("#d13438")
FARBE_LOGIN = QColor("#e8912d")
FARBE_PAUSE = QColor("#7a7a7a")
FARBE_SYNC = QColor("#2b88d8")

SYNC_FRAMES = 4
_SIZES = (16, 20, 24, 32, 48, 64)


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


def _draw_glyph(p: QPainter, color: QColor, arrow_offset: float = 0.0) -> None:
    """Zeichnet Pfeil und Ablage im 64er Koordinatensystem."""
    pen = QPen(color, 6.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # Ablage
    tray = QPainterPath()
    tray.moveTo(10, 38)
    tray.lineTo(10, 54)
    tray.lineTo(54, 54)
    tray.lineTo(54, 38)
    p.drawPath(tray)
    # Pfeil
    y = arrow_offset
    p.drawLine(QPointF(32, 6 + y), QPointF(32, 36 + y))
    head = QPainterPath()
    head.moveTo(20, 25 + y)
    head.lineTo(32, 37 + y)
    head.lineTo(44, 25 + y)
    p.drawPath(head)


def _clear_badge_area(p: QPainter) -> None:
    p.save()
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(Qt.GlobalColor.black))
    p.drawEllipse(QPointF(49, 49), 17, 17)
    p.restore()


def _draw_badge(p: QPainter, state: IconState, fill: QColor, mark: QColor | None) -> None:
    """Abzeichen unten rechts. mark=None bedeutet: Zeichen ausstanzen (Template)."""
    _clear_badge_area(p)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fill)
    p.drawEllipse(QPointF(49, 49), 14, 14)

    p.save()
    if mark is None:
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        mark = QColor(Qt.GlobalColor.black)
    p.setBrush(mark)
    if state == IconState.ERROR:
        p.drawRoundedRect(QRectF(46, 38, 6, 14), 2.5, 2.5)
        p.drawEllipse(QPointF(49, 57), 3.2, 3.2)
    elif state == IconState.PAUSED:
        p.drawRect(QRectF(42.5, 41, 5, 16))
        p.drawRect(QRectF(50.5, 41, 5, 16))
    elif state == IconState.LOGIN:
        # Schloss
        p.drawRoundedRect(QRectF(41, 47, 16, 11), 2, 2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(mark, 3.2))
        p.drawArc(QRectF(44, 39, 10, 14), 0, 180 * 16)
    p.restore()


def _render(size: int, state: IconState, frame: int, template: bool, dark: bool) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 64, size / 64)

    if template:
        glyph = QColor(Qt.GlobalColor.black)
    else:
        glyph = QColor("#f3f3f3") if dark else QColor("#1f1f1f")

    offset = 0.0
    if state == IconState.SYNCING:
        offset = (frame % SYNC_FRAMES) * 3.0 - 3.0
        if not template:
            glyph = FARBE_SYNC if not dark else QColor("#6cb8f6")
    _draw_glyph(p, glyph, offset)

    if state in (IconState.ERROR, IconState.LOGIN, IconState.PAUSED):
        if template:
            _draw_badge(p, state, QColor(Qt.GlobalColor.black), None)
        else:
            fill = {IconState.ERROR: FARBE_FEHLER, IconState.LOGIN: FARBE_LOGIN, IconState.PAUSED: FARBE_PAUSE}[state]
            _draw_badge(p, state, fill, QColor(Qt.GlobalColor.white))
    p.end()
    return pm


def tray_icon(state: IconState, frame: int = 0, dark: bool | None = None) -> QIcon:
    """Icon für Tray bzw. Menüleiste im passenden Stil der Plattform."""
    template = IS_MAC
    if dark is None:
        dark = is_dark_mode()
    icon = QIcon()
    for size in _SIZES:
        icon.addPixmap(_render(size, state, frame, template, dark))
    if template:
        icon.setIsMask(True)
    return icon


def app_icon() -> QIcon:
    """Farbiges App Icon für Fenster und Dialoge."""
    icon = QIcon()
    for size in (16, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(size / 64, size / 64)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(UNI_BLAU)
        p.drawRoundedRect(QRectF(2, 2, 60, 60), 14, 14)
        p.translate(10, 9)
        p.scale(44 / 64, 44 / 64)
        _draw_glyph(p, QColor(Qt.GlobalColor.white))
        p.end()
        icon.addPixmap(pm)
    return icon


def status_pixmap(state: IconState, size: int = 20) -> QPixmap:
    """Farbiges Status Symbol für das Fenster (unabhängig von der Plattform)."""
    return _render(size, state, 0, False, is_dark_mode())
