# CD_READ commercial first-mode experiment (Issue #12 / #11, Agent-B G5 P1)

Status: **callee ABI not verified, CD_READ NOT_IMPLEMENTED for the genuine BIOS.
NOT a compatible System Card BIOS. NOT DEV_CALLABLE. release_eligible=false.
G5_OBSERVED=false.** Nothing here was taken from a System Card image or
disassembly. This document records a public-spec review (Phase A), a BLOCKED
commercial observation (Phase B), one experimental first-mode implementation
(Phase C), and its synthetic + opt-in-commercial evidence plan (Phases D/E).

Reference counters (unchanged by this experiment):
JSR executed on genuine callee 0/81, ABI VERIFIED 0/81, DEV_CALLABLE false,
release_eligible false, G5 (commercial genuine) false.

## Phase A: public-spec review vs the existing link

Public caller-side sources (all caller behaviour, never callee proof):

| # | Public source | Reports (caller-side fact) | Callee status |
| --- | --- | --- | --- |
| A1 | HuCC `hucc-systemcard.asm` L381-384 (`_cd_loaddata`) | writes `_dh=0`, `_ax` byte length | HYPOTHESIS for callee; code fact [C] for the write |
| A2 | HuCC `hucc-systemcard.asm` L425-457 (`_cd_loadbank`) | writes `_dh=3` (priority观 candidate per `docs/188-cd-read-transfer-mode-contract.md` D1) | UNKNOWN for callee |
| A3 | HuCC `hucc-systemcard.asm` L294/322 | writes `_dh=$FF/$FE` (VRAM signal, comment-derived) | UNKNOWN for callee |
| A4 | HuCC `core-kernel.asm` L593-594 (overlay loader) | writes `_dh=6` (MPR6 path) | UNKNOWN for callee |
| A5 | stum.de second-data-track article (W1) | `_cl=1/2` recbase select, retry on recbase1, `_bh=$80` track mode | HYPOTHESIS (secondary source) |
| A6 | llvm-mos `pce_cdb_cd_read` | caller-side `cd_read` wrapper shape | HYPOTHESIS (caller only) |
| A7 | `docs/cd-read-contract.md` R2 | block 24-bit `_cl:_ch:_dl` at `$20FC/$20FD/$20FE` | HYPOTHESIS (HuCC callers) |
| A8 | `docs/cd-read-mode-contract.md` R3/R8 | `_dh=2` UNKNOWN (Hu-Go! HLE only, not grounds) | UNKNOWN |

Existing link (`src/cd_read_link.s`, CD_READ_LINK_V1) operand contract:
block LO/MID/HI at `$20F2/$20F3/$20F4`, window start at `$20F5/$20F6/$20F7`,
WINDOW_LEN=16 fixed, transfer exactly 1 sector to fixed `$2800`, base recbase0
at `$2400-$2402`. The `$20F2` operand and the 16-window are **synthetic-only**:
no public caller source writes `$20F2` for CD_READ, and no source bounds a data
track to 16 sectors.

Decision (with grounds, not a lift-without-grounds):
the commercial first-mode routine (`src/cd_read_commercial.s`) reads the block
from `$20FC/$20FD/$20FE` (A7) and ignores `$20F2-$20F4`; it enforces the INDEX01
lower bound but NO 16-sector window end. Grounds: (1) A7 is the only public
block-operand record; (2) the pinned 2-base commit message records commercial
CUE[1] reaching `$03 $64 AFTER` past the synthetic window on an in-disc LBA
(a070d24a, L2 note) -- the window is a fixture bound, not a drive bound;
(3) past-end on real media is judged by the drive STATUS path (T3/T4), never
precomputed. `$64 AFTER` is therefore RESERVED and never returned by the new
routine (taxonomy comparison with link V1 stays mechanical).

Out of scope (fail-closed): `_dh=1/2/3/4/5/6/$FE/$FF`, multi-sector counts,
byte counts other than 2048, destinations other than `$2800`. No `$60-$71`
value is presented as genuine: all are EXPERIMENTAL codes.

## Phase B: commercial observation (BLOCKED here)

Method (opt-in only): rebuild in the worker's own out dir from pinned-SHA
files, `--commercial-index1` read-only, snapshot unchanged, sanitized CUE[1]
anonymity (no names/paths/hashes/bytes in logs or artifacts).

