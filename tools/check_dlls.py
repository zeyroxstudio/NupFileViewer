"""Build troubleshooting: find unresolved named imports in bundled DLLs."""
from pathlib import Path
import pefile

root = Path('dist/NupFileViewer/_internal')
paths = {p.name.lower(): p for p in root.rglob('*') if p.suffix.lower() in ('.dll', '.pyd')}
exports = {}
for name, path in paths.items():
    try:
        pe = pefile.PE(str(path), fast_load=True)
        pe.parse_data_directories(directories=[0])
        exports[name] = {s.name for s in getattr(pe, 'DIRECTORY_ENTRY_EXPORT', type('E', (), {'symbols': []})).symbols}
        pe.close()
    except pefile.PEFormatError:
        pass
for path in paths.values():
    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(directories=[1])
    for imp in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', []):
        name = imp.dll.decode().lower()
        if name in exports:
            missing = [s.name.decode() for s in imp.imports if s.name and s.name not in exports[name]]
            if missing:
                print(path.name, '->', paths[name], missing[:12])
    pe.close()
