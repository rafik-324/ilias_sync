<p align="center">
  <img src="ilias_sync/gui/assets/icon_light.svg" alt="ilias sync Logo" height="72">
</p>

<h1 align="center">ilias sync</h1>

<p align="center">
  Lädt alle Dateien aus ILIAS der Universität Mannheim automatisch herunter<br>
  und sortiert sie nach Semester, Kurs und Kategorie ein.
</p>

<p align="center">
  macOS und Windows · Menüleiste bzw. Infobereich · Python 3.11+ · PySide6
</p>

---

## Was das Tool macht

* Meldet sich mit Uni-ID und Passwort an und liest alle Kurse aus "Meine Kurse und Gruppen"
* Lädt alle **Dateien** (auch Videos) und sortiert sie in einen lokalen Uni Ordner ein
* Läuft in der Menüleiste und synchronisiert im Hintergrund, standardmäßig einmal am Tag

Übungen, Foren, Weblinks, Tests und andere ILIAS Objekte werden bewusst **nicht** geladen.

**Grundsätze**: löscht und überschreibt nie etwas · Passwort nur im Schlüsselbund des Systems · nur HTTPS · höchstens 3 parallele Abrufe (einstellbar bis 5) · nur für den persönlichen Gebrauch

## Installation

**Fertige App (macOS)**: `ILIAS Sync.app` nach **Programme** ziehen. Die App ist nicht notarisiert, beim ersten Start daher einmal **Systemeinstellungen › Datenschutz & Sicherheit › Trotzdem öffnen** wählen. Danach erscheint sie nur in der Menüleiste, nicht im Dock.

**Aus dem Quellcode** (Python 3.11 oder neuer):

```bash
git clone https://github.com/rafik-324/ilias_sync.git
cd ilias_sync
python3 -m venv ~/.venvs/ilias-sync          # am besten außerhalb von OneDrive / iCloud
~/.venvs/ilias-sync/bin/pip install -r requirements.txt
~/.venvs/ilias-sync/bin/python -m ilias_sync
```

Unter Windows: `%USERPROFILE%\.venvs\ilias-sync\Scripts\python.exe`.

## Erster Start

Ein Assistent führt durch fünf Schritte:

1. **Anmeldung** mit Uni-ID und Passwort
2. **Uni Ordner** wählen. Eigene Ordner darin sind erlaubt und bleiben unberührt, nur Semesterordner (`01_semester` ...) mit Inhalt werden abgelehnt
3. **Kurse**: Vorschlag eines Kursordners pro Veranstaltung. `[V]`, `[Ü]`, `[T]` und Tutoriengruppe landen im selben Ordner
4. **Vorschau** als Baum, noch ohne etwas zu schreiben
5. **Fertig**: erster Sync und auf Wunsch Autostart, damit das Symbol immer in der Menüleiste bleibt

## Benutzung

Das **Menüleisten Symbol** bietet: Jetzt synchronisieren, letzter Sync, die letzten 10 neuen Dateien, in ILIAS geänderte Dateien, Uni Ordner öffnen, Fenster öffnen, Sync pausieren, Beenden.

Das **Hauptfenster** hat die Seiten Übersicht, Kurse (Ordner, Inhalte pro Kurs, Regeln), Vorschau, Einstellungen und Log. Es folgt dem hellen oder dunklen Modus des Systems.

## Ordnerstruktur

```
<Uni Ordner>/
├── 01_semester/                     chronologisch (HWS 2026, FSS 2027 ...)
│   ├── 01_marketing/                ein Ordner pro Veranstaltung
│   │   ├── 01_vorlesung/
│   │   │   └── 01_videos/           Videos getrennt neben den PDFs
│   │   ├── 02_uebung/
│   │   └── 03_tutorium/
│   └── _neue_kurse/                 noch nicht zugeordnete Kurse
└── _ohne_semester/                  Kurse ohne Semester im Namen
```

* Neue Namen nur aus Kleinbuchstaben, Ziffern, `_`, `-` und `.`, Umlaute als `ae`, `oe`, `ue`, `ss`. Ordnernamen höchstens 40 Zeichen, ganze Pfade unter 250 Zeichen
* Vorhandene Ordner werden wiederverwendet, auch in anderer Schreibweise (`02_Übung` = `02_uebung`)

## Wie einsortiert wird

Das erste Passende gewinnt:

1. **Eigene Regeln** aus der Config (Muster auf Ordner oder Dateinamen)
2. **ILIAS Ordnername** auf der ersten Ebene unter dem Kurs (Sammelordner wie `Dateien` zählen nicht):

   | enthält | Kategorie |
   |---|---|
   | vorlesung, lecture, slides, folien, skript | Vorlesung |
   | übung, uebung, exercise, übungsgruppe | Übung |
   | tutorium, tutorien, tutorial, tut | Tutorium |
   | blatt, sheet, übungsblatt, aufgabenblatt | Übungsblätter (sonst Übung) |
   | abgabe, submission, hausaufgabe | Abgaben |

