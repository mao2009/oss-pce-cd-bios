# Experimental System-Card-sized boot image

**Status: EXPERIMENTAL / DEV-only / not compatible.** `make experimental-bios-boot` produces a 256 KiB image for a narrow, project-owned synthetic-media experiment. It is **not** a compatible System Card BIOS, makes **no System Card ABI claim**, exposes no new callable API, and is not release eligible.

The default ROM is unchanged: `src/boot.s` still deliberately stops at reset. This opt-in image instead links original reset code from `src/experimental_bios_boot.s`:

```
reset -> minimal MPR0/MPR1/stack init -> synthetic LBA 0 media check
      -> existing synthetic READ(6) LBA 5 -> RAM staging/copy -> payload handoff
```

The check, transfer, RAM copy, and handoff reuse existing project diagnostic sources (`src/cd_media_state.s` and `src/cd_load_exec_probe.s`). They are only established for the repository-generated synthetic fixture and pinned Geargrafx; this reuse does not establish a retail-disc format, physical-hardware behavior, boot ABI, or commercial-game compatibility.

## Opt-in commands

```sh
make experimental-bios-boot
make experimental-bios-boot-integration
```

The integration command builds the pinned Geargrafx diagnostic frontend and generates its synthetic CD fixture at runtime. It does not inspect `rom/` or require private firmware/discs. A successful result is **synthetic-disc real-core diagnostic evidence only**; it is not System Card BIOS API execution or a game boot result. Missing pinned tools/core is `FAIL/BLOCKED`, not PASS.
