"""Host-only CUE selection checks for the synthetic real-core track diagnostic."""
import importlib.util
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from tools import cue_analysis
from tools import synthetic_boot_media as media
from tools.make_cd_image import CUE_NAME as READ_CUE_NAME, build_image
from tools.synthetic_track_select import NoDataTrackError, select_first_mode1_track

INTEGRATION = Path(__file__).resolve().parents[1] / "integration" / "geargrafx_synthetic_track_select.py"
SPEC = importlib.util.spec_from_file_location("geargrafx_synthetic_track_select", INTEGRATION)
track_integration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(track_integration)


class SyntheticTrackSelectionTests(unittest.TestCase):
    def write(self, mutation, layout):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        media.write_case(root, mutation, layout)
        return root / media.CUE_NAME

    def test_single_data_track_selects_index01(self):
        # A one-track CUE is separately generated here; the selection helper is
        # intentionally driven by CUE analysis rather than a guessed BIOS command.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_image(root)
            cue = root / READ_CUE_NAME
            selected = select_first_mode1_track(cue)
            self.assertEqual((selected["track"], selected["lba"]), (1, 0))

    def test_multitrack_selects_data_after_audio(self):
        cue = self.write("NONE", "V1")
        selected = select_first_mode1_track(cue)
        self.assertEqual((selected["track"], selected["lba"]), (2, media.AUDIO_FRAMES))

    def test_stored_pregap_selects_index01_not_index00(self):
        cue = self.write("NONE", "V2")
        selected = select_first_mode1_track(cue)
        track = selected["report"]["tracks"][1]
        self.assertEqual(track["pregap_frames"], media.V2_PREGAP_FRAMES)
        self.assertEqual(selected["lba"], track["start_lba"])
        self.assertNotEqual(selected["lba"], track["pregap_start_lba"])
        with self.assertRaisesRegex(cue_analysis.CueError, "pregap"):
            cue_analysis.user_sector(cue, selected["report"], track["pregap_start_lba"])

    def test_audio_only_is_rejected(self):
        cue = self.write("NO_TRACK", "V1")
        with self.assertRaises(NoDataTrackError):
            select_first_mode1_track(cue)


