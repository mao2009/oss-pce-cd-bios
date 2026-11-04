# Experimental System-Card-sized boot image

**Status: EXPERIMENTAL / DEV-only / not compatible.** `make experimental-bios-boot` produces a 256 KiB image for a narrow, project-owned synthetic-media experiment. It is **not** a compatible System Card BIOS, makes **no System Card ABI claim**, exposes no new callable API, and is not release eligible.

The default ROM is unchanged: `src/boot.s` still deliberately stops at reset. This opt-in image instead links original reset code from `src/experimental_bios_boot.s`:

```
reset -> minimal MPR0/MPR1/stack init -> validate the host-built selection record
      -> cd_set_read_lba (record's INDEX 01 LBA) -> cd_entry_selected
      -> READ(6) of that one sector -> RAM staging/copy -> payload handoff
invalid record (magic, version, format, kind, track 1..99, 21-bit LBA)
      -> experimental_bios_selection_halt, before any READ(6)
```

The selection record is computed on the **host** by `tools/experimental_track_selection.py` from an original synthetic CUE (first MODE1/2352 track, its INDEX 01 position after any pregap) and assembled into the image. The guest does **not** parse a CUE, issue READ TOC, or select a track itself; an audio-only CUE is refused by the host and never produces a normal record. The transfer, RAM copy, and handoff reuse `src/cd_load_exec_probe.s`; its plain `cd_entry` always uses the ROM CDB template (it never trusts power-on RAM), and only `cd_entry_selected` keeps the CDB built by `cd_set_read_lba`.

All of this is established only for repository-generated synthetic fixtures and pinned Geargrafx; it does not establish a retail-disc format, physical-hardware behavior, boot ABI, or commercial-game compatibility.

## Opt-in commands

```sh
make experimental-bios-boot EXPERIMENTAL_CUE=path/to/original-synthetic.cue
make experimental-bios-boot-integration
```

`experimental-bios-boot` requires `EXPERIMENTAL_CUE` naming the exact original synthetic CUE to analyse; without it the target is `BLOCKED`. There is no fallback LBA.

The integration command builds the pinned Geargrafx diagnostic frontend and generates its synthetic CD fixtures at runtime. It does not inspect `rom/` or require private firmware/discs. It checks, cold and warm:

- single data track (LBA 0) and mixed audio + pregap media with a dynamic, non-LBA-5 INDEX 01 selection: the reset-vector boot path by label order and PC, SP at halt, a clean load/execute result block, the selected 2048-byte sector, the payload copy at `$3000`, the handoff marker, and exactly one READ(6) of the selected LBA;
- host refusal of an audio-only CUE;
- guest refusal of a host-valid image with one record field corrupted (magic, version, format, kind, track 0, track 100, LBA above 21 bits, and kind with an audio-only medium mounted): each halts at `experimental_bios_selection_halt` with no SCSI command/data phase.

It writes `build/<mode>/experimental-bios-boot/experimental-track-contract-evidence.json`. A successful result is **synthetic-disc real-core diagnostic evidence only**; it is not System Card BIOS API execution or a game boot result. Missing pinned tools/core is `FAIL/BLOCKED`, not PASS.
