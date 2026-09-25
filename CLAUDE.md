# ILIAS Sync (Uni Mannheim)

Desktop App, die automatisch alle Inhalte aus ILIAS der Universität Mannheim herunterlädt und in die bestehende lokale Ordnerstruktur des Nutzers einsortiert. Läuft als Tray/Menüleisten App auf macOS und Windows.

## Leitprinzipien

* **Sicherheit vor Komfort**: eigener Code, minimale Abhängigkeiten (Versionen pinnen), keine Drittanbieter ILIAS Tools
* **Nie Daten verlieren**: das Tool löscht nie etwas und überschreibt nie Dateien des Nutzers
* **Plattformunabhängig**: identisches Verhalten auf macOS und Windows, keine OS spezifischen Pfade im Code
* **Anpassung an den Nutzer**: das Tool folgt der bestehenden Ordnerstruktur, nicht umgekehrt
* **Server schonen**: moderate Parallelität, Pausen zwischen Requests, eigener User-Agent
* Code Kommentare und UI Texte auf Deutsch
* In Texten und UI keine Gedankenstriche als Satzzeichen verwenden

## Tech Stack

* Python 3.11+
* `requests` + `beautifulsoup4` für HTTP und HTML Parsing
* `keyring` für Zugangsdaten (macOS Schlüsselbund, Windows Anmeldeinformationsverwaltung)
* `PySide6` für GUI und Tray Icon (`QSystemTrayIcon`)
* `platformdirs` für Config, Daten und Log Pfade
* `tomllib` (stdlib) zum Lesen, `tomli-w` zum Schreiben der Config
* `sqlite3` (stdlib) für den Status Index
* `PyInstaller` für Builds (`.app` auf macOS, `.exe` auf Windows)
* `pytest` + `pytest-qt` für Tests, Einstellungen in `pyproject.toml`
* `ruff` für Linting (Zeilenlänge 120)

## Entwicklung

### Zwei Agenten, zwei Worktrees

Backend und GUI werden parallel von zwei Claude Sitzungen entwickelt. Jede hat ihren eigenen Git Worktree, damit keine die Dateien der anderen verändert (auch nicht bei `rebase` oder `checkout`):

| Ordner | Branch | Wer |
|---|---|---|
| `C:\Users\rafik\OneDrive\01_Universität_Mannheim\code_projects\Ilias_getter` | `main` | Backend Agent |
| `C:\Users\rafik\dev\ilias_sync-gui` (außerhalb von OneDrive) | `gui` | GUI Agent |

* Nur im eigenen Ordner lesen, schreiben, testen und Git Befehle ausführen, immer mit absoluten Pfaden
* Gemeinsame Dateien (`api.py` Vertrag, `GUI_KONTEXT.md`, `CLAUDE.md`, `requirements*.txt`, `pyproject.toml`) nur klein ändern und in der Commit Nachricht erwähnen
* `gui` wird regelmäßig nach `main` gemergt (nach getesteten Schritten, nach Rückfrage beim Nutzer), danach `main` in `gui` übernehmen

### Git und GitHub

* Remote: `origin` = `https://github.com/rafik-324/ilias_sync.git` (privat)
* Commit E-Mail im Repo: `215122369+rafik-324@users.noreply.github.com` (GitHub blockiert Pushes mit der privaten Adresse)
* Nach getesteten Schritten selbst committen, pushen nur nach Rückfrage
* Keine Historie umschreiben, solange ein anderer Agent im Repo arbeitet

### Python Umgebung

* Eine gemeinsame venv für beide Agenten: `C:\Users\rafik\.venvs\ilias-sync` (Python 3.13, alle Pakete aus `requirements-dev.txt`). Nicht im Projektordner, weil OneDrive sonst tausende Dateien synchronisiert
* `python` im PATH ist nur der Microsoft Store Alias, daher immer den vollen Pfad nutzen: `C:\Users\rafik\.venvs\ilias-sync\Scripts\python.exe -m pytest`
* Tests immer aus dem eigenen Worktree starten, damit dessen Code getestet wird (`pythonpath = ["."]` in `pyproject.toml`)

## Architektur

