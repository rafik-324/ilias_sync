"""
Design System der GUI: Farb Tokens für hell und dunkel, zentrales Stylesheet.

Widgets bekommen ihr Aussehen über Rollen (dynamische Property "role" bzw.
"variant") statt über eigene Stylesheets. Dadurch passt sich alles
automatisch an, wenn das System zwischen hell und dunkel wechselt.

Basis ist der Qt Stil "Fusion", damit Windows und macOS identisch aussehen.
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from ilias_sync.gui.icons import is_dark_mode

# Nur Schriften des jeweiligen Systems nennen, fehlende Familien kosten Qt beim Start Zeit
MONO = 'Menlo, monospace' if sys.platform == "darwin" else '"Cascadia Mono", Consolas, monospace'


@dataclass(frozen=True)
class Tokens:
    bg: str
    sidebar: str
    surface: str
    surface2: str
    input: str
    border: str
    border_strong: str
    text: str
    muted: str
    faint: str
    hover: str
    pressed: str
    accent: str
    accent_hover: str
    accent_soft: str
    accent_text: str
    success: str
    success_soft: str
    warning: str
    warning_soft: str
    danger: str
    danger_soft: str
    scroll: str


LIGHT = Tokens(
    bg="#f5f6f8",
    sidebar="#eef0f3",
    surface="#ffffff",
    surface2="#f7f8fa",
    input="#ffffff",
    border="#e4e7eb",
    border_strong="#d3d8de",
    text="#1a1d21",
    muted="#646b75",
    faint="#9aa1ab",
    hover="#eceef2",
    pressed="#e1e4e9",
    accent="#2f5bd8",          # Markenblau aus dem Logo
    accent_hover="#2650c2",
    accent_soft="#e5edfc",
    accent_text="#ffffff",
    success="#15803d",
    success_soft="#e3f5e8",
    warning="#b45309",
    warning_soft="#fdf1dc",
    danger="#c62828",
    danger_soft="#fce6e6",
    scroll="#c9ced5",
)

DARK = Tokens(
    bg="#121418",
    sidebar="#0e1013",
    surface="#191c21",
    surface2="#1e2228",
    input="#15181c",
    border="#262a31",
    border_strong="#333842",
    text="#e7e9ec",
    muted="#98a0ab",
    faint="#6b727d",
    hover="#232830",
    pressed="#2b313a",
    accent="#7d9cff",          # Markenblau hell aus dem Logo
    accent_hover="#95afff",
    accent_soft="#1f2b44",
    accent_text="#0b1020",
    success="#4ade80",
    success_soft="#16301f",
    warning="#fbbf24",
    warning_soft="#33280f",
    danger="#f87171",
    danger_soft="#3a1c1c",
    scroll="#3a404a",
)


def tokens() -> Tokens:
    return DARK if is_dark_mode() else LIGHT


# ---------------- Hilfsgrafiken für das Stylesheet ----------------

def _asset_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "ilias_sync_theme"
    path.mkdir(exist_ok=True)
    return path


def _write_svg(name: str, svg: str) -> str:
    path = _asset_dir() / name
    if not path.exists() or path.read_text(encoding="utf-8") != svg:
        path.write_text(svg, encoding="utf-8")
    return path.as_posix()


def _assets(t: Tokens) -> dict[str, str]:
    check = _write_svg(
        f"check_{t.accent_text.strip('#')}.svg",
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
        f'<path d="M3.5 8.5l3 3 6-7" fill="none" stroke="{t.accent_text}" stroke-width="2.2" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>',
    )
    chevron = _write_svg(
        f"chevron_{t.muted.strip('#')}.svg",
        '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
        f'<path d="M3 4.5l3 3 3-3" fill="none" stroke="{t.muted}" stroke-width="1.6" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>',
    )
    up = _write_svg(
        f"up_{t.muted.strip('#')}.svg",
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
        f'<path d="M2.5 6.5l2.5-2.5 2.5 2.5" fill="none" stroke="{t.muted}" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>',
    )
    down = _write_svg(
        f"down_{t.muted.strip('#')}.svg",
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
        f'<path d="M2.5 3.5l2.5 2.5 2.5-2.5" fill="none" stroke="{t.muted}" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>',
    )
    return {"check": check, "chevron": chevron, "up": up, "down": down}


# ---------------- Stylesheet ----------------

def stylesheet(t: Tokens) -> str:
    a = _assets(t)
    return f"""
