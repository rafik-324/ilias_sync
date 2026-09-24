"""
Hintergrund Ausführung von Backend Aufrufen.

Jeder Aufruf, der Netzwerk oder grössere Dateisystem Arbeit bedeutet, läuft in
einem eigenen QThread. Der Worker übersetzt den progress Callback des Backends
in Qt Signale. Widgets werden nie aus dem Worker angefasst.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

from ilias_sync.api import Progress

log = logging.getLogger(__name__)


class Worker(QObject):
    progress = Signal(object)   # Progress
    finished = Signal(object)   # Rückgabewert
    failed = Signal(object)     # Exception

    def __init__(self, fn: Callable[..., Any], *args: Any, with_progress: bool = False, **kwargs: Any) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._with_progress = with_progress
        self.cancel_event = threading.Event()

    def run(self) -> None:
        try:
            if self._with_progress:
                result = self._fn(self._emit_progress, self.cancel_event, *self._args, **self._kwargs)
            else:
                result = self._fn(*self._args, **self._kwargs)
        except Exception as e:  # noqa: BLE001, alles geht als Signal an die GUI
            if not _is_expected(e):
                log.exception("Unerwarteter Fehler im Hintergrund")
            self.failed.emit(e)
        else:
            self.finished.emit(result)
        finally:
            # Argumente (z. B. Passwort beim Login) nicht länger als nötig halten
            self._args = ()
            self._kwargs = {}

    def _emit_progress(self, p: Progress) -> None:
        self.progress.emit(p)

    def cancel(self) -> None:
        self.cancel_event.set()


def _is_expected(e: Exception) -> bool:
    from ilias_sync.api import IliasSyncError

    return isinstance(e, IliasSyncError)


class Task(QObject):
    """Hält Thread und Worker zusammen und räumt nach dem Ende auf."""

    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(object)
    done = Signal()             # immer, nach finished oder failed

    _running: set["Task"] = set()   # verhindert vorzeitige Garbage Collection

    def __init__(self, fn: Callable[..., Any], *args: Any, with_progress: bool = False,
                 parent: QObject | None = None, **kwargs: Any) -> None:
        super().__init__(parent)
        self.thread = QThread()
        self.worker = Worker(fn, *args, with_progress=with_progress, **kwargs)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)

    def start(self) -> "Task":
        Task._running.add(self)
        self.thread.start()
        return self

    def cancel(self) -> None:
        self.worker.cancel()

    def is_running(self) -> bool:
        return self.thread.isRunning()

    def wait(self, msecs: int) -> bool:
        return self.thread.wait(msecs)

    def _on_finished(self, result: Any) -> None:
        self.finished.emit(result)
        self._cleanup()

    def _on_failed(self, error: Exception) -> None:
        self.failed.emit(error)
        self._cleanup()

    def _cleanup(self) -> None:
        self.thread.quit()
        self.thread.wait(2000)
        Task._running.discard(self)
        self.done.emit()


def run_task(fn: Callable[..., Any], *args: Any, on_done: Callable[[Any], None] | None = None,
             on_error: Callable[[Exception], None] | None = None,
             on_progress: Callable[[Progress], None] | None = None,
             with_progress: bool = False, **kwargs: Any) -> Task:
    """Kurzform: Aufgabe im Hintergrund starten und Callbacks verbinden."""
    task = Task(fn, *args, with_progress=with_progress, **kwargs)
    if on_done:
        task.finished.connect(on_done)
    if on_error:
        task.failed.connect(on_error)
    if on_progress:
        task.progress.connect(on_progress)
    return task.start()