```
ilias_sync/
  __main__.py          Einstieg: GUI starten oder CLI Modus (--login, --logout, --crawl
                       mit --course/--output/-v, --test-sync ZIELORDNER [--videos],
                       --dry-run und --sync [--output])
  auth.py              Login, Session, Cookies, Keyring (Basis: ilias_login.py)
  crawler.py           ILIAS Kurse und Objekte rekursiv erfassen
  objects/             Handler pro ILIAS Objekttyp
    file.py
    folder.py
    video.py           Opencast / Mediacast
    exercise.py        Übungen: Aufgaben + eigene Abgaben
    weblink.py
    forum.py
  mapper.py            ILIAS Pfad -> lokaler Zielpfad (Kategorien, Config Regeln)
  downloader.py        Downloads, atomares Schreiben, Hash, Namen für Versionen
  sync.py              Sync planen (Mapper + Index + Dateisystem) und ausführen
  state.py             SQLite Index, eine Datenbank pro Uni Ordner
  config.py            Laden, Validieren, Standardwerte
  scheduler.py         Intervall Sync im Hintergrund (Logik ohne Qt)
  api.py               Fassade für die GUI: einzige Schnittstelle zwischen GUI und Backend
  gui/
    app.py             Einstieg: QApplication, Einzelinstanz, Tray, Wizard bei Erststart
    controller.py      Zentraler Zustand, einziger Zugriff auf das Backend, nur ein Sync gleichzeitig
    logic.py           Qt freie Hilfslogik (Zeiten, Texte, Vorschau Baum, Validierung), ohne QApplication testbar
    tray.py            Tray / Menüleisten Icon + Menü
    window.py          Hauptfenster: Seitenleiste mit Navigation, rechts die jeweilige Seite
    theme.py           Design Tokens für hell und dunkel, zentrales Stylesheet (Qt Stil Fusion)
    tab_overview.py    Seiten des Hauptfensters: Übersicht,
    tab_courses.py       Kurse,
    tab_preview.py       Vorschau,
    tab_settings.py      Einstellungen,
    tab_log.py           Log
    rules_dialog.py    Regeln pro Kurs bearbeiten
    widgets.py         Wiederverwendbare Widgets
    setup_wizard.py    Erststart: Login, Basisordner, Kurszuordnung
    worker.py          QThread Worker für Backend Aufrufe
    scheduler_gui.py   QTimer, löst den Intervall Sync aus
    fake_backend.py    Attrappe von api.py für Entwicklung und Tests
    icons.py           Icons für Tray Zustände
  platform_utils.py    Autostart, Dateinamen, Unicode, Link Dateien
tests/
  gui/
```

Crawler, Mapper und Downloader dürfen keine GUI Abhängigkeiten haben, damit alles auch per CLI testbar ist.

**Schichtung**: `gui/` importiert ausschließlich `api.py`, nie direkt `auth`, `crawler`, `mapper`, `downloader`, `state` oder `config`. Kein Backend Modul importiert PySide6. `api.py` ist synchron und Qt frei, langlaufende Methoden nehmen einen `progress` Callback und ein `cancel` Token (`threading.Event`) entgegen und werfen nur Fehler aus der Hierarchie `IliasSyncError` (`InvalidCredentialsError`, `LoginError`, `NetworkError`, `ConfigError`, `Cancelled`), deren Text direkt anzeigbar ist und keine Geheimnisse enthält. Der genaue Vertrag (Datentypen, Methoden) steht in `GUI_KONTEXT.md` Abschnitt 3 und ist für GUI und Backend verbindlich. Änderungen am Vertrag werden dort eingetragen.

## Authentifizierung (bereits analysiert, funktioniert)

ILIAS nutzt **SAML über Shibboleth IdP**, kein 2FA. Nur Uni-ID + Passwort.

Ablauf (per Edge DevTools verifiziert):

