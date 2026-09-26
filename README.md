<p align="center">
  <img src="ilias_sync/gui/assets/icon_light.svg" alt="ilias sync Logo" height="72">
</p>

<h1 align="center">ilias sync</h1>

<p align="center">
  Lädt alle Materialien aus ILIAS der Universität Mannheim automatisch herunter<br>
  und sortiert sie in eine saubere lokale Ordnerstruktur ein.
</p>

<p align="center">
  macOS und Windows · Menüleiste bzw. Infobereich · Python 3.11+ · PySide6
</p>

---

## Inhalt

* [Was das Tool macht](#was-das-tool-macht)
* [Grundsätze](#grundsätze)
* [Installation](#installation)
* [Erster Start](#erster-start)
* [Benutzung](#benutzung)
* [Ordnerstruktur](#ordnerstruktur)
* [Wie einsortiert wird](#wie-einsortiert-wird)
* [Geänderte und vorhandene Dateien](#geänderte-und-vorhandene-dateien)
* [Konfiguration](#konfiguration)
* [Kommandozeile](#kommandozeile)
* [Speicherorte](#speicherorte)
* [Entwicklung](#entwicklung)
* [App bauen](#app-bauen)
* [Architektur](#architektur)
* [Stand und Ausblick](#stand-und-ausblick)
* [Rechtliches](#rechtliches)

---

## Was das Tool macht

* Meldet sich mit Uni-ID und Passwort über das Shibboleth Login der Uni bei ILIAS an (kein 2FA nötig)
* Liest alle Kurse und Gruppen aus "Meine Kurse und Gruppen" auf dem Dashboard, funktioniert also für jede und jeden Studierenden
* Durchsucht jeden Kurs rekursiv (Ordner, Sitzungen, Gruppen) und lädt alle **Dateien** herunter, auch Videos, die in ILIAS als Datei liegen
* Sortiert alles nach Semester, Kurs und Kategorie (Vorlesung, Übung, Tutorium ...) in einen lokalen Uni Ordner
* Läuft unauffällig in der Menüleiste (macOS) bzw. im Infobereich (Windows) und synchronisiert in einem festen Intervall
* Meldet neue Dateien gebündelt als Systembenachrichtigung ("5 neue Dateien in Marketing")
* Zeigt vor jedem Sync auf Wunsch eine Vorschau, was wohin einsortiert würde

Übungen, Foren, Weblinks, Tests und andere ILIAS Objekttypen werden derzeit bewusst **nicht** geladen.

## Grundsätze

* **Nie Daten verlieren**: Das Tool löscht nie etwas und überschreibt nie eine vorhandene Datei
* **Sicherheit vor Komfort**: eigener Code, wenige Abhängigkeiten mit festen Versionen, keine fremden ILIAS Tools. Das Passwort liegt nur im Schlüsselbund des Systems, nie in Config, Log oder Fehlermeldungen. Nur HTTPS mit Zertifikatsprüfung
* **Server schonen**: höchstens 3 parallele Abrufe (einstellbar bis 5), kleine Pausen, eigener User Agent
* **Plattformunabhängig**: gleiches Verhalten auf macOS und Windows, Dateinamen sind auf beiden Systemen gültig
* **Nur für den persönlichen Gebrauch**: keine Funktion zum Teilen oder Hochladen

## Installation

### Fertige App (macOS)

1. `ILIAS Sync.app` in den Ordner **Programme** ziehen
2. Beim ersten Start meldet macOS, dass die App von einem nicht verifizierten Entwickler stammt (die App ist nicht notarisiert). Dann einmal **Systemeinstellungen › Datenschutz & Sicherheit › Trotzdem öffnen** wählen, oder im Finder per Rechtsklick **Öffnen**

Die App erscheint danach nur oben rechts in der Menüleiste, nicht im Dock.

### Aus dem Quellcode

Voraussetzung: Python 3.11 oder neuer (auf macOS z. B. über Homebrew, das System Python 3.9 ist zu alt).

```bash
git clone https://github.com/rafik-324/ilias_sync.git
cd ilias_sync

# virtuelle Umgebung am besten außerhalb von Cloud Ordnern (OneDrive, iCloud)
python3 -m venv ~/.venvs/ilias-sync
~/.venvs/ilias-sync/bin/pip install -r requirements.txt

# App starten
~/.venvs/ilias-sync/bin/python -m ilias_sync
```

Unter Windows lautet der Pfad zum Python der Umgebung `%USERPROFILE%\.venvs\ilias-sync\Scripts\python.exe`.

## Erster Start

Beim ersten Start führt ein Einrichtungsassistent durch fünf Schritte:

1. **Anmeldung**: Uni-ID und Passwort. Das Passwort landet im Schlüsselbund (macOS) bzw. in der Anmeldeinformationsverwaltung (Windows)
2. **Basisordner**: einmalig festlegen, wo der Uni Ordner entstehen soll. Der Ordner muss beim ersten Sync leer sein. Liegt er in einem Cloud Ordner oder ist der Pfad sehr lang, erscheint ein Hinweis
3. **Kurse**: Das Tool ruft alle Kurse ab und schlägt pro Veranstaltung einen Kursordner vor. Mehrere ILIAS Kurse einer Veranstaltung (`[V]`, `[Ü]`, `[T]`, Tutoriengruppe) landen zusammen in einem Ordner
4. **Vorschau**: zeigt als Baum, was wohin geladen würde, noch ohne etwas zu schreiben
5. **Erster Sync**: lädt alles einmal herunter. Je nach Videos können das mehrere GB sein

## Benutzung

**Menüleisten Symbol** (bzw. Infobereich unter Windows)

* Jetzt synchronisieren
* Letzter Sync mit Anzahl neuer Dateien
* Neue Dateien: die letzten 10, ein Klick öffnet die Datei
* In ILIAS geänderte Dateien (falls vorhanden)
* Uni Ordner öffnen, Fenster öffnen
* Sync pausieren bzw. fortsetzen, Beenden

**Hauptfenster**

| Seite | Inhalt |
|---|---|
| Übersicht | Status, letzter und nächster Sync, zuletzt geladene Dateien, in ILIAS geänderte Dateien, Fehler mit Details |
| Kurse | ILIAS Kurs und zugehöriger lokaler Ordner, Ordner ändern, Inhalte pro Kurs abschalten, Regeln bearbeiten |
| Vorschau | Probelauf als Baum: neu, geändert, Name belegt, Pfad zu lang |
| Einstellungen | Basisordner, Intervall, Parallelität, Semester Ordner, Autostart, Konto |
| Log | letzte Meldungen, Log Ordner öffnen |

Das Design folgt automatisch dem hellen oder dunklen Modus des Systems.

## Ordnerstruktur

Unter dem Basisordner legt das Tool diese Struktur an:

```
<Basisordner>/
└── 01_Universität_Mannheim/
    ├── 01_semester/                         chronologisch nach Semester (HWS 2026, FSS 2027 ...)
    │   ├── 01_marketing/                    ein Ordner pro Veranstaltung
    │   │   ├── 01_vorlesung/
    │   │   ├── 02_uebung/
    │   │   └── 03_tutorium/
    │   └── 03_praktische_informatik_i/
    │       └── 01_vorlesung/
    │           └── 01_vom_problem_zum_programm/
    │               └── 01_videos/           Videos liegen getrennt neben den PDFs
    ├── _neue_kurse/                         Kurse, die noch keinem Ordner zugeordnet sind
    └── _ohne_semester/                      Kurse ohne Semester im Namen
```

**Namensregel für alles, was das Tool neu anlegt**: nur Kleinbuchstaben, Ziffern, `_`, `-` und Punkt. Leerzeichen werden zu `_`, Umlaute zu `ae`, `oe`, `ue`, `ss`. Aus `Ü1 Übungsskript Kernlösung.pdf` wird `ue1_uebungsskript_kernloesung.pdf`. Neue Ordnernamen sind höchstens 40 Zeichen lang, gekürzt wird am Wortende mit einem kurzen, stabilen Kennzeichen (`09a_objektorientierte_ec0c`). Der gesamte Pfad bleibt unter 250 Zeichen, damit er auch unter Windows funktioniert.

**Vorhandene Ordner werden weiterverwendet**, auch in anderer Schreibweise. `02_Übung`, `02_uebung` und `02_UEBUNG` gelten als derselbe Ordner. Es entstehen keine Doppelordner.

## Wie einsortiert wird

Für jede Datei bestimmt das Tool die Kategorie in dieser Reihenfolge, das erste Passende gewinnt:

1. **Eigene Regeln** aus der Config (pro Kurs, Muster auf Ordner oder Dateinamen)
2. **ILIAS Ordnername** auf der ersten Ebene unter dem Kurs, per Schlüsselwort:

   | Ordnername enthält | Kategorie |
   |---|---|
   | vorlesung, lecture, slides, folien, skript | Vorlesung |
   | übung, uebung, exercise, übungsgruppe | Übung |
   | tutorium, tutorien, tutorial, tut | Tutorium |
   | blatt, sheet, übungsblatt, aufgabenblatt | Übungsblätter (sonst Übung) |
   | abgabe, submission, hausaufgabe | Abgaben |

3. **Kürzel des ILIAS Kurses**: `[V]` Vorlesung, `[Ü]` Übung, `[T]` Tutorium
4. **Gruppenname**, z. B. `... - Tut [1. PG]` wird Tutorium
5. Sonst wird der ILIAS Ordner 1:1 direkt im Kursordner übernommen

Sammelordner wie `Dateien` oder `Materialien` zählen nicht als Ebene. Tiefere Ordner werden 1:1 aus ILIAS übernommen.

## Geänderte und vorhandene Dateien

| Situation | Verhalten |
|---|---|
| Neue Datei in ILIAS | wird geladen, erst als `.part`, dann atomar umbenannt |
| Datei in ILIAS geändert | wird **nur gemeldet**, nicht automatisch geladen. Auf Klick kommt die neue Version als `<name>_ilias_<Datum>.<ext>` neben die vorhandene Datei, oder die Änderung wird ignoriert |
| Zielname lokal schon belegt | vorhandene Datei bleibt, die ILIAS Datei kommt als `<name>_ilias_<Datum>.<ext>` daneben |
| Datei vom Nutzer verschoben oder gelöscht | wird nicht erneut geladen, "Erneut herunterladen" ist in der App möglich |
| Pfad zu lang | wird in Vorschau und Sync als Fehler angezeigt, nicht still übersprungen |
| Ordner aus der Ignore Liste | werden nie betreten oder beschrieben |

## Konfiguration

Die Config wird von der App angelegt und gepflegt, lässt sich aber auch von Hand bearbeiten (`config.toml`, Speicherort siehe unten).

```toml
[general]
base_dir = "~/Documents/Uni"
root_folder_name = "01_Universität_Mannheim"
sync_interval_minutes = 60
max_parallel = 3                              # 1 bis 5
semester_pattern = '\((HWS|FSS) (\d{4})\)'
autostart = false
sync_paused = false
username = "<uni-id>"                         # nur die Uni-ID, nie das Passwort

[semesters]
"HWS 2026" = "01_semester"

[[courses]]
ref_id = 1234567                              # stabile ILIAS ID, Primärschlüssel
ilias_name = "Marketing (HWS 2026)"
local_dir = "01_marketing"
download_files = true
download_videos = true

  [[courses.rules]]
  match_folder = "Lecture Slides"             # Glob auf ILIAS Ordnernamen
  target = "01_vorlesung"

  [[courses.rules]]
  match_file = "*blatt*.pdf"                  # Glob auf Dateinamen
  target = "03_uebungsblaetter"

[ignore]
local_dirs = ["04_code", "linux_data", "*-MacBook*", "* von *"]
```

## Kommandozeile

Alle Befehle mit `python -m ilias_sync` aus dem Quellcode, oder direkt mit dem Programm in der App (`/Applications/ILIAS Sync.app/Contents/MacOS/ILIAS Sync`).

| Befehl | Wirkung |
|---|---|
| *(ohne Argumente)* | startet die App |
| `--login` | Anmeldung testen und Session speichern (fragt nach dem Passwort) |
| `--logout` | Passwort und Cookies löschen |
| `--status` | Pfade, Schlüsselbund, Session und Autostart anzeigen, ändert nichts |
| `--crawl [--course REF_ID] [--output DATEI]` | alle Kurse, Ordner und Dateien auflisten, nichts herunterladen |
| `--dry-run` | zeigen, was ein Sync tun würde, nichts schreiben |
| `--sync` | echter Sync in den Uni Ordner aus der Config (erster Sync nur in einen leeren Ordner) |
| `--config-dir ORDNER` | Config, Daten und Log in ORDNER statt am Standard Ort |
| `-v` | ausführliche Ausgabe |

Dasselbe wie `--config-dir` bewirkt die Umgebungsvariable `ILIAS_SYNC_HOME`. Damit lässt sich die App gefahrlos mit einer getrennten Test Umgebung starten, die eigene Einzelinstanz Sperre bekommt die Test App automatisch.

## Speicherorte

| | macOS | Windows |
|---|---|---|
| Config (`config.toml`) | `~/Library/Application Support/ilias-sync` | `%LOCALAPPDATA%\ilias-sync` |
| Daten (Cookies, Index) | wie Config | wie Config |
| Log (`ilias-sync.log`) | `~/Library/Logs/ilias-sync` | `%LOCALAPPDATA%\ilias-sync\Logs` |
| Passwort | Schlüsselbund, Dienst `ilias-sync` | Anmeldeinformationsverwaltung |
| Autostart | LaunchAgent in `~/Library/LaunchAgents` | Registry `HKCU\...\Run` |

Die Pfade stammen aus `platformdirs`. Die Cookie Datei hat die Rechte 600. Der Status Index ist eine SQLite Datenbank pro Uni Ordner.

## Entwicklung

```bash
~/.venvs/ilias-sync/bin/pip install -r requirements-dev.txt

# Tests (Parser gegen gespeicherte HTML Fixtures, keine Live Requests)
~/.venvs/ilias-sync/bin/python -m pytest

# Linting
~/.venvs/ilias-sync/bin/python -m ruff check .

# GUI mit Beispieldaten statt echtem ILIAS
~/.venvs/ilias-sync/bin/python -m ilias_sync.gui.app --fake
~/.venvs/ilias-sync/bin/python -m ilias_sync.gui.app --fake-setup    # mit Einrichtungsassistent
```

Konventionen: Code Kommentare und UI Texte auf Deutsch, Zeilenlänge 120, Abhängigkeiten in `requirements*.txt` mit festen Versionen.

## App bauen

### macOS

```bash
~/.venvs/ilias-sync/bin/pyinstaller packaging/ilias_sync_macos.spec --noconfirm \
  --distpath ~/dev/ilias_sync-build/dist --workpath ~/dev/ilias_sync-build/build
```

Ergebnis ist `ILIAS Sync.app` (Bundle ID `de.rafik.ilias-sync`, nur Menüleiste ohne Dock Icon, ad hoc signiert). Den Build am besten außerhalb von Cloud Ordnern ablegen. Die App Icons erzeugt `packaging/make_icons.py` aus den SVGs.

### Windows

Die Windows Spec (`.exe` ohne Konsolenfenster, Icon `packaging/icons/ilias_sync.ico`) folgt.

## Architektur

```
ilias_sync/
  __main__.py        Einstieg: App oder Kommandozeile
  auth.py            SAML Login über Shibboleth, Session, Cookies, Schlüsselbund
  crawler.py         Kurse und Objekte rekursiv erfassen
  mapper.py          ILIAS Pfad zu lokalem Zielpfad (Kategorien, Regeln, vorhandene Ordner)
  downloader.py      Downloads, atomares Schreiben, Hash, Fehlertexte
  sync.py            Sync planen und ausführen
  state.py           SQLite Index
  config.py          Config laden, prüfen, atomar speichern
  platform_utils.py  Dateinamen, Pfadlängen, Autostart
  api.py             Vertrag zwischen GUI und Backend (Datentypen, Fehler)
  backend.py         Umsetzung von api.Api
  gui/               PySide6 Oberfläche: Tray, Hauptfenster, Assistent, Worker Threads
packaging/           PyInstaller Spec, Startdatei, Icons
tests/               pytest und pytest-qt, HTML Fixtures
```

Die GUI spricht ausschließlich über `api.py` mit dem Backend, kein Backend Modul importiert Qt. Dadurch ist alles auch ohne Oberfläche per Kommandozeile nutzbar und testbar. Langlaufende Aufrufe laufen in Worker Threads, die Oberfläche blockiert nie.

## Stand und Ausblick

Fertig:

* Login, Crawler, Einsortierung, Download mit Index, Vorschau und Sync
* Oberfläche mit Tray, Hauptfenster und Einrichtungsassistent, hell und dunkel
* macOS App mit eigenem Icon, getestet mit echtem Sync (283 Dateien, 4 GB, ohne Fehler)

Geplant:

* Build für Windows
* Autostart in der fertigen App einschalten
* DMG zum einfachen Installieren auf anderen Macs

## Rechtliches

ILIAS darf laut Universität Mannheim ausschließlich für wissenschaftliche Zwecke und die Lehre genutzt werden. Die Materialien sind urheberrechtlich geschützt. Dieses Tool ist nur für den persönlichen Gebrauch gedacht und lädt ausschließlich Inhalte, auf die der angemeldete Nutzer ohnehin Zugriff hat. Materialien dürfen nicht weitergegeben werden. Das Projekt steht in keiner Verbindung zur Universität Mannheim oder zu den Entwicklern von ILIAS.
