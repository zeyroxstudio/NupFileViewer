"""Check and stage GitHub Release assets for the portable app."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZipFile

from . import __version__
from .i18n import tr
from .archive import check_cancel, safe_component


REPO = "zeyroxstudio/NupFileViewer"
ASSET = "NupFileViewer-portable-win-x64.zip"
MANAGED = (
    "NupFileViewer.exe", "_internal", "licenses", "source",
    "README.md", "LICENSE", "THIRD-PARTY-NOTICES.md", "portable-version.txt",
)
MAX_DOWNLOAD = 512 * 1024 * 1024


@dataclass(frozen=True)
class Release:
    tag: str
    digest: str
    size: int

    @property
    def url(self):
        return f"https://github.com/{REPO}/releases/download/{self.tag}/{ASSET}"


def version_tuple(tag):
    match = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", tag)
    return tuple(int(value or 0) for value in match.groups()) if match else None


def portable_root():
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
        if (root / "portable-version.txt").is_file():
            return root
    return None


def request(url):
    return Request(url, headers={"User-Agent": f"NupFileViewer/{__version__}"})


def check_release(cancel=None):
    try:
        with urlopen(request(f"https://api.github.com/repos/{REPO}/releases/latest"), timeout=8) as response:
            payload = json.loads(response.read(1024 * 1024))
    except HTTPError as error:
        if error.code == 404:
            return None
        raise
    check_cancel(cancel)
    tag = payload.get("tag_name", "")
    version = version_tuple(tag)
    if payload.get("draft") or payload.get("prerelease") or not version or version <= version_tuple(__version__):
        return None
    for asset in payload.get("assets", []):
        digest = asset.get("digest") or ""
        size = asset.get("size", 0)
        if (asset.get("name") == ASSET and re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                and isinstance(size, int) and 0 < size <= MAX_DOWNLOAD):
            return Release(tag, digest[7:], size)
    raise ValueError(tr('The release has no portable package with a SHA-256 digest.'))


def unpack_portable(zip_path, destination, tag, cancel=None):
    with ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) > 10000 or sum(item.file_size for item in members) > 1024**3:
            raise ValueError(tr('The package exceeds the extraction limits.'))
        seen = set()
        for item in members:
            check_cancel(cancel)
            parts = PurePosixPath(item.filename).parts
            key = tuple(part.casefold() for part in parts)
            if (not parts or parts[0] not in MANAGED or key in seen
                    or not all(safe_component(part) for part in parts)
                    or (len(parts) > 1 and parts[0] not in {"_internal", "licenses", "source"})):
                raise ValueError(tr('Invalid path in the package: {0}', item.filename))
            seen.add(key)
            target = destination.joinpath(*parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
    marker = destination / "portable-version.txt"
    if (not all((destination / name).exists() for name in MANAGED)
            or not (destination / "NupFileViewer.exe").is_file()
            or not (destination / "_internal").is_dir()
            or version_tuple(marker.read_text(encoding="utf-8").strip()) != version_tuple(tag)):
        raise ValueError(tr('Portable package is incomplete or has the wrong version.'))


def stage_release(release, root, cancel=None, progress=None):
    stage = Path(tempfile.mkdtemp(prefix=".NupFileViewer-update-", dir=root.parent))
    try:
        digest = hashlib.sha256()
        received = 0
        zip_path = stage / ASSET
        with urlopen(request(release.url), timeout=15) as response, zip_path.open("wb") as output:
            while chunk := response.read(256 * 1024):
                check_cancel(cancel)
                received += len(chunk)
                if received > release.size:
                    raise ValueError(tr('The download exceeds the declared size.'))
                output.write(chunk)
                digest.update(chunk)
                if progress:
                    progress(received, release.size, tr('Downloading update'))
        if received != release.size or digest.hexdigest() != release.digest:
            raise ValueError(tr('The update size or SHA-256 does not match.'))
        unpack_portable(zip_path, stage / "new", release.tag, cancel)
        zip_path.unlink()
        return stage
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def install_staged(stage, root, archive_path):
    script = stage / "apply_update.ps1"
    shutil.copy2(Path(__file__).with_name("apply_update.ps1"), script)
    (stage / "update.json").write_text(json.dumps({
        "pid": os.getpid(), "root": str(root),
        "archive": str(archive_path) if archive_path else "",
        "managed": MANAGED,
    }), encoding="utf-8")
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        cwd=root.parent, creationflags=subprocess.CREATE_NO_WINDOW,
        env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
    )
