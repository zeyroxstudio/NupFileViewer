@echo off
cd /d "%~dp0"
if exist "dist\NupFileViewer\NupFileViewer.exe" (
  start "" "dist\NupFileViewer\NupFileViewer.exe" %*
) else (
  start "" ".venv\Scripts\pythonw.exe" "main.py" %*
)
