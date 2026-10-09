# Baut ILIAS Sync.exe und packt sie als Installer ILIAS-Sync-<version>-Setup.exe (Inno Setup).
# Daneben liegt eine SHA-256 Datei.
#
# Aufruf aus dem Projektordner:
#   powershell -ExecutionPolicy Bypass -File packaging\make_installer.ps1 [-Out AUSGABEORDNER]
# Standard für AUSGABEORDNER ist %USERPROFILE%\dev\ilias_sync-build (außerhalb von OneDrive).
# Python: $env:ILIAS_SYNC_PYTHON, sonst %USERPROFILE%\.venvs\ilias-sync\Scripts\python.exe
# Inno Setup 6 nötig, z. B. per: winget install --id JRSoftware.InnoSetup -e --scope user
param(
    [string]$Out = (Join-Path $env:USERPROFILE "dev\ilias_sync-build")
)
$ErrorActionPreference = "Stop"

$Project = Split-Path -Parent $PSScriptRoot
$Python = if ($env:ILIAS_SYNC_PYTHON) { $env:ILIAS_SYNC_PYTHON } else { Join-Path $env:USERPROFILE ".venvs\ilias-sync\Scripts\python.exe" }
$AppName = "ILIAS Sync"

$Iscc = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) { throw "Inno Setup 6 (ISCC.exe) nicht gefunden" }

$Version = & $Python -c "import sys; sys.path.insert(0, r'$Project'); import ilias_sync; print(ilias_sync.__version__)"
if ($LASTEXITCODE -ne 0) { throw "Version konnte nicht gelesen werden" }

Write-Host "== App bauen (Version $Version)"
Push-Location $Project
try {
    & $Python -m PyInstaller packaging\ilias_sync_windows.spec --noconfirm `
        --distpath (Join-Path $Out "dist") --workpath (Join-Path $Out "build")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller fehlgeschlagen" }

    Write-Host "== Installer packen"
    $Source = Join-Path $Out "dist\$AppName"
    & $Iscc /Q "/DAppVersion=$Version" "/DSourceDir=$Source" "/DOutputDir=$Out" packaging\ilias_sync_windows.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup fehlgeschlagen" }
} finally {
    Pop-Location
}

$Setup = Join-Path $Out "ILIAS-Sync-$Version-Setup.exe"
$Hash = (Get-FileHash $Setup -Algorithm SHA256).Hash.ToLower()
# Gleiches Format wie shasum: "<hash>  <dateiname>"
"$Hash  $(Split-Path -Leaf $Setup)" | Set-Content -Encoding ascii "$Setup.sha256"

Write-Host "== Fertig"
Write-Host $Setup
Get-Content "$Setup.sha256"
