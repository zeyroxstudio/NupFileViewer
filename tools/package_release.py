"""Finalize a Windows portable build and retain its source and license notices."""
from importlib.metadata import distribution
from pathlib import Path
import runpy
import shutil
import sys

root = Path(__file__).resolve().parents[1]
release = root / 'dist' / 'NupFileViewer'
internal = release / '_internal'
if not (release / 'NupFileViewer.exe').is_file():
    raise SystemExit('Build the executable first.')

# Qt 6.12 uses Windows' unversioned ICU API. PATH may contain a different
# ICU build (for example devkitPro) with version-suffixed symbols. Never bundle it.
for path in internal.glob('icu*.dll'):
    path.unlink()

qt_dir = Path(distribution('PySide6-Essentials').locate_file('PySide6'))
for name in ('vcruntime140.dll', 'vcruntime140_1.dll'):
    if (qt_dir / name).is_file():
        shutil.copy2(qt_dir / name, internal / name)

for name in ('LICENSE', 'THIRD-PARTY-NOTICES.md'):
    shutil.copy2(root / name, release / name)
readme = (root / 'README.md').read_text(encoding='utf-8')
(release / 'README.md').write_text(
    readme.replace('src="nupviewer/logo.svg"', 'src="source/nupviewer/logo.svg"'), encoding='utf-8'
)

source = release / 'source'
source.mkdir(exist_ok=True)
for folder in ('nupviewer', 'tests', 'tools'):
    target = source / folder
    target.mkdir(exist_ok=True)
    for file in (root / folder).glob('*.py'):
        shutil.copy2(file, target / file.name)
shutil.copy2(root / 'nupviewer' / 'apply_update.ps1', source / 'nupviewer' / 'apply_update.ps1')
shutil.copy2(root / 'nupviewer' / 'logo.svg', source / 'nupviewer' / 'logo.svg')
shutil.copy2(root / 'nupviewer' / 'github.svg', source / 'nupviewer' / 'github.svg')
translations = internal / 'translations'
translations.mkdir(exist_ok=True)
for language in ('it', 'es', 'fr'):
    name = f'qtbase_{language}.qm'
    shutil.copy2(qt_dir / 'translations' / name, translations / name)
for name in ('main.py', 'requirements.txt', 'build.ps1', 'README.md', 'LICENSE', 'THIRD-PARTY-NOTICES.md', 'CHANGELOG.md'):
    shutil.copy2(root / name, source / name)
(source / 'docs').mkdir(exist_ok=True)
for name in ('NUP-SPEC.reference.md', 'NUP-LICENSE.txt'):
    shutil.copy2(root / 'docs' / name, source / 'docs' / name)
licenses = release / 'licenses'
licenses.mkdir(exist_ok=True)
for name in ('PySide6-Essentials', 'shiboken6', 'zstandard', 'pyinstaller'):
    dist = distribution(name)
    target = licenses / name
    target.mkdir(exist_ok=True)
    for file in dist.files or []:
        if '.dist-info/licenses/' in str(file).replace('\\', '/'):
            src = Path(dist.locate_file(file))
            if src.is_file():
                shutil.copy2(src, target / src.name)
    (target / 'package-metadata.txt').write_text(dist.read_text('METADATA') or '', encoding='utf-8')
for file in (root / 'docs' / 'licenses').glob('*.txt'):
    shutil.copy2(file, licenses / file.name)
    (source / 'docs' / 'licenses').mkdir(exist_ok=True)
    shutil.copy2(file, source / 'docs' / 'licenses' / file.name)
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if python_license.is_file():
    shutil.copy2(python_license, licenses / 'Python-LICENSE.txt')
version = runpy.run_path(str(root / 'nupviewer' / '__init__.py'))['__version__']
(release / 'portable-version.txt').write_text(version + '\n', encoding='utf-8')
shutil.make_archive(str(release.parent / 'NupFileViewer-portable-win-x64'), 'zip', root_dir=release)
print(f'Portable release ready: {release}')
