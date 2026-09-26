# GUI Kontext für ILIAS Sync

Dieses Dokument ist der Arbeitsauftrag für einen eigenen Agenten, der **nur die GUI** baut. Die übergeordnete Spezifikation steht in `CLAUDE.md` und gilt weiterhin. Hier steht alles, was für die GUI zusätzlich oder genauer festgelegt ist, vor allem die Schnittstelle zum Backend.

## 1. Auftrag und Abgrenzung

**Der GUI Agent baut:**

```
ilias_sync/gui/
  __init__.py
  app.py              Einstieg: QApplication, Einzelinstanz, Logging, Tray, Wizard bei Erststart
  controller.py       Zentraler Zustand (bereit, Sync, Fehler, Login, pausiert), nur ein Sync gleichzeitig
  logic.py            Qt freie Hilfslogik (Zeitangaben, Benachrichtigungstexte, Vorschau Baum, Validierung)
  tray.py             Tray / Menüleisten Icon + Menü + Benachrichtigungen
  window.py           Hauptfenster mit Tabs
  tab_overview.py     Tab Übersicht
  tab_courses.py      Tab Kurse
  rules_dialog.py     Dialog "Regeln bearbeiten"
  tab_preview.py      Tab Vorschau
  tab_settings.py     Tab Einstellungen
  tab_log.py          Tab Log
  widgets.py          Gemeinsame Widgets (Login Formular, Fortschritt, Vorschau Baum)
  setup_wizard.py     Erststart Assistent
  worker.py           QThread Worker für alle Backend Aufrufe, die länger dauern können
  scheduler_gui.py    QTimer, der den Intervall Sync auslöst (nutzt Werte aus der Config)
  theme.py            Design System: Farb Tokens hell/dunkel, zentrales Stylesheet, Qt Stil Fusion
  icons.py            Platzhalter Icons per QPainter (hell, dunkel, macOS Template), siehe Abschnitt 8
  fake_backend.py     Attrappe des Backends für Entwicklung und Tests
tests/gui/            Tests für die GUI Logik und Widgets (pytest-qt)
```

Stand 24.09.2026: alle Dateien oben sind umgesetzt und getestet. Start mit Beispieldaten: `python -m ilias_sync.gui.app --fake` (bzw. `--fake-setup` für den Wizard).

**Der GUI Agent baut nicht und ändert nicht:** `auth.py`, `crawler.py`, `objects/`, `mapper.py`, `downloader.py`, `state.py`, `config.py`, `platform_utils.py`, `api.py`. Diese Module entstehen parallel oder später durch andere Agenten. Die GUI spricht **ausschließlich** über die Fassade `ilias_sync/api.py` (Abschnitt 3) mit dem Backend. Solange es die Fassade noch nicht gibt, wird gegen `gui/fake_backend.py` entwickelt.

Die Abhängigkeitsrichtung ist eindeutig: `gui` importiert `api`, niemals umgekehrt. Kein Backend Modul darf PySide6 importieren.

## 2. Verbindliche Regeln

* **PySide6** (Version gepinnt in `requirements.txt`), keine weiteren GUI Bibliotheken
* **UI Texte und Code Kommentare auf Deutsch**
* **Keine Gedankenstriche** (– oder —) als Satzzeichen, weder in UI Texten noch in Benachrichtigungen. Stattdessen Punkt, Komma, Doppelpunkt oder Klammern
* **GUI blockiert nie**: jeder Aufruf, der Netzwerk, Dateisystem Scans oder die Datenbank in größerem Umfang betrifft, läuft in `worker.py` (QThread). Nur schnelle Aufrufe wie `load_config()` dürfen im Hauptthread laufen
* **Passwort**: nur im Eingabefeld (`QLineEdit.Password`) und direkt weiter an `api.login(...)`. Nicht in Attributen halten, nicht loggen, nicht in Fehlermeldungen anzeigen. Das Feld nach dem Login leeren
* **Keine Lösch oder Teilen Funktionen**: die GUI bietet nichts an, was Dateien löscht, verschiebt oder hochlädt. "Abmelden" löscht nur Keyring Eintrag und Cookies (macht das Backend)
* **Plattformunabhängig**: keine OS spezifischen Pfade. Dateien und Ordner öffnen über `QDesktopServices.openUrl(QUrl.fromLocalFile(...))`. Pfade als `pathlib.Path`
* **Nur eine Instanz**: zweiter Start holt das Fenster der laufenden Instanz nach vorne (z. B. per `QLocalServer` / `QLocalSocket`) und beendet sich

