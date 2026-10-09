; Inno Setup Skript für den Windows Installer ILIAS-Sync-<version>-Setup.exe.
;
; Nicht direkt aufrufen, sondern über packaging\make_installer.ps1 (baut vorher die App
; und übergibt Version, Quell und Ausgabeordner). Von Hand:
;   ISCC.exe /DAppVersion=0.1.0 /DSourceDir=<ordner>\dist\ILIAS Sync /DOutputDir=<ordner> packaging\ilias_sync_windows.iss
;
; Installiert ohne Administratorrechte nach %LOCALAPPDATA%\Programs\ILIAS Sync. Ein Update ist
; einfach die neue Setup Datei: gleicher Ordner, Config, Passwort und Index bleiben erhalten.
; Deinstallieren entfernt nur das Programm und den Autostart Eintrag, nie Config oder Downloads.

#ifndef AppVersion
  #error AppVersion fehlt (/DAppVersion=...)
#endif
#ifndef SourceDir
  #error SourceDir fehlt (/DSourceDir=...)
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

#define AppName "ILIAS Sync"
#define AppExe "ILIAS Sync.exe"

[Setup]
; Feste AppId: daran erkennt Windows ein Update derselben App. Nie ändern
AppId={{6B0E2F4A-3C1D-4E8B-9A57-2D4F1C8E7B31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=ILIAS Sync
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#OutputDir}
OutputBaseFilename=ILIAS-Sync-{#AppVersion}-Setup
SetupIconFile=icons\ilias_sync.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Laufende App vor dem Update schließen und danach wieder starten
CloseApplications=yes
RestartApplications=yes

[Languages]
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Programmdateien der Vorversion entfernen, damit keine veralteten Bibliotheken liegen bleiben.
; Betrifft nur den Installationsordner, nie Config, Index oder Downloads
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
// Autostart Eintrag der App (platform_utils.AUTOSTART_NAME) beim Deinstallieren entfernen,
// sonst zeigt er auf ein gelöschtes Programm
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', '{#AppName}');
end;