1. `GET https://ilias.uni-mannheim.de/saml.php?baseClass=ilstartupgui&cmd=doSamlAuthentication&saml_idp_id=1`
2. Weiterleitung zum IdP `https://idp.uni-mannheim.de/idp/profile/SAML2/Redirect/SSO?execution=e1s1`
3. `POST` an den IdP mit Loginformular (Felder automatisch erkennen: Passwortfeld über `type=password`, Benutzerfeld `j_username` oder erstes Textfeld, alle Hidden Felder wie `csrf_token` übernehmen, Button `_eventId_proceed` mitsenden). Antwort `200` mit Auto-Submit Formular, das `SAMLResponse` und `RelayState` enthält
4. `POST https://ilias.uni-mannheim.de/module.php/saml/sp/saml2-acs.php/default-sp` mit `SAMLResponse` + `RelayState`, Antwort `303`
5. Weiterleitung zu `https://ilias.uni-mannheim.de/saml.php?baseClass=ilstartupgui&cmd=doSamlAuthentication&idpentityid=https://idp.uni-mannheim.de/idp/shibboleth&saml_idp_id=1`, Session Cookies gesetzt (`PHPSESSID`, `SimpleSAMLAuthToken`)

Ein funktionsfähiger Prototyp liegt in `ilias_login.py` (Formularparser, Loop über IdP Zwischenseiten wie LocalStorage Check und Einwilligung, Cookie Persistenz, Keyring). Diesen als Basis für `auth.py` verwenden.

Anforderungen:

* Login Erkennung: auf `ilias.uni-mannheim.de`, URL enthält nicht `login.php`, Seite enthält Logout Link
* Cookies persistent speichern (Datei mit Rechten 600 im Datenverzeichnis aus `platformdirs`), vor jedem Sync Session prüfen, bei Ablauf automatisch neu einloggen
* Passwort nur im Keyring, nie in Config, Logs oder Fehlermeldungen
* Bei falschem Passwort: Keyring Eintrag löschen, GUI fragt neu
* Nur HTTPS, `verify=True`

## Crawler

**Wichtig**: Die HTML Struktur von ILIAS (Version 9.x) nicht raten. Vor der Implementierung echte Seiten nach dem Login abrufen, als Fixtures unter `tests/fixtures/` speichern (personenbezogene Daten entfernen) und die Parser dagegen entwickeln.

* Einstieg: Dashboard `https://ilias.uni-mannheim.de/ilias.php?baseClass=ilDashboardGUI`, Bereich "Meine Kurse und Gruppen". Das liefert automatisch nur die Kurse des eingeloggten Nutzers, dadurch universell für alle Studierenden
* Kurse haben stabile `ref_id`s (sichtbar in `goto.php?target=crs_<ref_id>` bzw. Permalinks). Diese als Primärschlüssel verwenden, nicht die Namen
* Semester aus dem Kursnamen ableiten, Muster z. B. `(HWS 2026)` oder `(FSS 2027)`, per Regex in Config anpassbar
* Rekursiv durch Ordner, Sitzungen, Gruppen und Lernmodule mit Container Charakter
* Pro Objekt erfassen: Typ, ref_id, Name, ILIAS Pfad, Änderungsdatum, Größe, Download URL
* Parallelität standardmäßig 3, maximal 5, kleine Pause zwischen Seitenabrufen
* Unbekannte Objekttypen loggen und überspringen, nicht abbrechen

## Was heruntergeladen wird

**Entscheidung des Nutzers (2026-09-25): nur Dateien**, dazu gehören auch Videos, die in ILIAS als Datei liegen (`.mp4` usw.). Übungen, Weblinks, Foren, MobileQuiz, TraQ, Lernmodule und alle übrigen Objekttypen werden **nicht** geladen, auch keine Link Dateien. Die Tabelle unten war der ursprüngliche Plan und gilt nur noch, falls der Nutzer das später wieder möchte.

| ILIAS Objekt | Lokal |
|---|---|
| Datei | Originaldatei mit Originalnamen und Endung |
| Video (Opencast / Mediacast) | Videodatei in bester verfügbarer Qualität. Opencast Anbindung erst recherchieren |
| Übung | Aufgabenstellungen, Anhänge und die **eigenen** Abgaben, je Aufgabe ein Unterordner |
| Weblink | `.url` auf Windows, `.webloc` auf macOS |
| Forum | pro Thread eine Markdown Datei (Autor, Datum, Text), Anhänge daneben |
| Sonstiges (Wiki, Test, Umfrage ...) | vorerst nur als Link Datei zum ILIAS Objekt |

Jeder Objekttyp ist in den Einstellungen pro Kurs einzeln abschaltbar (Standard: alles an). Config Schalter pro Kurs: `download_files`, `download_videos`, `download_exercises`, `download_weblinks`, `download_forums`, `download_other`.