## 3. Backend Schnittstelle (`ilias_sync/api.py`)

Das ist der Vertrag zwischen GUI und Backend. Der GUI Agent legt ihn **nicht** selbst in `api.py` an, sondern programmiert gegen diese Beschreibung und bildet sie 1:1 in `fake_backend.py` nach. Weicht die spätere echte Fassade ab, wird der Vertrag hier angepasst, nicht heimlich in der GUI.

Alle Methoden sind synchron und Qt frei. Langlaufende Methoden nehmen einen `progress` Callback und ein `cancel` Token (`threading.Event`) entgegen. Der Worker übersetzt Callbacks in Qt Signale.

### 3.1 Datentypen

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable
import threading

class SessionState(Enum):
    OK = "ok"                    # gültige Session
    LOGIN_REQUIRED = "login"     # kein Passwort gespeichert oder abgelaufen und kein Relogin möglich
    OFFLINE = "offline"          # Netzwerk nicht erreichbar

class ItemStatus(Enum):
    OK = "ok"
    UPDATED = "updated"
    USER_MODIFIED = "user_modified"
    USER_REMOVED = "user_removed"
    ERROR = "error"
    ILIAS_CHANGED = "ilias_changed"  # in ILIAS geändert, neue Version noch nicht geladen (Nutzer entscheidet)

class ActionKind(Enum):
    NEW = "new"                  # neue Datei
    CHANGED = "changed"          # in ILIAS geändert: wird nur gemeldet, nicht geladen
    UPDATE = "update"            # veraltet, wird vom Backend nicht mehr erzeugt (kein _alt_ mehr)
    CONFLICT = "conflict"        # Zielname belegt: vorhandene Datei bleibt, ILIAS Datei kommt als *_ilias_<Datum> daneben
    SKIP_REMOVED = "skip_removed"  # vom Nutzer entfernt, wird nicht geladen
    SKIP_IGNORED = "skip_ignored"  # liegt in [ignore]
    SKIP_TOO_LONG = "skip_too_long"  # Zielpfad zu lang: wird nicht geladen. dry_run liefert sie mit dem geplanten
                                     # Ziel als local_path, sync zählt sie als Fehler ("Pfad zu lang (263 Zeichen): ...")
    CREATE_DIR = "create_dir"
    UNASSIGNED = "unassigned"    # Kurs ohne Zuordnung, Ziel _neue_kurse (bzw. ein vorhandenes _Neue_Kurse)

@dataclass
class CourseRule:
    target: str
    match_folder: str | None = None   # Glob auf ILIAS Ordnernamen
    match_file: str | None = None     # Glob auf Dateinamen

@dataclass
class CourseConfig:
    ref_id: int
    ilias_name: str
    local_dir: str | None             # None = noch nicht zugeordnet
    download_files: bool = True
    download_videos: bool = True
    download_exercises: bool = True
    download_weblinks: bool = True
    download_forums: bool = True
    download_other: bool = True       # Wiki, Test, Umfrage ... als Link Datei
    rules: list[CourseRule] = field(default_factory=list)

@dataclass
class Config:
    base_dir: Path | None
    root_folder_name: str = "01_Universität_Mannheim"
    sync_interval_minutes: int = 60
    max_parallel: int = 3             # 1 bis 5
    semester_pattern: str = r"\((HWS|FSS) (\d{4})\)"
    semesters: dict[str, str] = field(default_factory=dict)   # "HWS 2026" -> "01_SEMESTER"
    courses: list[CourseConfig] = field(default_factory=list)
    ignore_local_dirs: list[str] = field(default_factory=list)
    autostart: bool = False
    sync_paused: bool = False
    username: str | None = None       # Uni-ID, kein Passwort

@dataclass
class IliasCourse:
    ref_id: int
    name: str                         # z. B. "Marketing (HWS 2026)"
    semester: str | None              # z. B. "HWS 2026", None wenn Muster nicht passt
    url: str                          # Permalink, zum Öffnen im Browser