* {{ outline: 0; }}
QWidget {{ color: {t.text}; font-size: 13px; }}
QMainWindow, QDialog, QWizard, QWizardPage, QMessageBox {{ background: {t.bg}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QToolTip {{
    background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong};
    padding: 5px 8px; border-radius: 6px;
}}

/* Navigation */
#sidebar {{ background: {t.sidebar}; border-right: 1px solid {t.border}; }}
#brand {{ font-size: 17px; font-weight: 600; }}
#nav {{ background: transparent; border: none; }}
#nav::item {{
    color: {t.muted}; padding: 7px 10px; margin: 1px 10px; border-radius: 8px; border: none;
}}
#nav::item:hover {{ background: {t.hover}; color: {t.text}; }}
#nav::item:selected {{ background: {t.accent_soft}; color: {t.accent}; font-weight: 600; }}
#statusPill {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px; }}

/* Typografie */
QLabel[role="title"] {{ font-size: 22px; font-weight: 600; }}
QLabel[role="subtitle"] {{ color: {t.muted}; font-size: 13px; }}
QLabel[role="section"] {{ font-size: 14px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {t.muted}; }}
QLabel[role="faint"] {{ color: {t.faint}; font-size: 12px; }}
QLabel[role="error"] {{ color: {t.danger}; }}
QLabel[role="success"] {{ color: {t.success}; }}
QLabel[role="warning"] {{ color: {t.warning}; }}
QLabel[role="hero"] {{ font-size: 20px; font-weight: 600; }}
QLabel[role="stat"] {{ font-size: 22px; font-weight: 600; }}
QLabel[role="code"] {{
    font-family: {MONO}; font-size: 12px;
    background: {t.surface2}; border: 1px solid {t.border}; border-radius: 8px; padding: 10px 12px;
}}
QLabel[role="banner"] {{
    background: {t.warning_soft}; color: {t.text}; border-radius: 8px; padding: 9px 12px;
}}
QLabel[role="banner-error"] {{
    background: {t.danger_soft}; color: {t.text}; border-radius: 8px; padding: 9px 12px;
}}

/* Karten */
QFrame#card, QFrame[role="card"] {{
    background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px;
}}
QFrame[role="card"] QLabel, QFrame#card QLabel {{ background: transparent; }}
QGroupBox {{
    background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px;
    margin-top: 30px; padding: 14px 14px 10px 14px; font-size: 14px; font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left; left: 2px; top: 2px;
    padding: 0 0 6px 0; color: {t.text};
}}
QGroupBox QLabel {{ font-weight: 400; font-size: 13px; background: transparent; }}
QGroupBox > QWidget {{ font-weight: 400; }}

/* Buttons */
QPushButton {{
    background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong};
    border-radius: 8px; padding: 6px 14px; min-height: 18px; font-weight: 500;
}}
QPushButton:hover {{ background: {t.hover}; }}
QPushButton:pressed {{ background: {t.pressed}; }}
QPushButton:disabled {{ color: {t.faint}; background: {t.surface2}; border-color: {t.border}; }}
QPushButton[variant="primary"] {{
    background: {t.accent}; color: {t.accent_text}; border: 1px solid {t.accent}; font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
QPushButton[variant="primary"]:disabled {{
    background: {t.accent_soft}; border-color: {t.accent_soft}; color: {t.faint};
}}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; color: {t.muted}; }}
QPushButton[variant="ghost"]:hover {{ background: {t.hover}; color: {t.text}; }}
QPushButton[variant="danger"] {{ color: {t.danger}; }}
QPushButton[variant="danger"]:hover {{ background: {t.danger_soft}; }}
QToolButton {{ background: transparent; border: none; border-radius: 6px; padding: 4px 6px; color: {t.muted}; }}
QToolButton:hover {{ background: {t.hover}; color: {t.text}; }}
QToolButton:checked {{ color: {t.text}; }}

/* Eingaben */
QLineEdit, QSpinBox, QComboBox {{ min-height: 20px; }}
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {{
    background: {t.input}; border: 1px solid {t.border_strong}; border-radius: 8px;
    padding: 5px 9px; selection-background-color: {t.accent}; selection-color: {t.accent_text};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {t.accent}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {t.faint}; background: {t.surface2}; }}
QLineEdit[readOnly="true"] {{ background: {t.surface2}; color: {t.muted}; }}
QComboBox {{ padding-right: 26px; }}
QComboBox::drop-down {{ border: none; width: 24px; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{a['chevron']}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: 8px; padding: 4px;
    selection-background-color: {t.accent_soft}; selection-color: {t.text}; outline: 0;
}}
QSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border; width: 20px; border: none; background: transparent;
}}
QSpinBox::up-button {{ subcontrol-position: top right; margin: 3px 3px 0 0; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; margin: 0 3px 3px 0; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {t.hover}; border-radius: 4px; }}
QSpinBox::up-arrow {{ image: url("{a['up']}"); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url("{a['down']}"); width: 10px; height: 10px; }}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 5px; border: 1px solid {t.border_strong}; background: {t.input};
}}
QCheckBox::indicator:hover {{ border-color: {t.accent}; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; image: url("{a['check']}"); }}
QCheckBox::indicator:disabled {{ background: {t.surface2}; border-color: {t.border}; }}