class SyntheticTrackSelectionClassificationTests(unittest.TestCase):
    @staticmethod
    def complete_records():
        base = {"name": "checkpoint", "cycle": "cold", "step": 1, "pc": 0, "a": 0, "x": 0, "y": 0,
                "p": 0, "sp": 0, "mpr": [0] * 8, "scsi_phase": 0, "scsi_signals": 0, "load_sector": 0,
                "load_count": 0, "cd_irq_active": 0, "cd_irq_enabled": 0}
        run = {**base, "reason": "cd_halt_ok", "halt_confirmed": True, "halted_after_step": 0,
               "halt_confirmation_steps": 0, "halt_confirmation_required": 0, "result_hex": "00" * 32,
               "buffer_hex": "00" * track_integration.BUFFER_BYTES}
        cap = {"instruction_step": True, "bios_loaded": True, "loader": "test", "cdrom_hardware": True,
               "cdrom_media": True, "disc_given": True, "drive_ready": True}
        return {"CAP": [cap], "HIT": [], "PHASE": [base, {**base, "cycle": "warm"}], "RUN": [run, {**run, "cycle": "warm"}],
                "BLOCK": [], "INJECT": []}

    @staticmethod
    def complete_manifest():
        return {"symbols": {name: (0xF000 if name == "cd_entry" else 0xF100)
                            for name in track_integration.REQUIRED_SYMBOLS},
                "code_sha256_excluding_request": "a" * 64}

    def test_missing_artifact_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(track_integration.BlockedError):
                track_integration.require_file(Path(tmp) / "missing.pce")
        self.assertEqual(track_integration.exit_for_error(track_integration.BlockedError("BLOCKED: missing")), 3)

    def test_partial_core_observation_is_blocked(self):
        partial = {"CAP": [{}], "RUN": [{"cycle": "cold"}], "PHASE": []}
        with self.assertRaisesRegex(track_integration.BlockedError, "partial probe record"):
            track_integration.require_complete_records(partial, "test")
        with mock.patch.object(track_integration, "execute", side_effect=ValueError("probe did not complete cold and warm runs")):
            with self.assertRaisesRegex(track_integration.BlockedError, "partial or unreadable"):
                track_integration.execute_complete("probe", "rom", "cue", "labels")

    def test_malformed_transcript_container_rows_keys_and_cycles_are_blocked(self):
        cases = []
        records = self.complete_records(); records["PHASE"] = ()
        cases.append(records)
        records = self.complete_records(); records["RUN"][0] = []
        cases.append(records)
        records = self.complete_records(); del records["RUN"][0]["buffer_hex"]
        cases.append(records)
        records = self.complete_records(); records["RUN"][1]["cycle"] = "cold"
        cases.append(records)
        for records in cases:
            with self.subTest(records=records):
                with self.assertRaisesRegex(track_integration.BlockedError, "BLOCKED:"):
                    track_integration.require_complete_records(records, "test")

    def test_manifest_schema_rejects_missing_none_string_and_bool_symbols(self):
        for mutation in ("missing", None, "f100", True):
            manifest = self.complete_manifest()
            if mutation == "missing":
                del manifest["symbols"]["cd_cdb"]
            else:
                manifest["symbols"]["cd_cdb"] = mutation
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(track_integration.BlockedError, "BLOCKED:"):
                    track_integration.require_manifest(manifest)

    def test_manifest_schema_rejects_bad_digest(self):
        manifest = self.complete_manifest()
        manifest["code_sha256_excluding_request"] = "not-a-digest"
        with self.assertRaises(track_integration.BlockedError):
            track_integration.require_manifest(manifest)

    def test_transcript_value_and_hex_errors_are_blocked(self):
        mutations = {
            "buffer_none": lambda r: r["RUN"][0].__setitem__("buffer_hex", None),
            "buffer_non_hex": lambda r: r["RUN"][0].__setitem__("buffer_hex", "GG"),
            "buffer_odd_hex": lambda r: r["RUN"][0].__setitem__("buffer_hex", "0"),
            "buffer_short": lambda r: r["RUN"][0].__setitem__("buffer_hex", "00"),
            "result_non_hex": lambda r: r["RUN"][0].__setitem__("result_hex", "XX" * 32),
            "bad_cycle": lambda r: r["RUN"][0].__setitem__("cycle", "hot"),
        }
        for name, mutate in mutations.items():
            records = self.complete_records()
            mutate(records)
            with self.subTest(name=name):
                with self.assertRaisesRegex(track_integration.BlockedError, "BLOCKED:"):
                    track_integration.require_complete_records(records, "test")

    def test_complete_transcript_reaches_oracle_and_mismatch_fails(self):
        records = self.complete_records()
        track_integration.require_complete_records(records, "test")
        records["RUN"][0]["reason"] = "budget"
        records["RUN"][0]["halted_after_step"] = -1
        records["PHASE"] = []  # valid complete negative run: no phase transition occurred
        track_integration.require_complete_records(records, "test")
        self.assertEqual(track_integration.exit_for_error(ValueError("complete oracle mismatch")), 1)

    def test_partial_json_and_unreadable_artifacts_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            partial = root / "manifest.json"
            partial.write_text('{"symbols": ')
            with self.assertRaisesRegex(track_integration.BlockedError, "partial or unreadable JSON"):
                track_integration.read_json_artifact(partial)
            with self.assertRaises(track_integration.BlockedError):
                track_integration.read_artifact(root)
            artifact = root / "artifact"
            artifact.write_bytes(b"ok")
            with mock.patch.object(track_integration.os, "open", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(track_integration.BlockedError, "unreadable"):
                    track_integration.read_artifact(artifact)

    def test_symlink_artifact_and_replacement_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"; target.write_bytes(b"safe")
            link = root / "link"
            try:
                link.symlink_to(target)
            except OSError as error:
                self.skipTest(f"cannot create symlink in this environment: {error}")
            with self.assertRaisesRegex(track_integration.BlockedError, "symlink"):
                track_integration.read_artifact(link)
            replacement = root / "replacement"; replacement.write_bytes(b"other")
            before, after = target.lstat(), replacement.lstat()
            with mock.patch.object(Path, "lstat", autospec=True, side_effect=(before, before, after)):
                with self.assertRaisesRegex(track_integration.BlockedError, "changed while being read"):
                    track_integration.read_artifact(target)

    def test_partial_json_from_probe_is_blocked(self):
        with mock.patch.object(track_integration, "execute", side_effect=json.JSONDecodeError("partial", "{", 1)):
            with self.assertRaises(track_integration.BlockedError):
                track_integration.execute_complete("probe", "rom", "cue", "labels")

    def test_completed_oracle_failure_is_fail(self):
        self.assertEqual(track_integration.exit_for_error(ValueError("cold: RAM differs from sector data")), 1)
        self.assertEqual(track_integration.exit_for_error(ValueError("unexpected Geargrafx behaviour")), 1)
