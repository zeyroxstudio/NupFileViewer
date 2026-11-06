$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& .\.venv\Scripts\python.exe tools/build_icon.py nupviewer/logo.svg build/NupFileViewer.ico
if ($LASTEXITCODE -ne 0) { throw 'Icon build failed' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed --onedir --name NupFileViewer --icon build/NupFileViewer.ico --add-data 'nupviewer/logo.svg:nupviewer' --add-data 'nupviewer/apply_update.ps1:nupviewer' main.py
if ($LASTEXITCODE -ne 0) { throw 'Application build failed' }
& .\.venv\Scripts\python.exe tools/package_release.py
if ($LASTEXITCODE -ne 0) { throw 'Release packaging failed' }
