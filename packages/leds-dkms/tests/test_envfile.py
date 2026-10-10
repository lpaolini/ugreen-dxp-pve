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


if __name__ == "__main__":
    unittest.main()