Result in THIS worktree: **SKIP/BLOCKED -- `./rom/` does not exist here**, so
no commercial-media run was attempted and none is claimed. Per AGENTS.md, the
absence is reported as SKIP/BLOCKED, never PASS. The `$02 $00/$00` +
`$03 $64 AFTER` loop reproduction, and the 7-answer classification
(destination_mode / relative_sector / sector_count / target_range / bank_bounds /
track_bounds / read6 / current-`$64` with `$20FC`-vs-`$20F2` proven by execution,
not guess), remain OPEN and are owned by a follow-up run with media present.
The 2-base commit note (a070d24a L2) is cited as prior observation only, not as
this branch's evidence.

## Phase C: FIRST commercial mode implementation

`src/cd_read_commercial.s` (CD_READ_COMMERCIAL_V1) + host mirror
`tools/cd_read_commercial.py`. FIRST mode only: `_dh=0` LOCAL bytes, exactly
2048 bytes (`_ax=$0800`) to logical `$2800` (end-exclusive `$3000`).

- LBA = recbase0 + commercial block, 24-bit with carry; carry or HI>=`$20`
  refused (`$62`); LBA < INDEX01 refused (`$63` pregap/audio positional guard;
  track KIND is UNKNOWN -- no TOC is read).
- `_dh!=0` -> `$65`, size != 2048 -> `$66`, dest != `$2800` -> `$67`, all before
  any `$1800` access. No fallback, no truncation, no wrap.
- Success (`$00`) only after an actual 2048-byte DATA_IN with GOOD
  status/message and bus-free observed. Fail (`$70`) and timeout (`$71`) follow
  the link-V1 two-path/stage taxonomy with CRES-local bytes.
- CRES at `$2330-$2339` (separate from link V1 `$2320-$2329`, so control vs
  experimental runs are comparable without overwrite); CDB at `$27E8` on the
  valid path only. X/Y/MPR preserved (no TAM/TXS), I/D/V/T preserved, SP
  balanced, 2048-byte bound never exceeded (`$2800` + 2048 = `$3000` exactly;
  refusals leave `$2800` untouched; partial counts mirror the READ loop).

## Phase D: 15 synthetic RED-GREEN checks

Host (`tests/host/test_cd_read_commercial_host.py`, 18 tests covering the 15
required items): rel hi/mid/lo, +recbase0, 21-bit cap, track BEFORE bound,
outside-window acceptance (no `$64`), size limits, mode ok/refuse, dest bounds,
`$20F2` independence, state/window invalids, premature/tail-bad/timeout paths.
Real-core (`tests/integration/test_g5_cdread_commercial.py`, 12 runs):
c01 valid memcmp vs BIN frame, c02 distinct sector, c03 win+16 accepted +
`$03`-control `$64` on the same LBA, c04 BEFORE canary, c05a drive-judged
past-end + c05b `$62`, c06 `$65`, c07 `$66`, c08 `$67`, c09 `$20F2` garbage
independence, c10 cold==warm + 2-base link, c11 `$60`, c12 state/input
preservation + payload intact. Old regression suites are untouched (separate
files; no mix beyond the pinned-SHA dependency).

## Phase E: control vs experimental outputs (14 observables)

| # | Observable | Control (link V1 `$E009`) | Experimental (commercial) |
| --- | --- | --- | --- |
| 1 | A code | `$64` at win+16 | `$00` at win+16 |
| 2 | C/Z flags (P) | error | success |
| 3 | result code area | `$2320` = `$64` | `$2330` = `$00` |
| 4 | stage | 1 (refusal) | 8 (transferred) |
| 5 | byte count | 0 | 2048 |
| 6 | reported LBA | win+16 | win+16 (same address, different verdict) |
| 7 | `$2800` content | canary intact | BIN frame memcmp |
| 8 | opposite result area | `$2330` untouched | `$2320` untouched |
| 9 | `$20F2` sensitivity | verdict changes | verdict unchanged |
| 10 | X/Y | preserved | preserved |
| 11 | SP | balanced | balanced |
| 12 | MPR0-7 | preserved | preserved |
| 13 | no-OOB (state/inputs/payload) | preserved | preserved |
| 14 | cold vs warm | equal | equal |

Observability ladder (project-local): L0 assembled, L1 host mirror green,
L2 one real-core valid transfer with memcmp, L3 refusal suite green on core,
L4 control-vs-experimental divergence table (above) green, L5 independent
review + Required CI green. Target: L4+. G5 (commercial-media genuine
observation) is BLOCKED here and NOT claimed.

## Gates

JSR on genuine callee 0/81. ABI VERIFIED 0/81. DEV_CALLABLE false.
release_eligible=false. G5_OBSERVED=false. No title claims compatibility.
