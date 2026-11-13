# NupFileViewer

<img src="nupviewer/logo.svg" alt="" width="64">

A Windows viewer for NUP v1 archives. Browse and extract Raw and Zstandard files, preview text or hex, and check file hashes.

[Download the latest portable build](https://github.com/zeyroxstudio/NupFileViewer/releases/latest)

## Getting started

Extract the ZIP to a folder and run `NupFileViewer.exe`. Choose English, Italian, Spanish, or French on first launch. To change it later, use **Options → Language** and restart the app.

Drop a `.nup` file on the window or press **Ctrl+O**. In **Overview**, pick a package to see its title, display version, and raw NCM version. Use **Details…** to see the dump name, Build ID, and any localized titles and icons. **Contents** lets you browse, search, preview, and extract files. Press **Ctrl+F** to search the selected package.

## Integrity

Opening an archive checks its index. Use **Tools → Verify all** to check the SHA-256 hashes of the files themselves, or verify a selection from the file list. The result appears in Overview and in the status bar. A successful selection check applies only to those files.

Extraction keeps the paths inside the archive and refuses to overwrite existing files. Each extracted file is checked against its SHA-256 hash before it is moved into place.

## Updates

The portable build checks GitHub for a newer stable release at startup. You can turn this off or check manually under **Options**. If you accept an update, the app checks the ZIP against GitHub's SHA-256 digest, installs it, and restarts. A failed restart triggers a rollback and writes `update-error.txt`.

## Build from source

Use Python 3.13 on Windows x64.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\build.ps1
```

The build produces `dist/NupFileViewer-portable-win-x64.zip`. For a release, update `__version__` in `nupviewer/__init__.py` and attach the ZIP to a stable GitHub release tagged `v<version>`.

## Format and credits

Supports NUP v1 with Raw or single-frame Zstandard content. External Zstandard dictionaries are unsupported. NACP and NPDM data are read as opaque metadata.

The parser follows [a55uka's NUP specification](https://github.com/a55uka/nup/blob/meow/SPEC.md). The layout takes inspiration from [NxFileViewer](https://github.com/Myster-Tee/NxFileViewer). See [LICENSE](LICENSE) and [third-party notices](THIRD-PARTY-NOTICES.md).