@dataclass
class MappingSuggestion:
    course: IliasCourse
    semester_dir: str                 # vorhanden oder neu vorgeschlagen
    local_dir: str                    # vorhanden oder neu vorgeschlagen, z. B. "01_marketing"
    exists: bool                      # True = vorhandener Ordner wurde gefunden
    score: float                      # 0.0 bis 1.0, Güte des Fuzzy Match

@dataclass
class BaseDirCheck:
    exists: bool
    writable: bool
    has_root_folder: bool             # root_folder_name existiert schon darunter
    existing_semesters: list[str]     # z. B. ["01_SEMESTER"]
    message: str | None               # verständlicher Text bei Problemen
    is_empty: bool = True             # Uni Ordner fehlt oder ist leer (Voraussetzung für den ersten Sync)

@dataclass
class PlannedAction:
    kind: ActionKind
    course_ref_id: int
    course_name: str
    ilias_path: str                   # "Kurs / Ordner / Datei.pdf"
    local_path: Path                  # absolutes Ziel
    size: int | None

@dataclass
class DownloadedFile:
    ref_id: int
    course_name: str
    name: str
    local_path: Path
    downloaded_at: datetime
    status: ItemStatus

@dataclass
class ChangedFile:
    # nach dem Herunterladen in ILIAS geändert, wird nie automatisch geladen
    ref_id: int
    course_name: str
    name: str
    local_path: Path                  # vorhandene lokale Datei (kann inzwischen fehlen)
    old_mtime: datetime | None        # ILIAS Änderungsdatum der geladenen Version
    new_mtime: datetime | None        # ILIAS Änderungsdatum der neuen Version
    new_size: int | None
    detected_at: datetime

@dataclass
class SyncRun:
    started_at: datetime
    finished_at: datetime | None
    new: int
    updated: int
    errors: int
    cancelled: bool = False

@dataclass
class SyncResult:
    run: SyncRun
    new_files: list[DownloadedFile]
    new_unassigned_courses: list[IliasCourse]
    errors: list[str]                 # bereits nutzerfreundlich formuliert, ohne Geheimnisse
    changed_files: list[ChangedFile] = field(default_factory=list)  # in diesem Lauf neu als geändert erkannt

@dataclass
class Progress:
    phase: str                        # "Anmelden", "Kurse abrufen", "Durchsuchen", "Herunterladen"
    current: int
    total: int | None                 # None = unbestimmt
    detail: str | None                # z. B. aktueller Kurs oder Dateiname

