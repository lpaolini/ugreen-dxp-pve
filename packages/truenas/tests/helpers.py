import importlib.util
import os
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_script(name, relpath):
    """Import a hyphenated script such as src/zfs/ugreen-truenas-zfs.py as a module."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeDirectoryTestCase(unittest.TestCase):
    """Runs each test with RUNTIME_DIRECTORY pointing at a fresh temp directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        patcher = mock.patch.dict(os.environ, {"RUNTIME_DIRECTORY": self.dir})
        patcher.start()
        self.addCleanup(patcher.stop)

    def read(self, led):
        with open(os.path.join(self.dir, led)) as f:
            return f.read()