## Ordneraufbau ab Basispfad

Der Nutzer gibt beim ersten Start **einmalig einen Basispfad** ein (z. B. `C:\Users\rafik\OneDrive\Uni` oder `~/Documents/Uni`). Alles darunter erstellt das Tool selbst nach diesem Schema:

```
<Basispfad>/
└── 01_Universität_Mannheim/
    └── NN_SEMESTER/                 01_SEMESTER, 02_SEMESTER ... chronologisch nach Semester
        └── NN_<kursname>/           Nummer fortlaufend, Name klein, Leerzeichen -> _
            └── NN_<Kategorie>/      01_Vorlesung, 02_Übung, 03_Tutorium ...
                └── ... ILIAS Unterordner 1:1
```

* Existiert `01_Universität_Mannheim` bzw. ein Semester oder Kursordner schon, wird der vorhandene verwendet, nichts doppelt angelegt
* Neue Semester werden automatisch als nächste Nummer angelegt, sobald Kurse eines neuen Semesters in ILIAS auftauchen
* Neue Kurse bekommen die nächste freie Nummer im Semesterordner, Name aus dem ILIAS Kursnamen ohne Semesterzusatz (z. B. "Marketing (HWS 2026)" -> `01_marketing`)
* Ordnernamen für neu angelegte Kurse sind in der GUI (Tab Kurse) umbenennbar, das Tool merkt sich die Zuordnung per ref_id
* Der Name `01_Universität_Mannheim` ist in der Config änderbar (`root_folder_name`)

## Lokale Ordnerstruktur des Nutzers (Semester 1)

Bestehende Struktur unter dem Basispfad, muss vom Tool erkannt und weiterverwendet werden:

```
01_Universität_Mannheim/
└── 01_SEMESTER/
    ├── 00_mathe_vorkurs/
    │   ├── 01_Vorlesung/
    │   └── 02_Übung/
    ├── 01_marketing/
    │   ├── 01_Vorlesung/
    │   ├── 02_Übung/
    │   └── 03_Tutorium/
    ├── 02_praktische_informatik_I/
    │   ├── 01_Vorlesung/
    │   ├── 02_Übung/
    │   ├── 03_Tutorium/
    │   └── 04_code/                   eigene Arbeit, NIE anfassen
    ├── 03_programmier_praktikum_1/
    │   ├── 01_Vorlesung/
    │   ├── 02_Übung/
    │   ├── 03_Tutorium/
    │   ├── 04_freiwillige_abgaben/
    │   └── linux_data/                eigene Arbeit, NIE anfassen
    ├── 04_wirtschaftsinformatik_1/
    │   ├── 01_vorlesung/
    │   └── 02_tutorium/
    └── 05_formale_grundlagen/
        ├── 01_Vorlesung/
        ├── 02_Übung/
        └── 03_Übungsblätter/
```

Beobachtungen, die der Code berücksichtigen muss:

* Groß/Kleinschreibung ist uneinheitlich (`01_Vorlesung` vs `01_vorlesung`)
* Umlaute und Ersatzschreibweise gemischt (`02_Übung` vs `02_uebung`)
* Nummernpräfixe `NN_` vor Kategorien und Kursen
* Ordner mit Konfliktnamen aus Cloud Sync existieren (z. B. `01_übung-MacBook Pro von Rafael`), diese ignorieren

## Mapping (mapper.py)

Ziel: ILIAS Pfad `Kurs / ILIAS Ordner / ... / Datei` wird zu `Basis / Semesterordner / Kursordner / Kategorieordner / ... / Datei`.

### 1. Semester

Semester aus Kursname → Semesterordner laut Config (z. B. `HWS 2026` → `01_SEMESTER`).

### 2. Kurs

