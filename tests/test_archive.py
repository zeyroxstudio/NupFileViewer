import hashlib
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest

import zstandard as zstd

from nupviewer.archive import Archive, NupError, Cancelled, safe_component


def fixture(names=("hello.txt", "packed.bin", "empty"), payloads=None):
    payloads = payloads or [b"hello world", b"abcdefgh" * 300000, b""]
    encoded = [payloads[0], zstd.ZstdCompressor(write_checksum=True).compress(payloads[1]), b""]
    pool = bytearray()
    def name(value):
        data = value.encode()
        off = len(pool)
        pool.extend(data)
        return off, len(data)
    root_name, romfs_name = name(""), name("romfs")
    file_names = [name(n) for n in names]
    pool_off = 48 + 64 + 2 * 24 + 3 * 80 + 4 * 4
    toc = bytearray(pool_off + len(pool))
    struct.pack_into("<4sIQQQQQ", toc, 0, b"TOC\x01", 1, 2, 3, pool_off, len(pool), 0)
    struct.pack_into("<QIIIIBB", toc, 48, 0x0400123456789000, 0, 0xFFFFFFFF, 1, 65536, 0, 0)
    struct.pack_into("<IIIIQ", toc, 112, *root_name, 0, 1, 0)
    struct.pack_into("<IIIIQ", toc, 136, *romfs_name, 1, 3, 0)
    struct.pack_into("<4I", toc, 400, 1, 2, 3, 4)
    toc[pool_off:] = pool
    blob_offset = (128 + len(toc) + 15) & ~15
    blob = bytearray()
    for i, (content, packed) in enumerate(zip(payloads, encoded)):
        while len(blob) % 16:
            blob.append(0)
        offset = blob_offset + len(blob) if content else 0
        struct.pack_into("<IIQQQ32sB", toc, 160 + i * 80, *file_names[i], len(content), offset,
                         len(packed), hashlib.sha256(content).digest(), int(i == 1))
        blob.extend(packed)
    header = bytearray(128)
    struct.pack_into("<4sHHQQ", header, 0, b"NUP\x01", 128, 1, 128, len(toc))
    header[24:56] = hashlib.sha256(toc).digest()
    struct.pack_into("<QQII", header, 56, blob_offset, blob_offset + len(blob), 1, 0)
    header[80:96] = hashlib.sha256(struct.pack("<QIB", 0x0400123456789000, 65536, 0)).digest()[:16]
    return header + toc + bytes(blob_offset - 128 - len(toc)) + blob


