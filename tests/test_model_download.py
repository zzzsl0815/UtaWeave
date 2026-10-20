import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

spec=importlib.util.spec_from_file_location('download_models',Path(__file__).resolve().parents[1]/'scripts/download_models.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ModelArchiveTests(unittest.TestCase):
    def test_zip_parent_path_cannot_escape_model_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);out=root/'out';out.mkdir();archive=root/'bad.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('../escaped','bad')
            with self.assertRaises(ValueError):module.safe_extract(archive,out)
            self.assertFalse((root/'escaped').exists())

    def test_zip_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);out=root/'out';out.mkdir();archive=root/'bad.zip'
            info=zipfile.ZipInfo('link');info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16
            with zipfile.ZipFile(archive,'w') as z:z.writestr(info,'../../outside')
            with self.assertRaises(ValueError):module.safe_extract(archive,out)


if __name__=='__main__':unittest.main()