ProgressCallback = Callable[[Progress], None]
```

### 3.2 Fehler

```python
class IliasSyncError(Exception): ...            # Basis, Text ist direkt anzeigbar
class InvalidCredentialsError(IliasSyncError): ...  # falsches Passwort, Keyring bereits geleert
class LoginError(IliasSyncError): ...           # Login aus anderen Gründen fehlgeschlagen
class NetworkError(IliasSyncError): ...         # offline, Timeout, TLS Fehler
class ConfigError(IliasSyncError): ...          # Config ungültig, Text nennt Zeile / Feld
class Cancelled(IliasSyncError): ...            # durch cancel Token abgebrochen
```

Die GUI zeigt `str(e)` an. Das Backend garantiert, dass darin keine Passwörter, Cookies oder vollständigen Session URLs stehen. Unerwartete andere Exceptions zeigt die GUI mit einem generischen Text ("Unerwarteter Fehler, Details im Log") und loggt sie.

### 3.3 Methoden

```python
class Api:
    # Konto
    def stored_username(self) -> str | None: ...
    def has_stored_password(self) -> bool: ...
    def login(self, user: str, password: str, remember: bool = True) -> None: ...
        # Netzwerk. Wirft InvalidCredentialsError, LoginError, NetworkError
    def check_session(self) -> SessionState: ...
        # Netzwerk. Versucht bei Ablauf selbst neu einzuloggen, wenn Passwort im Keyring
    def logout(self) -> None: ...
        # Keyring Eintrag und Cookie Datei löschen

    # Config (schnell, Hauptthread erlaubt)
    def config_exists(self) -> bool: ...
    def load_config(self) -> Config: ...          # wirft ConfigError
    def save_config(self, cfg: Config) -> None: ...  # validiert, wirft ConfigError

    # Basispfad
    def check_base_dir(self, path: Path) -> BaseDirCheck: ...
    def create_base_structure(self, path: Path) -> None: ...
        # legt nur root_folder_name an, nie etwas löschen

    # Kurse (Netzwerk)
    def fetch_courses(self, progress: ProgressCallback, cancel: threading.Event) -> list[IliasCourse]: ...
    def suggest_mapping(self, courses: list[IliasCourse]) -> list[MappingSuggestion]: ...
        # Dateisystem, nutzt base_dir aus der gespeicherten Config
    def local_course_dirs(self, semester_dir: str) -> list[str]: ...
        # vorhandene Kursordner für das Dropdown, Ignore Liste bereits angewendet

    # Sync
    def dry_run(self, progress: ProgressCallback, cancel: threading.Event) -> list[PlannedAction]: ...
    def sync(self, progress: ProgressCallback, cancel: threading.Event) -> SyncResult: ...
        # Erster Sync (Index für diesen Uni Ordner leer) in einen Uni Ordner mit Inhalt: ConfigError.
        # Ein noch nicht vorhandener Uni Ordner ist in Ordnung. Der Index ist pro Uni Ordner getrennt
    def redownload(self, ref_id: int) -> DownloadedFile: ...   # "Erneut herunterladen"
    def download_changed(self, ref_id: int) -> DownloadedFile: ...
        # neue ILIAS Version als <name>_ilias_<YYYY-MM-DD>.<ext> neben die vorhandene Datei, nie überschreiben
    def dismiss_changed(self, ref_id: int) -> None: ...
        # "Ignorieren": nicht mehr melden, bis sich die Datei in ILIAS erneut ändert

    # Status (SQLite, schnell)
    def last_run(self) -> SyncRun | None: ...
    def recent_files(self, limit: int = 50) -> list[DownloadedFile]: ...
    def removed_items(self) -> list[DownloadedFile]: ...        # Status user_removed
    def changed_items(self) -> list[ChangedFile]: ...           # Status ilias_changed

    # Pfade
    def root_dir(self) -> Path | None: ...      # base_dir / root_folder_name
    def log_dir(self) -> Path: ...
    def log_file(self) -> Path: ...

    # Autostart
    def autostart_enabled(self) -> bool: ...
    def set_autostart(self, enabled: bool) -> None: ...
```

### 3.4 Hilfsfunktionen (Modulebene in `api.py`)

```python
from ilias_sync.api import linux_name