* Zuordnung ILIAS Kurs (ref_id) → lokaler Kursordner steht in der Config
* Setup Wizard schlägt Zuordnungen per Fuzzy Match zwischen ILIAS Kursname und vorhandenen Ordnern vor, Nutzer bestätigt
* Neue, nicht zugeordnete Kurse: GUI Benachrichtigung, bis zur Zuordnung Ablage in `Semesterordner/_Neue_Kurse/<ILIAS Kursname>/`
* **Eine Veranstaltung besteht in ILIAS aus mehreren Kursen und Gruppen** (so im Testlauf beobachtet), z. B. `Praktische Informatik I [V] [1. PG] (HWS 2026)`, `... [Ü] [1. PG] ...`, `... [T] ...` und die Gruppe `Praktische Informatik I - Tut [1. PG]`. Alle landen im **selben** lokalen Kursordner. Der Fuzzy Match vergleicht deshalb den Namen ohne Semester, ohne Kürzel in eckigen Klammern (`[V]`, `[Ü]`, `[T]`, `[1. PG]`) und ohne Zusätze wie `Große Übung` oder `Tut`
* Gruppen, die innerhalb eines Kurses liegen, gehören zum Kursordner dieses Kurses

### 3. Kategorie (Standardregeln, automatisch)

Die Kategorie wird in dieser Reihenfolge bestimmt, das erste Passende gewinnt:

1. **Config Regeln** (Abschnitt 4)
2. **ILIAS Ordnername per Schlüsselwort** (Tabelle unten), geprüft auf der ersten Ebene unter dem Kurs. Allgemeine Sammelordner wie `Dateien`, `Files`, `Materialien`, `Unterlagen` zählen nicht als Ebene, dort wird eine Ebene tiefer gesucht. So landet z. B. ein Ordner `Tutorien` in einem `[Ü]` Kurs trotzdem unter Tutorium, denn manchmal steckt das Tutorium in der Übung
3. **Kürzel des ILIAS Kurses** (Vorgabe des Nutzers): `[V]` = Vorlesung, `[Ü]` = Übung, `[T]` = Tutorium. Gilt für Dateien direkt im Kurs und für Ordner ohne Schlüsselwort (diese werden 1:1 als Unterordner der Kategorie übernommen, z. B. `Vorlesung/Studenteninitiativen`)
4. **Gruppenname per Schlüsselwort** (z. B. `... - Tut [1. PG]` = Tutorium), für Gruppen ohne Kürzel
5. Nichts davon: ILIAS Ordnername 1:1 direkt im Kursordner

Schlüsselwörter (case insensitive, Umlaute normalisiert, `ue`=`ü`, Vergleich auf ganze Wortteile):

| ILIAS Ordnername enthält | Kategorie |
|---|---|
| vorlesung, lecture, slides, folien, skript | Vorlesung |
| übung, uebung, exercise, übungsgruppe | Übung |
| tutorium, tutorien, tutorial, tut | Tutorium |
| blatt, sheet, übungsblatt, aufgabenblatt | Übungsblätter (falls im Kurs vorhanden, sonst Übung) |
| abgabe, submission, hausaufgabe | Abgaben |
| nichts davon | weiter mit Schritt 3 |

Enthält ein Name Schlüsselwörter mehrerer Kategorien (z. B. `Linux: Folien und Übung`), entscheidet das Kürzel des Kurses, falls es zu einer davon passt, sonst die erste Zeile der Tabelle. Ist ein Treffer nur Teil eines längeren (`übung` in `übungsblatt`), zählt nur der längere.

* Ein ILIAS Ordner, der nur die Kategorie benennt (`Vorlesung`, `Tutorien`, `Vorlesungsfolien`, `Große Übung`), wird selbst zum Kategorieordner. Trägt er mehr im Namen (`Übung 01`, `Tutorium_1`, `Linux: Folien und Übung`), bleibt er als Unterordner der Kategorie erhalten
* Ignore Muster gelten auf jeder Ebene: soll ein neuer Ordner so heißen, bekommt er den Zusatz ` (ILIAS)`
* Kurse ohne erkennbares Semester (z. B. MSDNAA Lizenzkurs) landen unter `_Ohne_Semester/<ILIAS Kursname>/`

* Existiert lokal bereits ein passender Ordner (Vergleich ohne Nummernpräfix, ohne Groß/Kleinschreibung, Umlaute normalisiert), **diesen verwenden**. Also `02_tutorium` wird für Kategorie Tutorium genutzt, kein zweiter Ordner angelegt
* Existiert keiner: neuen Ordner mit nächster freier Nummer anlegen, Schreibweise nach Mehrheit im Kurs (z. B. `03_Tutorium`)
* **Videos** (Wunsch des Nutzers) kommen in einen Unterordner `NN_Videos` des Ordners, in dem sie sonst neben den PDFs lägen, z. B. `02_praktische_informatik_i/01_Vorlesung/01 Vom Problem zum Programm/01_Videos/`. Ein vorhandener lokaler Ordner `videos` wird wiederverwendet. Kein Extra Ordner, wenn ein übernommener ILIAS Ordner schon nach Video klingt (`video`, `aufzeichnung`, `recording`, `screencast`)
* Tiefere Ebenen werden 1:1 aus ILIAS übernommen (gleiche Normalisierung, damit vorhandene Ordner wie `01_tutorium` wiedergefunden werden)

