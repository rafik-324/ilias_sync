"""
Einstieg der GUI.

Entwicklung ohne ILIAS Zugang:
  python -m ilias_sync.gui.app --fake            normaler Start mit Beispieldaten
  python -m ilias_sync.gui.app --fake-setup      Start mit Setup Wizard
  python -m ilias_sync.gui.app --fake --fail offline,sync_errors
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from ilias_sync import __version__
from ilias_sync.gui import theme
from ilias_sync.gui.controller import Controller
from ilias_sync.gui.icons import app_icon
from ilias_sync.gui.scheduler_gui import Scheduler
from ilias_sync.gui.setup_wizard import SetupWizard
from ilias_sync.gui.tray import ZIEL_UEBERSICHT, Tray
from ilias_sync.gui.widgets import LoginDialog
from ilias_sync.gui.window import MainWindow

log = logging.getLogger("ilias_sync.gui")

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup_logging(api) -> None:
    """Rotierende Logdatei im Log Verzeichnis des Backends."""
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    path = api.log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT, "%Y-%m-%d %H:%M:%S"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return f"ilias-sync-{user}"


class SingleInstance:
    """Stellt sicher, dass nur eine Instanz läuft. Die zweite weckt die erste."""

    def __init__(self, on_wake) -> None:
        self.name = _server_name()
        self.server: QLocalServer | None = None
        self._on_wake = on_wake

    def already_running(self) -> bool:
        sock = QLocalSocket()
        sock.connectToServer(self.name)
        if sock.waitForConnected(300):
            sock.write(b"show")
            sock.waitForBytesWritten(300)
            sock.disconnectFromServer()
            return True
        return False

    def listen(self) -> None:
        QLocalServer.removeServer(self.name)   # Reste nach Absturz entfernen
        self.server = QLocalServer()
        self.server.newConnection.connect(self._on_connection)
        self.server.listen(self.name)

    def _on_connection(self) -> None:
        conn = self.server.nextPendingConnection()
        if conn is not None:
            conn.disconnected.connect(conn.deleteLater)
        self._on_wake()


class GuiApp:
    """Verbindet Controller, Tray, Fenster, Scheduler und Wizard."""

    def __init__(self, api) -> None:
        self.api = api
        self.controller = Controller(api)
        self.tray = Tray(self.controller)
        self.scheduler = Scheduler(self.controller)
        self.window: MainWindow | None = None
        self.wizard: SetupWizard | None = None
        self._login_dialog: LoginDialog | None = None
        self._quitting = False

        self.tray.open_window.connect(self.show_window)
        self.tray.open_login.connect(self.show_login)
        self.tray.quit_requested.connect(self.quit)
        self.controller.login_required.connect(self._on_login_required)

    def start(self, show_window: bool = False) -> None:
        self.tray.show()
        if not self.api.config_exists():
            self.show_wizard()
        else:
            self._start_normal()
            if show_window:
                self.show_window()

    def _start_normal(self) -> None:
        self.controller.check_session()
        self.scheduler.start()

    # ---------------- Fenster ----------------

    def _ensure_window(self) -> MainWindow:
        if self.window is None:
            self.window = MainWindow(self.controller)
            self.window.login_requested.connect(self.show_login)
        return self.window

    def show_window(self, tab: str = ZIEL_UEBERSICHT) -> None:
        if self.wizard is not None:
            self.wizard.showNormal()
            self.wizard.raise_()
            self.wizard.activateWindow()
            return
        if tab == "login":
            self.show_login()
            return
        self._ensure_window().show_tab(tab)

    def wake(self) -> None:
        """Zweiter Programmstart: vorhandenes Fenster nach vorne holen."""
        self.show_window()

    def show_wizard(self) -> None:
        self.wizard = SetupWizard(self.api)
        self.wizard.accepted.connect(self._wizard_done)
        self.wizard.rejected.connect(self.quit)
        self.wizard.show()

    def _wizard_done(self) -> None:
        start = self.wizard.start_sync_requested() if self.wizard else False
        self.wizard = None
        self.controller.logged_in()
        self._start_normal()
        self.show_window()
        if start:
            self.controller.start_sync()

    def show_login(self) -> None:
        if self._login_dialog is not None:
            self._login_dialog.raise_()
            self._login_dialog.activateWindow()
            return
        dlg = LoginDialog(self.api, self.api.stored_username(), self.window)
        self._login_dialog = dlg
        if dlg.exec():
            self.controller.logged_in()
        self._login_dialog = None

    def _on_login_required(self) -> None:
        # Nur einen Dialog zeigen, wenn der Nutzer gerade mit dem Fenster arbeitet.
        # Sonst genügt die Benachrichtigung aus dem Tray.
        if self.window is not None and self.window.isVisible() and self.window.isActiveWindow():
            QTimer.singleShot(0, self.show_login)

    # ---------------- Beenden ----------------

    def quit(self) -> None:
        if self._quitting:
            return
        if self.controller.busy:
            answer = QMessageBox.question(
                None, "ILIAS Sync beenden",
                "Gerade läuft eine Synchronisierung. Sie wird abgebrochen. Trotzdem beenden?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._quitting = True
        log.info("Beenden")
        self.scheduler.stop()
        self.controller.shutdown()
        self.tray.hide()
        QApplication.quit()


def run(api, argv: list[str] | None = None, show_window: bool = False) -> int:
    """Startet die GUI und gibt den Exit Code zurück."""
    QCoreApplication.setOrganizationName("ILIAS Sync")
    QCoreApplication.setApplicationName("ILIAS Sync")
    QCoreApplication.setApplicationVersion(__version__)
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())
    theme.apply(app)

    holder: dict[str, GuiApp] = {}
    single = SingleInstance(lambda: holder["gui"].wake() if "gui" in holder else None)
    if single.already_running():
        return 0
    single.listen()

    setup_logging(api)
    log.info("ILIAS Sync %s gestartet", __version__)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "ILIAS Sync", "Auf diesem System ist kein Infobereich bzw. keine Menüleiste verfügbar.")
        return 1

    gui = GuiApp(api)
    holder["gui"] = gui
    gui.start(show_window)
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ilias_sync.gui.app", description="ILIAS Sync GUI")
    parser.add_argument("--fake", action="store_true", help="mit Fake Backend starten (Beispieldaten)")
    parser.add_argument("--fake-setup", action="store_true", help="Fake Backend, Start mit Setup Wizard")
    parser.add_argument("--fail", default="", help="Fehlerfälle im Fake Backend, z. B. offline,sync_errors")
    parser.add_argument("--fenster", action="store_true", help="Hauptfenster beim Start öffnen")
    parser.add_argument("--delay", type=float, default=0.05, help="Verzögerung im Fake Backend in Sekunden")
    args = parser.parse_args(argv)

    if args.fake or args.fake_setup:
        from ilias_sync.gui.fake_backend import FakeApi

        fail = {x.strip() for x in args.fail.split(",") if x.strip()}
        api = FakeApi(first_run=args.fake_setup, fail=fail, delay=args.delay)
    else:
        from ilias_sync.api import Api

        api = Api()
    # Im Fake Modus immer mit Fenster starten, sonst ist unter Windows 11 nichts zu sehen
    return run(api, show_window=args.fenster or args.fake or args.fake_setup)


if __name__ == "__main__":
    sys.exit(main())