linux_name("Ü1_MfN_Übungsskript Kernlösung.PDF")   # -> "ue1_mfn_uebungsskript_kernloesung.pdf"
```

* `linux_name(name: str) -> str`: Name nach Linux Standard, genau wie das Backend neue Dateien und Ordner benennt (nur `a-z`, `0-9`, `_`, `-`, `.`, Umlaute als ae/oe/ue/ss). Rein, ohne Seiteneffekte, im Hauptthread erlaubt. Die GUI nutzt sie für Vorschläge wie `NN_kursname`, statt eine eigene Kopie zu pflegen
* `home_override() -> Path | None`: Ordner der Testumgebung aus `ILIAS_SYNC_HOME`, sonst `None`. Die GUI hängt daran einen kurzen Hash an den Namen der Einzelinstanz Sperre und an den QSettings Scope, damit eine Test App die normale App nicht blockiert
* `short_dir_name(name: str) -> str`: kürzt einen (schon nach `linux_name` gebildeten) neuen Ordnernamen wie das Backend auf höchstens 40 Zeichen, am Wortende plus 4 Zeichen Hash, stabil. Kurze Namen bleiben unverändert. Für Vorschläge wie `NN_kursname`: `f"{nn:02d}_{short_dir_name(linux_name(kursname))}"` (das Backend entfernt vorher Semester, Kürzel und Zusätze)
* Umgebungsvariable `ILIAS_SYNC_HOME` (bzw. CLI `--config-dir ORDNER`): lenkt Config (`<ORDNER>/config`), Daten mit Index und Cookies (`<ORDNER>/data`) und Log (`<ORDNER>/logs`) in einen eigenen Ordner, für Wizard und Benachrichtigungs Tests ohne die echte Einrichtung. Der Schlüsselbund Eintrag bleibt derselbe

## 4. Threading Modell (`worker.py`)

* Ein generischer Worker: bekommt eine Funktion plus Argumente, führt sie in einem `QThread` aus, sendet Signale `progress(Progress)`, `finished(object)`, `failed(Exception)`
* Nur **ein** Sync oder Dry Run gleichzeitig. Wird "Jetzt synchronisieren" während eines laufenden Syncs geklickt, passiert nichts (Menüpunkt ist deaktiviert)
* Abbrechen setzt das `threading.Event`. Das Backend bricht an der nächsten sicheren Stelle ab (nie mitten in einem Rename)
* Beim Beenden der App: laufenden Sync abbrechen, auf den Thread warten (mit Timeout, dann trotzdem beenden, `.part` Dateien sind harmlos)
* Keine direkten Widget Zugriffe aus dem Worker, nur Signale

## 5. Tray / Menüleiste (`tray.py`)

### Icon Zustände

| Zustand | Wann | Tooltip |
|---|---|---|
| Bereit | letzter Sync ok | "ILIAS Sync: bereit" |
| Synchronisiert | Sync oder Dry Run läuft (animiert über Frame Wechsel per QTimer oder eigenes Icon) | "ILIAS Sync: synchronisiert ..." plus Phase |
| Fehler | letzter Sync mit Fehlern oder Config Fehler | "ILIAS Sync: Fehler, Details im Fenster" |
| Login nötig | `SessionState.LOGIN_REQUIRED` oder `InvalidCredentialsError` | "ILIAS Sync: Anmeldung nötig" |
| Pausiert | `sync_paused` | "ILIAS Sync: pausiert" |

### Menü (Reihenfolge fest)

1. **Jetzt synchronisieren** (deaktiviert während Sync; bei Login nötig öffnet es den Login Dialog)
2. **Letzter Sync: <Zeit> (<n> neue Dateien)** (nicht klickbar). Zeit relativ: "vor 5 Min.", "heute 14:03", "gestern 09:12", sonst Datum
3. **Neue Dateien ▸** Untermenü mit den letzten 10 Dateien (`recent_files(10)`), Anzeige "Dateiname (Kurs)", Klick öffnet die Datei. Existiert die Datei nicht mehr: Hinweis statt Fehler
   * Darunter, nur wenn `changed_items()` nicht leer: **<n> Dateien in ILIAS geändert**, Klick öffnet die Übersicht beim Bereich "In ILIAS geändert"
4. Trenner
5. **Uni Ordner öffnen** (`root_dir()`)
6. **Fenster öffnen**
7. **Sync pausieren** / **Sync fortsetzen** (Text wechselt, Zustand in Config speichern)
8. Trenner
9. **Beenden**

Linksklick auf das Tray Icon öffnet unter Windows das Fenster. Unter macOS öffnet ein Klick immer das Menü (Standardverhalten).

### Benachrichtigungen (`QSystemTrayIcon.showMessage`)

* Neue Dateien, gebündelt pro Kurs: "5 neue Dateien in Marketing". Bei mehreren Kursen eine Meldung: "12 neue Dateien in 3 Kursen"
* In ILIAS geänderte Dateien (`SyncResult.changed_files`, werden nicht automatisch geladen): "2 Dateien in Marketing wurden in ILIAS geändert". Bei mehreren Kursen: "5 Dateien in 3 Kursen wurden in ILIAS geändert". Klick öffnet die Übersicht beim Bereich "In ILIAS geändert"
* Neue, nicht zugeordnete Kurse: "Neuer Kurs gefunden: Statistik I. Bitte im Fenster zuordnen." Klick öffnet Tab Kurse
* Fehler: "Sync fehlgeschlagen: <kurzer Text>". Klick öffnet Tab Log
* Login nötig: "Anmeldung bei ILIAS nötig". Klick öffnet Login Dialog
* Keine Benachrichtigung, wenn nichts Neues passiert ist

## 6. Hauptfenster (`window.py`)

Klein und übersichtlich, Startgröße etwa 800 × 560, Größe und Position werden per `QSettings` gemerkt. Schließen des Fensters beendet die App **nicht**, sie läuft im Tray weiter. Tabs:

### 6.1 Übersicht
* Statuszeile mit Icon und Text (wie Tray Zustand), darunter "Letzter Sync: ..." und "Nächster Sync: ..."
* Buttons: **Jetzt synchronisieren**, **Abbrechen** (nur während Sync sichtbar), **Uni Ordner öffnen**
* Fortschrittsbalken mit Phase und Detail während eines Syncs
* Tabelle der zuletzt geladenen Dateien (`recent_files(50)`): Datei, Kurs, Zielordner (relativ zu `root_dir`), Zeit, Status. Doppelklick öffnet die Datei, Kontextmenü: "Datei öffnen", "Ordner öffnen"
* Bereich **"In ILIAS geändert"** (`changed_items()`), gut sichtbar oberhalb der Dateitabelle, nur wenn nicht leer, mit Anzahl im Titel. Pro Eintrag: Datei, Kurs, alt/neu Änderungsdatum, Buttons **Herunterladen** (`download_changed`, im Worker, legt die neue Version als `<name>_ilias_<Datum>` daneben, die vorhandene Datei bleibt unverändert) und **Ignorieren** (`dismiss_changed`). Zusätzlich "Alle herunterladen". Die Anzahl erscheint auch im Tray Menü ("3 Dateien in ILIAS geändert", Klick öffnet die Übersicht)
* Aufklappbarer Bereich **"Vom Nutzer entfernt"** (`removed_items()`), pro Eintrag Button **Erneut herunterladen** (`redownload`, im Worker)

### 6.2 Kurse
* Tabelle: ILIAS Kurs, Semester, lokaler Ordner (Dropdown mit `local_course_dirs` plus Eintrag "Neuer Ordner ..." und "Nicht zugeordnet"), Checkboxen für Dateien, Videos, Übungen, Weblinks, Foren, Sonstiges
* Nicht zugeordnete Kurse oben und farblich markiert, Hinweis: "Wird bis zur Zuordnung unter _neue_kurse abgelegt"
* "Neuer Ordner ..." fragt den Namen ab und schlägt `NN_kursname` vor (nächste freie Nummer, Name über `linux_name`, z. B. `03_statistik_i`). Umbenennen eines vom Tool angelegten Ordners ändert **nur die Config Zuordnung**, die GUI verschiebt keine Dateien. Hinweis dazu anzeigen
* Button **Regeln bearbeiten** pro Kurs: Dialog mit Liste der `CourseRule`s (Typ Ordner oder Datei, Glob Muster, Zielordner), Reihenfolge per Hoch/Runter änderbar ("erste passende gewinnt"), Hinzufügen, Entfernen
* Button **Kurse aktualisieren** (`fetch_courses` im Worker)
* Änderungen werden mit **Speichern** übernommen (`save_config`), ungespeicherte Änderungen beim Tab Wechsel oder Schließen nachfragen
* Die Semester Zuordnung steht nicht hier, sondern in den Einstellungen (Entscheidung des Nutzers, 25.09.2026)

### 6.3 Vorschau
* Button **Vorschau erstellen** (`dry_run` im Worker, mit Fortschritt und Abbrechen)
* Ergebnis als Baum (`QTreeView`) nach lokalem Zielpfad: Semester / Kurs / Kategorie / ... / Datei
* Symbol oder Farbe pro `ActionKind`, Legende darunter. Filter: "Nur Änderungen" (blendet `SKIP_*` aus)
* Zusammenfassung: "34 neue Dateien (1,2 GB), 3 Aktualisierungen, 1 Konflikt, 2 übersprungen"
* Button **Jetzt so synchronisieren** startet den echten Sync

### 6.4 Einstellungen
* Button **Jetzt synchronisieren** oben rechts (wie in der Übersicht, deaktiviert während eines Syncs, bei Login nötig öffnet er den Login Dialog). Ungespeicherte Einstellungen vorher speichern oder verwerfen lassen, denn der Sync nutzt die gespeicherte Config
* **Basisordner**: Anzeige plus "Ändern ..." (nativer Ordnerdialog `QFileDialog.getExistingDirectory`). Vor dem Übernehmen Warnung: "Vorhandene Dateien werden nicht verschoben. Neue Downloads landen im neuen Ordner." Danach `check_base_dir`. Ist `is_empty` falsch, zusätzlich warnen: der erste Sync in einen fremden Uni Ordner mit Inhalt wird verweigert
* **Name des Uni Ordners** (`root_folder_name`)
* **Intervall** in Minuten (`QSpinBox`, 15 bis 1440)
* **Parallelität** (`QSpinBox`, 1 bis 5, Standard 3)
* **Semester Muster** (Regex, mit Prüfung auf Gültigkeit vor dem Speichern)
* **Semester Ordner** (`semesters`): kleine Tabelle "HWS 2026" → `01_SEMESTER`. Semester aus Kursnamen ohne Ordner erscheinen als leere Zeile (wird beim Sync mit der nächsten Nummer angelegt, z. B. `02_semester`; vorhandene Ordner wie `01_SEMESTER` behalten ihre Schreibweise)
* **Ignorierte lokale Ordner** (Liste mit Hinzufügen / Entfernen, Glob Muster)
* **Autostart** Checkbox (`set_autostart`)
* **Konto**: angezeigte Uni-ID, Button **Abmelden** mit Bestätigung ("Passwort und Session werden von diesem Gerät entfernt. Heruntergeladene Dateien bleiben erhalten.")
* Config Fehler (`ConfigError`) werden oben im Tab rot und verständlich angezeigt

### 6.5 Log
* Letzte etwa 500 Zeilen der Logdatei, automatisch aktualisiert (Datei beobachten oder per Timer nachladen), nur lesend
* Filter: Alle / Warnungen / Fehler
* Buttons **Log Ordner öffnen**, **Kopieren**

## 7. Setup Wizard (`setup_wizard.py`)

`QWizard`, startet automatisch, wenn `config_exists()` falsch ist oder `base_dir` fehlt. Abbrechen beendet die App nach Rückfrage (ohne Setup gibt es nichts zu tun).

1. **Anmelden**: Uni-ID, Passwort, Checkbox "Passwort im Schlüsselbund speichern" (Standard an). Button "Anmelden testen" (`login` im Worker). Weiter erst nach Erfolg. Fehlertexte: falsches Passwort, Netzwerkproblem, sonstiges
2. **Basisordner**: Eingabefeld plus **Durchsuchen**, kein automatischer Vorschlag. Der erste echte Sync läuft in einen **neuen, leeren** Uni Ordner (Entscheidung des Nutzers): Weiter nur, wenn `check_base_dir(...).is_empty`, sonst verständlicher Hinweis, einen anderen Ordner zu wählen. Live Prüfung mit `check_base_dir` (existiert, beschreibbar, leer). Fehlende Ordner nach Bestätigung anlegen (`create_base_structure`). Vorschau der künftigen Struktur als kurzer Text Baum
3. **Kurse zuordnen**: `fetch_courses` und `suggest_mapping` im Worker. Das Backend schlägt für den leeren Uni Ordner `NN_kursname` Ordner vor, Kurse derselben Veranstaltung ([V]/[Ü]/[T]) im selben Ordner. Vorhandene Ordner mit Score anzeigen, unsichere Treffer (Score unter 0,7) markieren. Nutzer bestätigt oder korrigiert
4. **Vorschau**: `dry_run` (Worker), Baum wie Tab Vorschau
5. **Erster Sync**: Hinweis auf Datenmenge, Button **Synchronisierung starten**. Nach Start schließt der Wizard, Fortschritt im Tray und im Fenster

Config wird nach Schritt 2 und 3 gespeichert, damit ein Abbruch später nicht alles verliert.

## 8. Icons und Plattformdetails

* Icons als SVG oder PNG in `ilias_sync/gui/icons/`, für jeden Zustand aus Abschnitt 5 eine Variante. Solange keine finalen Icons existieren: einfache Platzhalter selbst erzeugen (z. B. Kreis mit Buchstabe, per `QPainter` oder als kleines SVG), keine Icons aus fremden Quellen
* **macOS**: Menüleisten Icons als Template Image (einfarbig schwarz mit Alpha, `QIcon.setIsMask(True)`), dann passt macOS sie an hell und dunkel an. App ohne Dock Icon: `LSUIElement` kommt über den PyInstaller Build, im Entwicklungsmodus zusätzlich `QApplication.setQuitOnLastWindowClosed(False)`
* **Windows**: farbige Icons, Varianten für hellen und dunklen Infobereich (Erkennung über `QGuiApplication.styleHints().colorScheme()`, Wechsel zur Laufzeit beachten)
* Hohe DPI: Icons mindestens in 16, 32 und 64 px oder als SVG
* Ressourcen Pfade so laden, dass es auch im PyInstaller Bundle funktioniert (`Path(__file__).parent / "icons"`, im Bundle über `sys._MEIPASS`)

## 9. Intervall Sync (`scheduler_gui.py`)

* `QTimer` mit `sync_interval_minutes`, Neustart bei Änderung in den Einstellungen
* Kein Sync, wenn pausiert, bereits einer läuft oder Login nötig ist
* Erster Sync etwa 1 Minute nach App Start (nicht sofort, damit der Rechner hochfahren kann)
* Nach Rückkehr aus dem Ruhezustand: ist das Intervall überschritten, zeitnah synchronisieren
* Bei `NetworkError` still im Zustand bleiben, nächster Versuch beim nächsten Intervall (keine Benachrichtigung bei jedem Offline Versuch, nur Tooltip "offline")

## 10. Einstieg (`app.py`)

```python
def run(api: Api) -> int:
    """Startet die GUI und gibt den Exit Code zurück."""