### 4. Config Regeln (Ausnahmen, haben Vorrang)

Ab Semester 2 pflegt der Nutzer die Struktur selbst über die Config bzw. GUI. Regeln pro Kurs, erste passende gewinnt:

```toml
[general]
base_dir = "~/Documents/Uni"                  # einmalig im Setup Wizard eingegeben
root_folder_name = "01_Universität_Mannheim"  # wird unter base_dir angelegt
sync_interval_minutes = 60
max_parallel = 3
semester_pattern = '\((HWS|FSS) (\d{4})\)'
autostart = false
sync_paused = false
username = "<uni-id>"                         # nur Uni-ID, Passwort nie in der Config

[semesters]
"HWS 2026" = "01_SEMESTER"

[[courses]]
ref_id = 1234567
ilias_name = "Marketing (HWS 2026)"
local_dir = "01_marketing"
download_files = true
download_videos = true
download_exercises = true
download_weblinks = true
download_forums = true
download_other = true

  [[courses.rules]]
  match_folder = "Lecture Slides"     # Glob auf ILIAS Ordnernamen
  target = "01_Vorlesung"

  [[courses.rules]]
  match_file = "*blatt*.pdf"          # Glob auf Dateinamen
  target = "03_Übungsblätter"

[[courses]]
ref_id = 7654321
ilias_name = "Formale Grundlagen (HWS 2026)"
local_dir = "05_formale_grundlagen"

[ignore]
local_dirs = ["04_code", "linux_data", "*-MacBook*", "* von *"]
```

Config liegt im Config Verzeichnis von `platformdirs`, wird beim Start validiert, Fehler werden in der GUI verständlich angezeigt.

## Download und Sicherheitsregeln (downloader.py)

* **Nie löschen, nie Nutzerdateien überschreiben**
* Download erst in temporäre Datei im Zielordner (`.part`), nach vollständigem Download atomar umbenennen (`os.replace`)
* Datei in ILIAS geändert (Änderungsdatum oder Größe anders): alte lokale Version umbenennen zu `<name>_alt_<YYYY-MM-DD>.<ext>`, dann neue ablegen
* Lokale Datei vom Nutzer verändert (Hash weicht vom Index ab): nicht anfassen, neue Version als `<name>_ilias_<YYYY-MM-DD>.<ext>` daneben legen
* Datei vom Nutzer verschoben oder gelöscht (laut Index schon geladen): **nicht erneut laden**. In der GUI gibt es "Erneut herunterladen" pro Datei
* Ordner aus `[ignore]` werden nie betreten oder beschrieben
* Dry Run Modus: zeigt geplante Aktionen, schreibt nichts

## Plattformdetails (platform_utils.py)

