"""describe_image separates headered/headerless images and SHA-256 from CRC32 (Issue #476). Synthetic bytes only."""

import hashlib

import sys

import tempfile

from unittest import mock

import unittest

from pathlib import Path



sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from reproduce_static_triage import describe_image, main  # noqa: E402





class DescribeImage(unittest.TestCase):

    def test_headerless_and_headered_share_payload_facts(self):

        body = bytes(range(256)) * 1024

        a, b = describe_image(body), describe_image(b"\0" * 512 + body)

        self.assertEqual((a["header_bytes"], b["header_bytes"]), (0, 512))

        self.assertEqual(b["header_kind"], "headered")

        self.assertEqual(a["sha256_payload"], b["sha256_payload"])

        self.assertEqual(a["crc32_payload"], b["crc32_payload"])

        self.assertNotEqual(a["sha256_whole_file"], b["sha256_whole_file"])



    def test_odd_size_unsupported(self):

        self.assertEqual(describe_image(b"x" * 100)["header_kind"], "UNSUPPORTED_SIZE")



    def test_exact_size_gate(self):

        for n in (0, 8192, 524288):

            self.assertEqual(describe_image(b"x" * n)["header_kind"], "UNSUPPORTED_SIZE")



    def test_cli_exit_codes(self):

        with tempfile.TemporaryDirectory() as t:

            bad = Path(t) / "bad.bin"

            bad.write_bytes(b"x" * 8192)

            self.assertEqual(main(["--rom", str(bad)]), 2)

            ok = Path(t) / "ok.bin"

            ok.write_bytes(bytes(262144))

            hd = Path(t) / "hd.bin"
            hd.write_bytes(bytes([170]) * 512 + bytes(262144))
            with mock.patch("reproduce_static_triage.subprocess.run") as run:

                self.assertEqual(main(["--rom", str(ok), "--expect-sha256", "0" * 64]), 1)

                run.assert_not_called()

                payload = hashlib.sha256(bytes(262144)).hexdigest()
                self.assertEqual(main(["--rom", str(hd), "--expect-sha256", payload]), 1)  # whole-file, not payload
                run.assert_not_called()
                self.assertEqual(main(["--rom", str(bad), "--expect-sha256", "0" * 64]), 2)
                good = hashlib.sha256(ok.read_bytes()).hexdigest()

                run.return_value = mock.Mock(returncode=7, stderr="")

                for v in (good, good.upper(), " " + good + chr(10)):

                    run.reset_mock()

                    main(["--rom", str(ok), "--expect-sha256", v])

                    run.assert_called_once()  # hash gate passed; auditor reached

            self.assertEqual(main(["--rom", str(Path(t) / "none")]), 2)





if __name__ == "__main__":

    unittest.main()

