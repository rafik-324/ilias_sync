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
* `pytest` für Tests

## Architektur

```
ilias_sync/
  __main__.py          Einstieg: GUI starten oder CLI Modus (--sync, --dry-run)
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
  downloader.py        Downloads, atomares Schreiben, Versionierung
  state.py             SQLite Index
  config.py            Laden, Validieren, Standardwerte
  scheduler.py         Intervall Sync im Hintergrund (Logik ohne Qt)
  api.py               Fassade für die GUI: einzige Schnittstelle zwischen GUI und Backend
  gui/
    app.py             Einstieg: QApplication, Einzelinstanz, Tray, Wizard bei Erststart
    tray.py            Tray / Menüleisten Icon + Menü
    window.py          Hauptfenster
    setup_wizard.py    Erststart: Login, Basisordner, Kurszuordnung
    worker.py          QThread Worker für Backend Aufrufe
    scheduler_gui.py   QTimer, löst den Intervall Sync aus
    fake_backend.py    Attrappe von api.py für Entwicklung und Tests
    icons/
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

## Was heruntergeladen wird ("alles")

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

### 3. Kategorie (Standardregeln, automatisch)

Erste Ebene unter dem Kurs in ILIAS wird per Schlüsselwort erkannt (case insensitive, Umlaute normalisiert, `ue`=`ü`):

| ILIAS Ordnername enthält | Kategorie |
|---|---|
| vorlesung, lecture, slides, folien, skript | Vorlesung |
| übung, uebung, exercise, übungsgruppe | Übung |
| tutorium, tutorial | Tutorium |
| blatt, sheet, übungsblatt, aufgabenblatt | Übungsblätter (falls im Kurs vorhanden, sonst Übung) |
| abgabe, submission, hausaufgabe | Abgaben |
| nichts davon | ILIAS Ordnername 1:1 |

* Existiert lokal bereits ein passender Ordner (Vergleich ohne Nummernpräfix, ohne Groß/Kleinschreibung, Umlaute normalisiert), **diesen verwenden**. Also `02_tutorium` wird für Kategorie Tutorium genutzt, kein zweiter Ordner angelegt
* Existiert keiner: neuen Ordner mit nächster freier Nummer anlegen, Schreibweise nach Mehrheit im Kurs (z. B. `03_Tutorium`)
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

* **Übersicht**: Status, letzter Sync, Liste der zuletzt geladenen Dateien mit Kurs und Zielordner
* **Kurse**: Tabelle ILIAS Kurs → lokaler Ordner, Dropdown zum Ändern, Checkboxen pro Kurs für Videos/Foren/Übungen, Regeln bearbeiten
* **Vorschau**: Dry Run Ergebnis als Baum ("so würde einsortiert"), bevor es echt läuft
* **Einstellungen**: Basisordner, Intervall, Parallelität, Autostart, Konto (Uni-ID, abmelden = Keyring und Cookies löschen)
* **Log**: letzte Meldungen, Button "Log Ordner öffnen"

### Setup Wizard (erster Start)

1. Uni-ID + Passwort, Login testen
2. **Basispfad einmalig festlegen**: Eingabefeld + "Durchsuchen" Button (nativer Ordnerdialog). Ab diesem Pfad erstellt das Tool alle Unterordner selbst (siehe Abschnitt "Ordneraufbau ab Basispfad"). Pfad wird geprüft (existiert, beschreibbar), fehlende Ordner werden nach Bestätigung angelegt. Danach nur noch in den Einstellungen änderbar, mit Warnung und ohne Dateien zu verschieben
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
5. **Weitere Objekttypen**: Übungen, Weblinks, Videos (Opencast recherchieren), Foren
6. **GUI**: Tray Icon, Hauptfenster, Setup Wizard, Benachrichtigungen, Hintergrund Intervall
7. **Plattform**: Autostart, Dateinamen/Unicode Tests, Builds für macOS und Windows

Nach jedem Meilenstein lauffähig und getestet, bevor der nächste beginnt.

Ausnahme: Die GUI (Meilenstein 6) kann parallel von einem eigenen Agenten gegen `gui/fake_backend.py` entwickelt werden (siehe `GUI_KONTEXT.md`). `api.py` entsteht schrittweise mit den Backend Meilensteinen und ersetzt dann die Attrappe.

## Tests

* Parser gegen gespeicherte HTML Fixtures (keine Live Requests in Tests)
* Mapper: Tabellen getriebene Tests mit der Beispielstruktur (Groß/Kleinschreibung, Umlaute, Präfixe, Konfliktordner, Ignore Liste)
* Downloader: Versionierung, atomares Schreiben, "vom Nutzer verschoben", "vom Nutzer geändert"
* Dateinamen: Windows Sonderzeichen, NFC/NFD

## Rechtliches

ILIAS darf laut Uni ausschließlich für wissenschaftliche Zwecke und Lehre genutzt werden, Urheberrecht der Materialien beachten. Das Tool ist nur für den persönlichen Gebrauch, keine Weitergabe von Materialien, keine Funktion zum Teilen oder Hochladen.