/* Listen, Tabellen, Bäume */
QTableWidget, QTreeWidget, QListWidget {{
    background: {t.surface}; border: none; gridline-color: transparent;
    alternate-background-color: {t.surface2};
    selection-background-color: {t.accent_soft}; selection-color: {t.text};
}}
QListWidget#plainList {{ border: 1px solid {t.border}; border-radius: 8px; padding: 4px; }}
QListWidget#plainList::item {{ padding: 5px 6px; border-radius: 6px; }}
QListWidget#plainList::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}
QTableView::item {{ padding: 0 8px; border: none; }}
QTreeView::item {{ padding: 0 4px; border: none; min-height: 28px; }}
QTableView::item:selected, QTreeView::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}
QTreeView::item:hover, QTableView::item:hover {{ background: {t.hover}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {t.surface}; color: {t.muted}; border: none; border-bottom: 1px solid {t.border};
    padding: 8px 8px; font-size: 12px; font-weight: 600;
}}
QTableCornerButton::section {{ background: {t.surface}; border: none; }}

QPlainTextEdit#logView {{
    background: transparent; border: none; padding: 8px 10px;
    font-family: {MONO}; font-size: 12px;
}}

/* Fortschritt */
QProgressBar {{ background: {t.surface2}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}

/* Scrollbars */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.scroll}; border-radius: 3px; min-height: 32px; margin: 0 2px; }}
QScrollBar::handle:horizontal {{ background: {t.scroll}; border-radius: 3px; min-width: 32px; margin: 2px 0; }}
QScrollBar::handle:hover {{ background: {t.faint}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* Menüs (Tray unter Windows, Kontextmenüs) */
QMenu {{ background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 6px; background: transparent; }}
QMenu::item:selected {{ background: {t.hover}; }}
QMenu::item:disabled {{ color: {t.faint}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 5px 8px; }}
QMenu::right-arrow {{ image: url("{a['chevron']}"); width: 12px; height: 12px; }}
"""


def palette(t: Tokens) -> QPalette:
    p = QPalette()
    role = QPalette.ColorRole
    p.setColor(role.Window, QColor(t.bg))
    p.setColor(role.WindowText, QColor(t.text))
    p.setColor(role.Base, QColor(t.surface))
    p.setColor(role.AlternateBase, QColor(t.surface2))
    p.setColor(role.Text, QColor(t.text))
    p.setColor(role.Button, QColor(t.surface))
    p.setColor(role.ButtonText, QColor(t.text))
    p.setColor(role.Highlight, QColor(t.accent))
    p.setColor(role.HighlightedText, QColor(t.accent_text))
    p.setColor(role.ToolTipBase, QColor(t.surface))
    p.setColor(role.ToolTipText, QColor(t.text))
    p.setColor(role.PlaceholderText, QColor(t.faint))
    p.setColor(role.Link, QColor(t.accent))
    p.setColor(role.Mid, QColor(t.border_strong))
    for r in (role.WindowText, role.Text, role.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, r, QColor(t.faint))
    return p


def _font() -> QFont:
    font = QApplication.font()
    if sys.platform == "win32":
        font.setFamilies(["Segoe UI Variable Text", "Segoe UI"])
    font.setPixelSize(13)
    return font


def apply(app: QApplication) -> None:
    """Wendet Stil, Palette, Schrift und Stylesheet an und folgt dem Systemmodus."""
    app.setStyle("Fusion")
    app.setFont(_font())
    _refresh(app)
    if not app.property("themeConnected"):
        QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: _refresh(app))
        app.setProperty("themeConnected", True)


def _refresh(app: QApplication) -> None:
    t = tokens()
    app.setPalette(palette(t))
    app.setStyleSheet(stylesheet(t))


# ---------------- Rollen ----------------

def set_role(widget: QWidget, role: str | None, prop: str = "role") -> QWidget:
    """Setzt die Stil Rolle eines Widgets und aktualisiert die Darstellung."""
    widget.setProperty(prop, role)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
    return widget


def set_variant(button: QWidget, variant: str | None) -> QWidget:
    return set_role(button, variant, "variant")
