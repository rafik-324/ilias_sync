"""
Intervall Sync.

Statt eines langen Einmal Timers prüft ein kurzer Takt, ob der nächste Sync
fällig ist. Dadurch wird nach dem Ruhezustand automatisch zeitnah
synchronisiert, wenn das Intervall inzwischen überschritten ist.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QTimer

from ilias_sync.api import Config
from ilias_sync.gui.controller import Controller

TICK_MS = 30_000
START_DELAY = timedelta(minutes=1)


def next_due(last_end: datetime | None, interval_minutes: int, started_at: datetime) -> datetime:
    """Zeitpunkt des nächsten automatischen Syncs."""
    first = started_at + START_DELAY
    if last_end is None:
        return first
    return max(first, last_end + timedelta(minutes=interval_minutes))


class Scheduler(QObject):
    def __init__(self, controller: Controller, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.started_at = datetime.now()
        self._last_end: datetime | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        controller.sync_finished.connect(self._on_sync_finished)
        controller.error.connect(lambda *_: self._mark_done())
        controller.config_changed.connect(self._recompute)
        self._recompute()

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _interval(self) -> int:
        cfg = self.controller.config
        # ohne Config der Standard aus dem Vertrag, keine eigene Zahl
        return (cfg or Config(base_dir=None)).sync_interval_minutes

    def _recompute(self) -> None:
        self.controller.next_sync_at = next_due(self._last_end, self._interval(), self.started_at)

    def _mark_done(self) -> None:
        self._last_end = datetime.now()
        self._recompute()

    def _on_sync_finished(self, *_args) -> None:
        self._mark_done()

    def _tick(self) -> None:
        c = self.controller
        if c.next_sync_at is None or datetime.now() < c.next_sync_at:
            return
        if c.busy or c.paused or c.login_needed:
            return
        if not c.start_sync(automatic=True):
            return
        # Bei Offline Fehlern kommt kein error Signal, daher hier schon verschieben
        self._mark_done()
