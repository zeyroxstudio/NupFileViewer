import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from nupviewer import updates


def release_payload(tag, digest):
    return json.dumps({
        "tag_name": tag,
        "assets": [{"name": updates.ASSET, "size": 10, "digest": digest}],
    }).encode()


def portable_zip(tag, extra=None):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name in updates.MANAGED:
            if name in {"_internal", "licenses", "source"}:
                archive.writestr(f"{name}/test.txt", "test")
            else:
                archive.writestr(name, tag if name == "portable-version.txt" else "test")
        if extra:
            archive.writestr(extra, "test")
    return buffer.getvalue()


class UpdateTests(unittest.TestCase):
    @patch.object(updates, "__version__", "0.1")
    def test_only_new_stable_release_with_digest(self):
        digest = "sha256:" + "a" * 64
        for tag in ("v0.1.0", "v0.0.9", "v0.2-beta"):
            with self.subTest(tag=tag), patch.object(updates, "urlopen", return_value=io.BytesIO(release_payload(tag, digest))):
                self.assertIsNone(updates.check_release())
        with patch.object(updates, "urlopen", return_value=io.BytesIO(release_payload("v0.2", digest))):
            self.assertEqual(updates.check_release().digest, "a" * 64)
        with patch.object(updates, "urlopen", return_value=io.BytesIO(release_payload("v0.2", ""))):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                updates.check_release()

    def test_stage_only_after_digest_validation(self):
        contents = portable_zip("0.2")
        with TemporaryDirectory() as temp:
            root = Path(temp) / "installation"
            root.mkdir()
            with patch.object(updates, "urlopen", return_value=io.BytesIO(contents)):
                release = updates.Release("v0.2", hashlib.sha256(contents).hexdigest(), len(contents))
                stage = updates.stage_release(release, root)
            self.assertTrue((stage / "new" / "NupFileViewer.exe").is_file())
            with patch.object(updates, "urlopen", return_value=io.BytesIO(contents)):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    updates.stage_release(updates.Release("v0.2", "0" * 64, len(contents)), root)
            self.assertEqual(list(root.parent.glob(".NupFileViewer-update-*")), [stage])

    def test_invalid_paths_and_marker(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for index, (tag, extra) in enumerate([
                ("0.2", "_internal/../../escape"),
                ("0.2", "_internal/TEST.TXT"),
                ("0.2", "_internal/file:stream"),
                ("0.3", None),
            ]):
                with self.subTest(extra=extra, tag=tag):
                    bundle = root / "test.zip"
                    bundle.write_bytes(portable_zip(tag, extra))
                    with self.assertRaises(ValueError):
                        updates.unpack_portable(bundle, root / str(index), "v0.2")


if __name__ == "__main__":
    unittest.main()
