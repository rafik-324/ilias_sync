"""
macOS Besonderheiten über die Objective-C Laufzeit (ctypes, ohne Zusatzpakete).

Im gebauten .app sorgt LSUIElement dafür, dass kein Dock Icon erscheint. Beim
Start aus der Entwicklungsumgebung fehlt diese Angabe, deshalb wird dieselbe
Einstellung hier zur Laufzeit gesetzt. Auf anderen Systemen tun die Funktionen
nichts.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import sys

log = logging.getLogger(__name__)

IS_MAC = sys.platform == "darwin"

# NSApplicationActivationPolicyAccessory: kein Dock Icon, kein App Menü
_POLICY_ACCESSORY = 1

_objc = None


def _runtime():
    global _objc
    if _objc is None:
        path = ctypes.util.find_library("objc")
        if not path:
            raise OSError("libobjc nicht gefunden")
        lib = ctypes.cdll.LoadLibrary(path)
        lib.objc_getClass.restype = ctypes.c_void_p
        lib.objc_getClass.argtypes = [ctypes.c_char_p]
        lib.sel_registerName.restype = ctypes.c_void_p
        lib.sel_registerName.argtypes = [ctypes.c_char_p]
        _objc = lib
    return _objc


def _send(receiver, selector: str, *args, restype=ctypes.c_void_p, argtypes=()):
    lib = _runtime()
    fn = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(
        ctypes.cast(lib.objc_msgSend, ctypes.c_void_p).value
    )
    return fn(receiver, lib.sel_registerName(selector.encode()), *args)


def _ns_app():
    cls = _runtime().objc_getClass(b"NSApplication")
    return _send(cls, "sharedApplication")


def hide_dock_icon() -> None:
    """App nur in der Menüleiste zeigen, ohne Dock Icon (wie LSUIElement)."""
    if not IS_MAC:
        return
    try:
        _send(_ns_app(), "setActivationPolicy:", _POLICY_ACCESSORY,
              restype=ctypes.c_bool, argtypes=(ctypes.c_long,))
    except Exception:  # noqa: BLE001
        log.warning("Dock Icon konnte nicht ausgeblendet werden", exc_info=True)


def activate_app() -> None:
    """App nach vorne holen. Ohne Dock Icon kommen Fenster sonst hinter anderen Apps auf."""
    if not IS_MAC:
        return
    try:
        _send(_ns_app(), "activateIgnoringOtherApps:", True, restype=None, argtypes=(ctypes.c_bool,))
    except Exception:  # noqa: BLE001
        log.warning("App konnte nicht aktiviert werden", exc_info=True)


def reduce_motion() -> bool:
    """Systemeinstellung "Bewegung reduzieren" (macOS). Auf anderen Systemen False."""
    if not IS_MAC:
        return False
    try:
        cls = _runtime().objc_getClass(b"NSWorkspace")
        ws = _send(cls, "sharedWorkspace")
        return bool(_send(ws, "accessibilityDisplayShouldReduceMotion", restype=ctypes.c_bool))
    except Exception:  # noqa: BLE001
        log.warning("Einstellung Bewegung reduzieren nicht lesbar", exc_info=True)
        return False
