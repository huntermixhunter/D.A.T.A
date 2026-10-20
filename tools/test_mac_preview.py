import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from unpack_mac_preview import unpack


class PreviewExtractionTests(unittest.TestCase):
    def archive(self, folder, link_name="python/bin/python3", link_target="python3.12"):
        archive = Path(folder) / "runtime.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            link = tarfile.TarInfo(link_name)
            link.type = tarfile.SYMTYPE
            link.linkname = link_target
            tar.addfile(link)
            data = b"Mac runtime fixture\n"
            file = tarfile.TarInfo("python/bin/python3.12")
            file.size = len(data)
            tar.addfile(file, io.BytesIO(data))
        return archive

    def test_link_before_target_is_materialized(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = self.archive(folder)
            out = Path(folder) / "path with spaces"
            unpack(archive, out)
            link = out / "python/bin/python3"
            self.assertFalse(link.is_symlink())
            self.assertEqual(link.read_bytes(), (out / "python/bin/python3.12").read_bytes())
            with self.assertRaisesRegex(ValueError, "already contains"):
                unpack(archive, out)

    def test_path_traversal_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = self.archive(folder, link_name="../outside")
            out = Path(folder) / "out"
            with self.assertRaisesRegex(ValueError, "Unsafe archive path"):
                unpack(archive, out)
            self.assertEqual(list(out.iterdir()), [])

    def test_link_outside_runtime_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = self.archive(folder, link_target="../../outside")
            out = Path(folder) / "out"
            with self.assertRaisesRegex(ValueError, "escapes runtime"):
                unpack(archive, out)
            self.assertEqual(list(out.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