```

`__main__.py` (gehört nicht dem GUI Agenten) ruft ohne Argumente `gui.app.run(Api())` auf, `Api` ist seit 2026-09-26 das echte Backend (`backend.py`, Log Datei `ilias-sync.log`). Zum Entwickeln startet `python -m ilias_sync.gui.app --fake` die GUI mit `FakeBackend`.

## 11. Fake Backend (`fake_backend.py`)

Implementiert jede Methode aus 3.3 mit realistischen Daten, damit sich alle Screens ohne ILIAS Zugang durchklicken lassen:

* Kurse aus der Beispielstruktur in `CLAUDE.md` (Mathe Vorkurs, Marketing, Praktische Informatik I, Programmierpraktikum 1, Wirtschaftsinformatik 1, Formale Grundlagen, alle "HWS 2025") plus ein neuer, nicht zugeordneter Kurs
* `sync` und `dry_run` simulieren Fortschritt mit kleinen Pausen und beachten `cancel`
* Schalter per Umgebungsvariable oder Konstruktor Argument, um Fehlerfälle zu erzwingen: falsches Passwort, offline, Config Fehler, Sync mit Fehlern
* Schreibt nichts ins echte Dateisystem außer in ein temporäres Verzeichnis

## 12. Tests (`tests/gui/`)

* Reine Logik (relative Zeitangaben, Bündelung der Benachrichtigungstexte, Baumaufbau der Vorschau aus `PlannedAction`s, Vorschlag `NN_kursname`, Validierung der Einstellungen) in Qt freie Funktionen auslagern und mit `pytest` testen
* Prüfen, dass kein UI Text einen Gedankenstrich enthält (Test, der alle String Literale in `gui/` durchsucht)
* Widget Tests optional mit `pytest-qt` (nur als Entwicklungsabhängigkeit, gepinnt), gegen `FakeBackend`, `QT_QPA_PLATFORM=offscreen`
* Keine Live Requests

## 13. Abnahmekriterien

* [ ] `python -m ilias_sync.gui.app --fake` startet auf Windows und macOS, Tray Icon erscheint (macOS geprüft 2026-09-26, Windows offen)
* [x] Setup Wizard lässt sich mit dem Fake Backend komplett durchlaufen, inklusive Fehlerfällen
* [x] Alle fünf Tabs funktionieren mit dem Fake Backend
* [x] Während eines simulierten Syncs bleibt die GUI bedienbar, Abbrechen funktioniert
* [x] Fenster schließen beendet die App nicht, "Beenden" im Tray schon, sauber auch während eines Syncs
* [x] Zweiter Start öffnet das vorhandene Fenster
* [x] Keine Imports aus Backend Modulen außer `api` (bzw. `fake_backend`)
* [x] Kein Passwort in Logs, Attributen oder Fehlermeldungen
* [x] Tests grün

## 14. Offene Punkte (mit dem Nutzer klären, nicht raten)

* keine

Geklärt am 26.09.2026: Finale Icons nach Design Handoff Richtung 1b (das "i" als Download Pfeil), SVGs in `ilias_sync/gui/assets/`, Build Icons in `packaging/icons/`.

Geklärt am 25.09.2026: Semester Zuordnung in den Einstellungen. Videos bleiben im Wizard standardmäßig an. Einstellungen bekommen einen Button "Jetzt synchronisieren". Erster Sync nur in einen leeren Uni Ordner.
