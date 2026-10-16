#!/usr/bin/env python3
"""Fail-closed reference BIOS run preflight. This does NOT invoke an emulator."""
import argparse
import json
from pathlib import Path

EXPECTED_SCHEMA = "oss-pce-cd-bios/reference-bios-run-config/v1"
REQUIRED = {"schema", "api_id", "bios_path", "bios_sha256", "test_rom_path",
            "test_rom_sha256", "emulator_revision", "max_instructions",
            "ram_write_allowlist", "isolated_run", "reviewed_abi", "approved_fixture"}
HEX = set("0123456789abcdef")


def validate(config):
    if not isinstance(config, dict) or set(config) != REQUIRED:
        return ["invalid configuration fields"]
    errors = []
    if config["schema"] != EXPECTED_SCHEMA:
        errors.append("unknown schema")
    if not isinstance(config["api_id"], str) or not config["api_id"]:
        errors.append("api_id is required")
    for name in ("bios_sha256", "test_rom_sha256"):
        value = config[name]
        if not isinstance(value, str) or len(value) != 64 or any(x not in HEX for x in value):
            errors.append(f"{name} must be lowercase sha256")
    for name in ("bios_path", "test_rom_path", "emulator_revision"):
        if not isinstance(config[name], str) or not config[name].strip():
            errors.append(f"{name} missing")
    if type(config["max_instructions"]) is not int or not 0 < config["max_instructions"] <= 10_000_000:
        errors.append("invalid bounded instruction budget")
    if not isinstance(config["ram_write_allowlist"], list) or not config["ram_write_allowlist"]:
        errors.append("explicit RAM write allowlist required")
    if any(not isinstance(x, dict) or set(x) != {"start", "end"} or
           type(x["start"]) is not int or type(x["end"]) is not int or
           not 0 <= x["start"] < x["end"] <= 65536
           for x in config["ram_write_allowlist"]):
        errors.append("invalid RAM allowlist interval")
    for name in ("isolated_run", "reviewed_abi", "approved_fixture"):
        if config[name] is not True:
            errors.append(f"{name} must be explicitly true")
    return errors


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("config", type=Path)
    a = ap.parse_args(argv)
    try:
        config = json.loads(a.config.read_text(encoding="utf-8"))
        errors = validate(config)
    except (OSError, ValueError, TypeError) as e:
        errors = [f"config unreadable: {type(e).__name__}"]
    if errors:
        print("BLOCKED: " + "; ".join(errors))
        return 2
    # No emulator invocation is implemented yet. Preflight-only success is never a test PASS.
    print("PREFLIGHT_ONLY: config shape accepted; execution NOT_RUN; emulator adapter NOT_IMPLEMENTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