def rehash(data):
    size = struct.unpack_from("<Q", data, 16)[0]
    data[24:56] = hashlib.sha256(data[128:128+size]).digest()
    return data


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "test.nup"

    def tearDown(self):
        self.temp.cleanup()

    def load(self, data=None):
        self.path.write_bytes(fixture() if data is None else data)
        return Archive(self.path)

    def test_raw_zstd_empty_preview_and_hashes(self):
        archive = self.load()
        self.assertEqual(archive.preview(archive.files[0]), b"hello world")
        self.assertEqual(len(archive.preview(archive.files[1])), 65536)
        self.assertEqual(archive.preview(archive.files[2]), b"")
        self.assertIn("3 file", archive.verify())

    def test_ncm_does_not_supply_display_version(self):
        data = fixture()
        struct.pack_into("<IBB", data, 128 + 48 + 20, 917504, 1, 1)
        data[80:96] = hashlib.sha256(struct.pack("<QIB", 0x0400123456789000, 917504, 1)).digest()[:16]
        package, = self.load(rehash(data)).report()["packages"]
        self.assertEqual(package["version_raw"], 917504)
        self.assertEqual(package["display_version"], "")
        self.assertTrue(package["standalone_update"])

    def test_localized_icons_and_package_metadata_in_report(self):
        def tlv(kind, value):
            return struct.pack("<HHI", kind, 0, len(value)) + value

        def localized(tag, value):
            return bytes([len(tag)]) + tag + struct.pack("<I", len(value)) + value

        data = fixture()
        data.extend(bytes((-len(data)) % 16))
        offset = len(data)
        icon = b"image bytes"
        metadata = b"".join((
            tlv(2, bytes(0x4000)), tlv(4, b"1.8.0"), tlv(8, b"Example dump"),
            tlv(9, localized(b"it", b"Titolo")),
            tlv(10, localized(b"FR", icon)),
        ))
        data.extend(metadata)
        struct.pack_into("<QI", data, 128 + 48 + 32, offset, len(metadata))
        struct.pack_into("<Q", data, 64, len(data))
        archive = self.load(rehash(data))
        self.assertEqual(archive.packages[0].icons, {"fr": icon})
        package, = archive.report()["packages"]
        self.assertEqual(package["display_version"], "1.8.0")
        self.assertEqual(package["dump_name"], "Example dump")
        self.assertTrue(package["has_nacp"])
        self.assertEqual(package["localized_titles"], {"it": "Titolo"})
        self.assertEqual(package["localized_icons"], ["fr"])

    def test_export_roundtrip_and_refuse_overwrite(self):
        a = self.load()
        a.export(a.files, self.root)
        outputs = list(self.root.glob("01_*/romfs/*"))
        self.assertEqual(len(outputs), 3)
        self.assertEqual(next(p for p in outputs if p.name == "packed.bin").read_bytes(), b"abcdefgh" * 300000)
        with self.assertRaisesRegex(NupError, "already exists"):
            a.export(a.files, self.root)
        self.assertFalse(list(self.root.rglob("*.part")))

    def test_bad_toc_hash(self):
        data = fixture()
        data[200] ^= 1
        with self.assertRaisesRegex(NupError, "SHA-256"):
            self.load(data)

    def test_truncated_archive(self):
        with self.assertRaisesRegex(NupError, "Archive size mismatch"):
            self.load(fixture()[:-1])

    def test_bad_extent(self):
        data = fixture()
        struct.pack_into("<Q", data, 128+160+16, len(data) + 1000)
        with self.assertRaises(NupError):
            self.load(rehash(data))

    def test_cycle(self):
        data = fixture()
        struct.pack_into("<I", data, 128+400, 0)
        with self.assertRaisesRegex(NupError, "Cycle"):
            self.load(rehash(data))

    def test_unknown_codec(self):
        data = fixture()
        data[128+160+64] = 7
        with self.assertRaisesRegex(NupError, "Codec"):
            self.load(rehash(data))

    def test_unknown_flags(self):
        data = fixture()
        data[76] = 1
        with self.assertRaises(NupError):
            self.load(data)

    def test_corrupt_payload_leaves_no_partial_file(self):
        data = fixture()
        offset = struct.unpack_from("<Q", data, 128+160+16)[0]
        data[offset] ^= 1
        a = self.load(data)
        with self.assertRaisesRegex(NupError, "SHA-256"):
            a.export([a.files[0]], self.root)
        self.assertFalse(list(self.root.rglob("*.part")))
        self.assertFalse(list(self.root.rglob("hello.txt")))

    def test_windows_case_collision_preflight(self):
        a = self.load(fixture(names=("Test", "test", "empty")))
        with self.assertRaisesRegex(NupError, "name collision"):
            a.export(a.files, self.root)
        self.assertFalse(list(self.root.glob("01_*")))

    def test_path_traversal_rejected(self):
        with self.assertRaisesRegex(NupError, "node name"):
            self.load(fixture(names=("../outside", "packed.bin", "empty")))

    def test_reserved_windows_name(self):
        a = self.load(fixture(names=("CON.txt", "packed.bin", "empty")))
        with self.assertRaisesRegex(NupError, "cannot be extracted"):
            a.export(a.files, self.root)

    def test_cancel(self):
        a = self.load()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            a.verify(cancel=cancel)
        with self.assertRaises(Cancelled):
            a.export(a.files, self.root, cancel=cancel)

    def test_archive_changed(self):
        a = self.load()
        with self.path.open("ab") as stream:
            stream.write(b"x")
        with self.assertRaisesRegex(NupError, "file changed"):
            a.preview(a.files[0])

    def test_missing_zstd_checksum_rejected(self):
        data = fixture()
        offset = 128 + 160 + 80 + 24
        stored = struct.unpack_from("<Q", data, offset)[0]
        struct.pack_into("<Q", data, offset, stored - 1)
        a = self.load(rehash(data))
        with self.assertRaisesRegex(NupError, "Zstandard frame"):
            a.verify([a.files[1]])

    def test_names(self):
        for name in ("..", "CON", "com1.txt", "aux", "x.", "x ", "a:b", "a/b", "a\\b"):
            self.assertFalse(safe_component(name), name)
        self.assertTrue(safe_component("日本語.txt"))


if __name__ == "__main__":
    unittest.main()
