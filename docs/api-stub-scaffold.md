# Issue #100 — fail-closed API-entry scaffold (DEV ONLY)

This change is **not** a compatible BIOS, a retail game boot, or even a confirmed emulator boot.

## Purpose and safety

- Main System Card 3.0 jump-table *candidates* $00..$50 are emitted as 81 three-byte absolute JMPs at CPU-visible $E000 + 3×slot.
- Generated fallback trampolines set the HuC6280 **X register to the slot ID** then JMP to a shared infinite BRA loop. There is **no RTS, BRK, success return value, or writes to backup memory**.
- Reset enters a separate nonreturning loop with X=$FF. Therefore loading the 256 KiB ROM should **not** be interpreted as booting CD-ROM games.
- The first 8 KiB is linked to a provisional CPU-visible $E000..$FFFF mapping and padded with $FF to a **development-only** 256 KiB image. Verify map/image acceptance in Geargrafx and another emulator before treating this as a supported firmware layout.
- Each later API implementation can add `src/overrides/slot_XX.s` (exporting `api_slot_XX` in `OVERRIDES` segment). The generator detects it and omits **only that fallback**, so the 81-entry table stays stable.
- API $48 is the PSG_BIOS dispatcher; its nine inventoried subcommands are **not** extra table slots.

## Commands (no Docker required)

```sh
python3 tools/build_rom.py --check-source
python3 -m unittest discover -s tests -v
# ca65 + ld65 required for the next step
python3 tools/build_rom.py
```

`--check-source` validates candidate-slot count/order and generated assembler source; it is **not** a ROM build or emulator result. If ca65 or ld65 is absent, the build exits **BLOCKED**, not PASS.

`out/dev-only-not-compatible-syscard3.pce` exists **only when** ca65+ld65 finish, the binary jump table passes verification and the image is exactly 256 KiB. The file is not a release candidate.

## Validation still required

- Confirm linker configuration, bank ordering and vectors against independent System Card 3.0 behavior and real emulator execution.
- Verify at least two API calls on a real Geargrafx HuC6280 core reach their correct diagnostic X markers.
- Transition to original working reset/boot code and each API contract only after #1/#2/#3/#6/#8 gates. An API override can return only when its **specific** ABI contract and tests allow it.
- The release gate must reject every remaining fallback. Such a gate has **not yet been implemented**.

See [#100](https://github.com/mao2009/oss-pce-cd-bios/issues/100) and [API inventory](api-inventory.md).
