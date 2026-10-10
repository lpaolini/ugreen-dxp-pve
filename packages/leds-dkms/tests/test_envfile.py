import os
import tempfile
import unittest

from ugreen_leds.envfile import read_env


class ReadEnvTest(unittest.TestCase):
    def test_pairs_quotes_and_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.conf")
            with open(path, "w") as f:
                f.write('# comment\n\nVMID=105\n  DEBUG = 1 \nCOLOR="0 64 16"\n'
                        "BLINK='blink 500 500'\nREGEX=a=b\nnot a pair\nEMPTY=\n")
            self.assertEqual(read_env(path), {
                "VMID": "105", "DEBUG": "1", "COLOR": "0 64 16",
                "BLINK": "blink 500 500", "REGEX": "a=b", "EMPTY": ""})

    def test_unquoted_inline_comment_is_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.conf")
            with open(path, "w") as f:
                f.write('A=vmbr1 # uplink\nB=a#b\nC="x # y"\nD="x" # z\nE=1\t#t\n')
            self.assertEqual(read_env(path), {
                "A": "vmbr1", "B": "a#b", "C": "x # y", "D": "x", "E": "1"})

    def test_stray_non_utf8_byte_is_tolerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.conf")
            with open(path, "wb") as f:
                f.write(b"# caf\xe9\nVMID=105\n")
            self.assertEqual(read_env(path), {"VMID": "105"})


if __name__ == "__main__":
    unittest.main()