* Dateinamen bereinigen: verbotene Zeichen für Windows (`<>:"/\|?*`), reservierte Namen (`CON`, `NUL` ...), Leerzeichen/Punkte am Ende, Pfadlänge unter 250 Zeichen halten
* Unicode: macOS liefert Dateinamen in NFD, Windows in NFC. **Alle Vergleiche in NFC**, beim Schreiben NFC verwenden
* Link Dateien: `.url` (Windows, INI Format) bzw. `.webloc` (macOS, plist)
* Basisordner kann in iCloud Drive oder OneDrive liegen: nur atomare Umbenennungen, keine halbfertigen Dateien sichtbar lassen
* Autostart: macOS über LaunchAgent in `~/Library/LaunchAgents` (oder `SMAppService`), Windows über Registry `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. In den Einstellungen an/aus schaltbar

## Status Index (state.py)

SQLite im Datenverzeichnis. Tabelle `items`:

`ref_id, course_ref_id, type, ilias_path, local_path, ilias_mtime, size, sha256, downloaded_at, status`

Status: `ok`, `updated`, `user_modified`, `user_removed`, `error`. Tabelle `sync_runs` mit Start, Ende, Anzahl neu/aktualisiert/Fehler.

## GUI

Die ausführliche Spezifikation für die GUI (Schnittstelle `api.py`, Threading, alle Screens, Fake Backend, Abnahmekriterien) steht in **`GUI_KONTEXT.md`**. Sie ist als eigenständiger Auftrag für einen separaten GUI Agenten geschrieben. Hier nur die Übersicht.

### Tray / Menüleisten Icon

* macOS: Menüleiste oben rechts, Windows: Infobereich unten rechts (`QSystemTrayIcon`)
* Icon Zustände: bereit, synchronisiert gerade (animiert oder anderes Icon), Fehler, Login nötig
* Menü:
  * Jetzt synchronisieren
  * Letzter Sync: <Zeit> (<n> neue Dateien)
  * Neue Dateien ▸ die letzten 10, Klick öffnet die Datei
  * Uni Ordner öffnen
  * Fenster öffnen
  * Sync pausieren / fortsetzen
  * Beenden
* Systembenachrichtigung bei neuen Dateien (gebündelt: "5 neue Dateien in Marketing"), bei Fehlern und bei neuen, nicht zugeordneten Kursen
* macOS: App ohne Dock Icon (`LSUIElement`), nur Menüleiste

### Hauptfenster (klein, übersichtlich)

Seitenleiste links mit diesen Seiten, Design folgt automatisch dem hellen oder dunklen Modus des Systems:

* **Übersicht**: Status, letzter Sync, Liste der zuletzt geladenen Dateien mit Kurs und Zielordner
* **Kurse**: Tabelle ILIAS Kurs → lokaler Ordner, Dropdown zum Ändern, Checkboxen pro Kurs für Videos/Foren/Übungen, Regeln bearbeiten
* **Vorschau**: Dry Run Ergebnis als Baum ("so würde einsortiert"), bevor es echt läuft
* **Einstellungen**: Button "Jetzt synchronisieren", Basisordner, Intervall, Parallelität, Semester Ordner, Autostart, Konto (Uni-ID, abmelden = Keyring und Cookies löschen)
* **Log**: letzte Meldungen, Button "Log Ordner öffnen"

### Setup Wizard (erster Start)

1. Uni-ID + Passwort, Login testen
2. **Basispfad einmalig festlegen**: Eingabefeld + "Durchsuchen" Button (nativer Ordnerdialog). Ab diesem Pfad erstellt das Tool alle Unterordner selbst (siehe Abschnitt "Ordneraufbau ab Basispfad"). Pfad wird geprüft (existiert, beschreibbar, Uni Ordner leer oder noch nicht vorhanden), fehlende Ordner werden nach Bestätigung angelegt. Der erste Sync läuft nur in einen leeren Uni Ordner, ein Uni Ordner mit Inhalt wird abgelehnt. Danach nur noch in den Einstellungen änderbar, mit Warnung und ohne Dateien zu verschieben
3. Kurse abrufen. Ist der Basispfad leer: Struktur vorschlagen und anlegen. Enthält er schon Ordner: Zuordnung zu vorhandenen Kursordnern per Fuzzy Match vorschlagen. Nutzer bestätigt/korrigiert
4. Dry Run anzeigen
5. Erster echter Sync

Sync läuft immer in einem Worker Thread (`QThread`), die GUI blockiert nie.

## Logging

* Rotierende Logdatei im Log Verzeichnis von `platformdirs`
* Niemals Passwort, Cookies, `SAMLResponse` oder vollständige Session URLs loggen

## Build

* PyInstaller Spec für macOS (`.app`, `LSUIElement=true`, Icon `.icns`) und Windows (`.exe`, ohne Konsolenfenster, Icon `.ico`)
* Eigene Icons für hellen und dunklen Modus (macOS Menüleiste: Template Image)
* `requirements.txt` mit gepinnten Versionen

## Meilensteine

1. **Auth**: `ilias_login.py` in `auth.py` überführen, Login + Session Wiederverwendung auf Windows und macOS testen
2. **Crawler Grundlage**: Dashboard Kurse auslesen, Fixtures speichern, Kursliste per CLI ausgeben
3. **Dateien + Ordner**: rekursiv crawlen, Download mit Index, CLI `--dry-run` und `--sync`
4. **Mapper**: Kategorieerkennung, Nutzung bestehender Ordner, Config Regeln, Ignore Liste. Tests mit der Struktur oben
5. ~~**Weitere Objekttypen**~~: entfällt, nur Dateien (Entscheidung des Nutzers 2026-09-25)
6. **GUI**: Tray Icon, Hauptfenster, Setup Wizard, Benachrichtigungen, Hintergrund Intervall
7. **Plattform**: Autostart, Dateinamen/Unicode Tests, Builds für macOS und Windows

Nach jedem Meilenstein lauffähig und getestet, bevor der nächste beginnt.

**Stand (2026-09-25)**: Meilenstein 1 und 2 fertig, Crawler mit 3 parallelen Abrufen und automatischem Relogin. Meilenstein 4 (Mapper) vorgezogen und weitgehend fertig. Meilenstein 3 im CLI fertig: Status Index (`state.py`), Versionierung (`_alt_`, `_ilias_`), `--dry-run` und `--sync` (`sync.py`). Es fehlt noch die echte `api.Api` für die GUI. `--test-sync` bleibt für Tests. **Der echte `01_SEMESTER` wird nie beschrieben** (Vorgabe des Nutzers). GUI (Meilenstein 6) läuft parallel auf Branch `gui` gegen das Fake Backend.

Entscheidung des Nutzers (2026-09-25): **Der erste echte Sync läuft in einen komplett neuen, leeren Basisordner** und lädt alles einmal herunter (auch Videos, auch den Lizenzkurs unter `_Ohne_Semester`). Die bisherigen eigenen Ordner bleiben unberührt, das Tool legt die Struktur selbst an. Umsetzung:
* Erster Sync (Index für diesen Uni Ordner leer) nur, wenn der Uni Ordner fehlt oder leer ist, sonst Abbruch mit Hinweis (`sync.check_first_sync`, `BaseDirCheck.is_empty`)
* Kurse ohne Zuordnung bekommen beim Sync `NN_kursname` Ordner (`Mapper.suggest_courses`), gespeichert in der Config. Kurse derselben Veranstaltung ([V], [Ü], [T], Tut Gruppe) teilen sich einen Ordner
* Der Index liegt pro Uni Ordner im Datenverzeichnis, lokale Pfade relativ zum Uni Ordner

**Erster echter Sync erledigt (2026-09-25)**: Config `base_dir` = `C:\Users\rafik\OneDrive\Uni_neu` (alte Config gesichert als `config_vor_Uni_neu.toml` im Config Verzeichnis). 282 Dateien, 4,0 GB, 0 Fehler; zweiter Lauf: nichts zu tun. Die Kurszuordnungen `NN_kursname` stehen jetzt in der Config.

Nächste Schritte Backend:
* Echte `api.Api` für die GUI (GUI Agent wartet darauf, zuerst dry_run, sync, last_run, recent_files, removed_items, redownload, Konto und Config Methoden, dann check_base_dir, fetch_courses, suggest_mapping)
* Danach Hintergrund Sync, Autostart, Builds

Ausnahme: Die GUI (Meilenstein 6) kann parallel von einem eigenen Agenten gegen `gui/fake_backend.py` entwickelt werden (siehe `GUI_KONTEXT.md`). `api.py` entsteht schrittweise mit den Backend Meilensteinen und ersetzt dann die Attrappe.

## Tests

* Parser gegen gespeicherte HTML Fixtures (keine Live Requests in Tests)
* Mapper: Tabellen getriebene Tests mit der Beispielstruktur (Groß/Kleinschreibung, Umlaute, Präfixe, Konfliktordner, Ignore Liste)
* Downloader: Versionierung, atomares Schreiben, "vom Nutzer verschoben", "vom Nutzer geändert"
* Dateinamen: Windows Sonderzeichen, NFC/NFD

## Rechtliches

ILIAS darf laut Uni ausschließlich für wissenschaftliche Zwecke und Lehre genutzt werden, Urheberrecht der Materialien beachten. Das Tool ist nur für den persönlichen Gebrauch, keine Weitergabe von Materialien, keine Funktion zum Teilen oder Hochladen.