3. **Kürzel des Kurses**: `[V]` Vorlesung, `[Ü]` Übung, `[T]` Tutorium
4. **Gruppenname**, z. B. `... - Tut [1. PG]`
5. Sonst der ILIAS Ordner 1:1 im Kursordner

## Geänderte und vorhandene Dateien

| Situation | Verhalten |
|---|---|
| Neue Datei | wird geladen (erst `.part`, dann atomar umbenannt) |
| In ILIAS geändert | wird nur gemeldet. Auf Klick kommt sie als `<name>_ilias_<Datum>.<ext>` daneben |
| Name lokal schon belegt | vorhandene Datei bleibt, ILIAS Datei als `<name>_ilias_<Datum>.<ext>` daneben |
| Vom Nutzer verschoben oder gelöscht | wird nicht erneut geladen, "Erneut herunterladen" in der App |
| Pfad zu lang | wird als Fehler angezeigt |

## Konfiguration

Die App pflegt `config.toml` selbst, von Hand bearbeiten geht auch:

```toml
[general]
base_dir = "~/Documents/Uni"   # der Uni Ordner
root_folder_name = ""          # optional: Unterordner darin
sync_interval_minutes = 1440
max_parallel = 3
semester_pattern = '\((HWS|FSS) (\d{4})\)'
username = "<uni-id>"          # nie das Passwort

[semesters]
"HWS 2026" = "01_semester"

[[courses]]
ref_id = 1234567               # stabile ILIAS ID
ilias_name = "Marketing (HWS 2026)"
local_dir = "01_marketing"
download_videos = true

  [[courses.rules]]
  match_file = "*blatt*.pdf"   # oder match_folder
  target = "03_uebungsblaetter"

[ignore]
local_dirs = ["04_code", "* von *"]
```

## Kommandozeile

`python -m ilias_sync` bzw. `/Applications/ILIAS Sync.app/Contents/MacOS/ILIAS Sync`:

| Befehl | Wirkung |
|---|---|
| *(ohne)* | startet die App |
| `--login` / `--logout` | anmelden bzw. Passwort und Cookies löschen |
| `--status` | Pfade, Schlüsselbund, Session, Autostart anzeigen |
| `--crawl [--course REF_ID]` | Kurse und Dateien auflisten |
| `--dry-run` / `--sync` | Sync nur anzeigen bzw. ausführen |
| `--config-dir ORDNER` | getrennte Test Umgebung (oder `ILIAS_SYNC_HOME`) |

## Speicherorte

| | macOS | Windows |
|---|---|---|
| Config, Cookies, Index | `~/Library/Application Support/ilias-sync` | `%LOCALAPPDATA%\ilias-sync` |
| Log | `~/Library/Logs/ilias-sync` | `%LOCALAPPDATA%\ilias-sync\Logs` |
| Passwort | Schlüsselbund (`ilias-sync`) | Anmeldeinformationsverwaltung |
| Autostart | `~/Library/LaunchAgents` | Registry `HKCU\...\Run` |

## Entwicklung

```bash
~/.venvs/ilias-sync/bin/pip install -r requirements-dev.txt
~/.venvs/ilias-sync/bin/python -m pytest                       # Tests, ohne Live Requests
~/.venvs/ilias-sync/bin/python -m ruff check .
~/.venvs/ilias-sync/bin/python -m ilias_sync.gui.app --fake    # GUI mit Beispieldaten (--fake-setup: mit Assistent)
```

macOS App bauen (Ergebnis `ILIAS Sync.app`, ad hoc signiert):

```bash
~/.venvs/ilias-sync/bin/pyinstaller packaging/ilias_sync_macos.spec --noconfirm \
  --distpath ~/dev/ilias_sync-build/dist --workpath ~/dev/ilias_sync-build/build
```

Die GUI (`ilias_sync/gui/`) spricht nur über `api.py` mit dem Backend, kein Backend Modul importiert Qt. Details in `CLAUDE.md` und `GUI_KONTEXT.md`.

## Rechtliches

ILIAS darf laut Universität Mannheim nur für Wissenschaft und Lehre genutzt werden, die Materialien sind urheberrechtlich geschützt. Das Tool ist nur für den persönlichen Gebrauch, lädt nur Inhalte, auf die der angemeldete Nutzer ohnehin Zugriff hat, und gibt nichts weiter. Kein offizielles Projekt der Universität Mannheim oder von ILIAS.
