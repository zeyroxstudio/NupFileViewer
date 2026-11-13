"""Bounded, read-only NUP v1 reader. No game keys or executable loading."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import re
import struct
import tempfile
from dataclasses import dataclass, field
from threading import Event

import zstandard as zstd

from .i18n import language, tr


class NupError(ValueError):
    pass


class Cancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled(tr('Canceled'))


def require(condition, message):
    if not condition:
        raise NupError(message)


def bounded(offset, size, total, label):
    require(0 <= offset <= total and 0 <= size <= total - offset,
            tr('Invalid range: {0}', label))


def human_size(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.2f} {unit}"
        n /= 1024


@dataclass(slots=True)
class Entry:
    id: int
    name: str
    size: int
    offset: int
    stored: int
    sha256: bytes
    codec: int
    parent: int = -1
    package: int = -1
    path: str = ""

    @property
    def compression(self):
        return "Zstandard" if self.codec else "Raw"


@dataclass(slots=True)
class Directory:
    name: str
    children: list[int]
    parent: int = -1
    package: int = -1
    path: str = ""


@dataclass(slots=True)
class Package:
    title_id: int
    root: int
    exefs: int
    romfs: int
    version: int
    kind: int
    flags: int
    meta_offset: int
    meta_size: int
    metadata: dict[int, bytes] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)
    icons: dict[str, bytes] = field(default_factory=dict)

    @property
    def title(self):
        return self.titles.get(language()) or self.text(5) or f"{self.title_id:016X}"

    def text(self, key):
        return self.metadata.get(key, b"").decode("utf-8", errors="replace").rstrip("\0")

    @property
    def display_version(self):
        return self.text(4)

    @property
    def kind_name(self):
        return (tr('Base game'), tr('Update'), "DLC", tr('DLC update'), tr('System'))[self.kind]


class LimitedReader(io.RawIOBase):
    def __init__(self, stream, remaining):
        self.stream, self.remaining = stream, remaining

    def readable(self):
        return True

    def read(self, size=-1):
        size = self.remaining if size < 0 else min(size, self.remaining)
        data = self.stream.read(size)
        self.remaining -= len(data)
        return data

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)


class Archive:
    MAX_TOC = 512 * 1024 * 1024
    MAX_META = 64 * 1024 * 1024

    def __init__(self, path, cancel=None):
        self.path = Path(path).resolve()
        self.packages = []
        self.dirs = []
        self.files = []
        self.warnings = []
        with self.path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            self.identity = (stat.st_size, stat.st_mtime_ns)
            self.size = stat.st_size
            header = stream.read(128)
            require(len(header) == 128 and header[:4] == b"NUP\x01", tr('This file is not a NUP v1 archive'))
            header_size, self.header_version = struct.unpack_from("<HH", header, 4)
            toc_offset, toc_size = struct.unpack_from("<QQ", header, 8)
            self.blob_offset, file_size, package_count, flags = struct.unpack_from("<QQII", header, 56)
            require(header_size >= 128 and toc_offset == header_size, tr('Invalid NUP header'))
            require(self.header_version >= 1 and flags == 0 and not any(header[96:]), tr('Unsupported header or flags'))
            require(file_size == self.size, tr('Archive size mismatch: the file was truncated or modified'))
            bounded(toc_offset, toc_size, self.size, "TOC")
            require(toc_size <= self.MAX_TOC, tr('Index exceeds the 512 MiB limit'))
            require(toc_offset + toc_size <= self.blob_offset <= self.size and self.blob_offset % 16 == 0,
                    tr('TOC and blob regions overlap or are misaligned'))
            stream.seek(toc_offset)
            toc = stream.read(toc_size)
            check_cancel(cancel)
            self.toc_hash = hashlib.sha256(toc).digest()
            require(self.toc_hash == header[24:56], tr('TOC SHA-256 mismatch'))
            self.uuid = header[80:96].hex()
            require(len(toc) >= 48 and toc[:4] == b"TOC\x01", tr('Invalid TOC'))
            pc, dc, fc, pool_off, pool_size, reserved = struct.unpack_from("<IQQQQQ", toc, 4)
            require(pc == package_count and pc >= 1 and reserved == 0, tr('Invalid TOC counts'))
            dirs_off = 48 + pc * 64
            files_off = dirs_off + dc * 24
            children_off = files_off + fc * 80
            require(children_off <= pool_off and pool_off + pool_size <= len(toc), tr('TOC tables out of bounds'))
            require(not any(toc[pool_off + pool_size:]), tr('Unexpected data after the string pool'))
            require((pool_off - children_off) % 4 == 0, tr('Misaligned child table'))
            slots = (pool_off - children_off) // 4

            def name_at(offset, length):
                bounded(offset, length, pool_size, tr('name'))
                try:
                    name = toc[pool_off + offset:pool_off + offset + length].decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise NupError(tr('Name is not UTF-8')) from exc
                require(not any(c in name for c in ("/", "\\", "\0")) and name not in (".", ".."),
                        tr('Unsafe node name'))
                return name

            for i in range(pc):
                off = 48 + i * 64
                values = struct.unpack_from("<QIIII BB", toc, off)
                mo, ms = struct.unpack_from("<QI", toc, off + 32)
                pkg = Package(*values, mo, ms)
                require(pkg.kind < 5 and pkg.flags & ~1 == 0, tr('Unsupported package type or flags'))
                require(not any(toc[off+26:off+32]) and not any(toc[off+44:off+64]), tr('Nonzero reserved package fields'))
                require(pkg.root < dc and all(x == 0xFFFFFFFF or x < dc for x in (pkg.exefs, pkg.romfs)), tr('Invalid package root'))
                self.packages.append(pkg)
            base_count = sum(p.kind == 0 for p in self.packages)
            require(base_count <= 1, tr('Multiple base packages in one NUP'))
            require(all(p.kind != 1 or bool(p.flags & 1) == (base_count == 0) for p in self.packages), tr('Inconsistent standalone update flag'))
            uuid = hashlib.sha256(b"".join(struct.pack("<QIB", p.title_id, p.version, p.kind) for p in self.packages)).digest()[:16]
            require(uuid == header[80:96], tr('UUID does not match the packages'))
            slot_ranges = []
            for i in range(dc):
                no, nl, first, count, reserved = struct.unpack_from("<IIIIQ", toc, dirs_off + i * 24)
                bounded(first, count, slots, tr('folder children'))
                require(reserved == 0, tr('Nonzero reserved folder field'))
                children = list(struct.unpack_from(f"<{count}I", toc, children_off + first * 4))
                require(all(c < dc + fc for c in children), tr('Reference to a missing node'))
                self.dirs.append(Directory(name_at(no, nl), children))
                if count:
                    slot_ranges.append((first, first + count))
            cursor = 0
            for start, end in sorted(slot_ranges):
                require(start == cursor, tr('Overlapping or missing child ranges'))
                cursor = end
            require(cursor == slots, tr('Unused child slots'))
            regions = {}
            for i in range(fc):
                if i % 8192 == 0:
                    check_cancel(cancel)
                off = files_off + i * 80
                no, nl, size, extent, stored = struct.unpack_from("<IIQQQ", toc, off)
                sha = toc[off+32:off+64]
                codec = toc[off+64]
                require(codec in (0, 1) and not any(toc[off+65:off+80]), tr('Codec or file flags not supported'))
                if size == 0:
                    require(stored == extent == codec == 0, tr('Invalid empty file'))
                else:
                    require(stored > 0 and extent >= self.blob_offset and extent % 16 == 0, tr('Invalid extent'))
                    bounded(extent, stored, self.size, tr('file contents'))
                    key = (extent, stored)
                    identity = (size, sha, codec)
                    require(key not in regions or regions[key] == identity, tr('Shared extent with different contents'))
                    regions[key] = identity
                require(codec != 0 or stored == size, tr('Raw size mismatch'))
                self.files.append(Entry(i, name_at(no, nl), size, extent, stored, sha, codec))
            self.unique_extents = len(regions)
            intervals = [(offset, offset + size) for offset, size in regions]
            require(sum(p.meta_size for p in self.packages) <= 256 * 1024 * 1024,
                    tr('Total metadata exceeds 256 MiB'))
            for pkg in self.packages:
                if not pkg.meta_size:
                    require(pkg.meta_offset == 0, tr('Metadata offset without size'))
                    continue
                require(pkg.meta_size <= self.MAX_META and pkg.meta_offset >= self.blob_offset, tr('Invalid metadata block'))
                bounded(pkg.meta_offset, pkg.meta_size, self.size, tr('metadata'))
                intervals.append((pkg.meta_offset, pkg.meta_offset + pkg.meta_size))
                stream.seek(pkg.meta_offset)
                self._metadata(pkg, stream.read(pkg.meta_size))
            previous = self.blob_offset
            for start, end in sorted(intervals):
                require(start >= previous, tr('Overlapping content regions'))
                previous = end
            self._index_trees(cancel)
            self.logical_size = sum(f.size for f in self.files)
            self.stored_size = sum(size for _, size in regions)
            self.toc_size = toc_size

    @staticmethod
    def _metadata(pkg, data):
        off = 0
        while off + 8 <= len(data):
            kind, reserved, size = struct.unpack_from("<HHI", data, off)
            off += 8
            bounded(off, size, len(data), "TLV")
            require(reserved == 0 and kind not in pkg.metadata, tr('Duplicate or invalid TLV'))
            value = data[off:off+size]
            pkg.metadata[kind] = value
            off += size
            if kind in (9, 10):
                pos, seen = 0, set()
                while pos < len(value):
                    lang_len = value[pos]
                    pos += 1
                    require(lang_len > 0, tr('Missing TLV language'))
                    bounded(pos, lang_len + 4, len(value), tr('TLV language'))
                    lang = value[pos:pos+lang_len].decode("utf-8").lower()
                    pos += lang_len
                    length = struct.unpack_from("<I", value, pos)[0]
                    pos += 4
                    bounded(pos, length, len(value), tr('localized content'))
                    require(lang not in seen, tr('Duplicate language'))
                    seen.add(lang)
                    if kind == 9:
                        pkg.titles[lang] = value[pos:pos+length].decode("utf-8").rstrip("\0")
                    else:
                        pkg.icons[lang] = value[pos:pos+length]
                    pos += length

    def _index_trees(self, cancel):
        dc = len(self.dirs)
        seen = set()
        for pi, pkg in enumerate(self.packages):
            stack = [(pkg.root, -1, "")]
            while stack:
                check_cancel(cancel)
                node, parent, prefix = stack.pop()
                require(node not in seen, tr('Cycle or node with multiple parents in the tree'))
                seen.add(node)
                obj = self.dirs[node] if node < dc else self.files[node-dc]
                require(parent == -1 or bool(obj.name), tr('Empty name outside the root'))
                obj.parent, obj.package = parent, pi
                obj.path = prefix + obj.name if parent != -1 else ""
                require(len(obj.path) <= 32760, tr('Path is too long'))
                if node < dc:
                    names = [self.dirs[c].name if c < dc else self.files[c-dc].name for c in obj.children]
                    require(len(set(names)) == len(names), tr('Duplicate names in one folder'))
                    for c in reversed(obj.children):
                        stack.append((c, node, obj.path + "/" if obj.path else ""))
            for special in (pkg.exefs, pkg.romfs):
                require(special == 0xFFFFFFFF or self.dirs[special].package == pi, tr('ExeFS/RomFS is outside the package'))
        require(len(seen) == len(self.dirs) + len(self.files), tr('Unreachable nodes in the archive'))

    def _open(self):
        stream = self.path.open("rb")
        stat = os.fstat(stream.fileno())
        if (stat.st_size, stat.st_mtime_ns) != self.identity:
            stream.close()
            raise NupError(tr('The file changed after opening. Reopen it before continuing.'))
        return stream

    def chunks(self, entry, cancel=None, limit=None):
        with self._open() as stream:
            if entry.codec:
                self._validate_zstd(stream, entry, cancel)
            stream.seek(entry.offset)
            raw = LimitedReader(stream, entry.stored)
            reader = raw
            if entry.codec:
                reader = zstd.ZstdDecompressor(max_window_size=256 * 1024 * 1024).stream_reader(raw, closefd=False)
            count = 0
            try:
                while True:
                    check_cancel(cancel)
                    if limit is not None and count >= limit:
                        return
                    request = min(1024 * 1024, entry.size - count + 1)
                    if limit is not None:
                        request = min(request, limit - count)
                    data = reader.read(request)
                    if not data:
                        break
                    count += len(data)
                    require(count <= entry.size, tr('Decompressed content exceeds the declared size'))
                    yield data
                require(count == entry.size, tr('Truncated content or incorrect size'))
            finally:
                if reader is not raw:
                    reader.close()

    @staticmethod
    def _validate_zstd(stream, entry, cancel):
        # Check one complete standard frame, including the final block/checksum.
        # stream_reader alone can accept an incomplete frame after emitting data.
        stream.seek(entry.offset)
        header = stream.read(min(18, entry.stored))
        require(header[:4] == b"\x28\xb5\x2f\xfd", tr('Nonstandard Zstandard frame'))
        params = zstd.get_frame_parameters(header)
        require(params.content_size == entry.size, tr('Zstandard frame size mismatch'))
        require(params.window_size <= 256 * 1024 * 1024 and params.dict_id == 0,
                tr('Zstandard requires a dictionary or a window larger than 256 MiB'))
        pos = zstd.frame_header_size(header)
        while True:
            check_cancel(cancel)
            bounded(pos, 3, entry.stored, tr('Zstandard block header'))
            stream.seek(entry.offset + pos)
            block = int.from_bytes(stream.read(3), "little")
            last, kind, size = block & 1, (block >> 1) & 3, block >> 3
            require(kind != 3 and size <= 128 * 1024, tr('Invalid Zstandard block'))
            length = 1 if kind == 1 else size
            pos += 3
            bounded(pos, length, entry.stored, tr('Zstandard block'))
            pos += length
            if last:
                break
        pos += 4 if params.has_checksum else 0
        require(pos == entry.stored, tr('Truncated Zstandard frame or trailing data'))

    def preview(self, entry, limit=65536):
        return b"".join(self.chunks(entry, limit=limit))

    def verify(self, entries=None, cancel=None, progress=None):
        entries = self.files if entries is None else entries
        seen = set()
        total = len(entries)
        for i, entry in enumerate(entries):
            check_cancel(cancel)
            key = (entry.offset, entry.stored, entry.sha256)
            if key not in seen:
                digest = hashlib.sha256()
                for chunk in self.chunks(entry, cancel):
                    digest.update(chunk)
                require(digest.digest() == entry.sha256, tr('SHA-256 mismatch: {0}', entry.path))
                seen.add(key)
            if progress:
                progress(i+1, total, entry.path)
        return tr('SHA-256 verified: {0:,} files, {1:,} unique contents.', total, len(seen))

    def export(self, entries, destination, cancel=None, progress=None):
        root = Path(destination).resolve(strict=True)
        require(root.is_dir(), tr('The destination must be a folder'))
        planned, targets = [], set()
        for entry in entries:
            check_cancel(cancel)
            pkg = self.packages[entry.package]
            parts = [f"{entry.package+1:02}_{pkg.title_id:016X}_v{pkg.version}"] + entry.path.split("/")
            require(all(safe_component(p) for p in parts), tr('Name cannot be extracted on Windows: {0}', entry.path))
            target = root.joinpath(*parts)
            key = str(target).casefold()
            require(key not in targets, tr('Windows name collision: {0}', entry.path))
            targets.add(key)
            self._safe_target(root, target)
            require(not target.exists(), tr('File already exists: {0}', target))
            planned.append((entry, target))
        for i, (entry, target) in enumerate(planned):
            check_cancel(cancel)
            self._safe_target(root, target)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._safe_target(root, target)
            temp = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".nup-", suffix=".part", delete=False) as out:
                    temp = Path(out.name)
                    digest = hashlib.sha256()
                    for chunk in self.chunks(entry, cancel):
                        out.write(chunk)
                        digest.update(chunk)
                require(digest.digest() == entry.sha256, tr('SHA-256 mismatch: {0}', entry.path))
                check_cancel(cancel)
                self._safe_target(root, target)
                # Windows rename refuses replacement and works on NTFS/exFAT.
                if os.name == "nt":
                    os.rename(temp, target)
                else:
                    os.link(temp, target)
            finally:
                if temp is not None:
                    temp.unlink(missing_ok=True)
            if progress:
                progress(i+1, len(planned), entry.path)
        return tr('Extracted and verified {0:,} files in {1}', len(planned), root)

    @staticmethod
    def _safe_target(root, target):
        require(target.resolve().is_relative_to(root), tr('Path is outside the destination'))
        current = target
        while current != root:
            require(not current.is_symlink() and not current.is_junction(), tr('Link or junction in the destination'))
            current = current.parent

    def report(self):
        return {
            "format": "NUP v1", "file": str(self.path), "size": self.size,
            "toc_sha256": self.toc_hash.hex(), "toc_verified": True,
            "content_verified": False, "uuid": self.uuid,
            "directories": len(self.dirs), "files": len(self.files),
            "logical_size": self.logical_size, "unique_stored_size": self.stored_size,
            "packages": [{"title_id": f"{p.title_id:016X}", "title": p.title,
                          "kind": p.kind_name, "version_raw": p.version,
                          "display_version": p.display_version, "publisher": p.text(6),
                          "build_id": p.metadata.get(7, b"").hex(),
                          "dump_name": p.text(8), "has_nacp": 2 in p.metadata,
                          "standalone_update": p.kind == 1 and bool(p.flags & 1),
                          "localized_titles": p.titles, "localized_icons": sorted(p.icons)}
                         for p in self.packages],
        }


def safe_component(name):
    return (bool(name) and name not in (".", "..") and not name.endswith((".", " "))
            and not re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            and not re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", name)
            and len(name.encode("utf-16-le")) <= 510)
