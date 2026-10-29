# CD_READ ($03 / candidate `$E009`) calling contract (Issue #12)

Status: **callee ABI not verified, CD_READ NOT_IMPLEMENTED.** Nothing here was taken from a System Card
image or disassembly. A third-party name, caller wrapper or HLE model is never promoted to VERIFIED.

Status tags:

本文書の Status は**宣言上の状態（declared status）**である。実効信頼状態（effective trust state）は §4 の検査で決まり、
現時点の実効値は LOCAL_ONLY までで、VERIFIED は実効化されない。語彙は `docs/cd-base-abi-ledger.md` §1
（SOURCE_CONFIRMED / HYPOTHESIS / MEASURED / UNKNOWN / BLOCKED / VERIFIED）に従う。SOURCE_CONFIRMED と MEASURED は本文書の Status 値ではない。
本文書の T1〜T5 の根拠は、列挙したシナリオの実行結果（MEASURED）である。MEASURED は VERIFIED を自動的には意味しない。

* **VERIFIED**: 対象を限定し、要求される独立検証と信頼条件を満たした事実（`docs/cd-base-abi-ledger.md` §1 の承認方針）。
  本文書の宣言上の VERIFIED は、列挙したシナリオを原本ゲストコードが pinned real Geargrafx core
  （`b49ae82a012eade566d72e9d61c9d95a5caa4da3`）上で実行した結果を根拠とする表示である。ソースの記述、HLE、呼出側の期待、名前だけでは付けられない。
  宣言上の VERIFIED は、独立検証と信頼条件が満たされるまで実効 VERIFIED ではない。自己申告の bundle では実効 VERIFIED にならず、
  gh attestation の出力や`docs/cd-read-contract.md`（本文書）へ渡された run 記録でも成立しない（§4、#153）。このモジュールのどの関数も、現時点で実効 VERIFIED を返さない。
  仮に実効 VERIFIED になっても、転送層のエミュレータ証拠に限られる。実機と callee ABI は UNKNOWN のままである。
* **LOCAL_ONLY**: a declared VERIFIED claim whose cited scenarios a self-declared bundle records consistently
  (pinned revision/archive hash, ROM hash, exact scenario kinds). The trust meaning is the same as
  `LOCAL_ONLY` in `tools/evidence_provenance.py` (#125): no attestation, never compatibility evidence. The
  acceptance condition is looser: #125 reaches LOCAL_ONLY only after all its artifact checks pass (disc
  hashes, its full scenario table, `primary_observations.RUN`), while this ledger checks only the fields
  listed here. A forged bundle with correct hashes reaches this level, so it is not PASS and not VERIFIED.
* **HYPOTHESIS**: a pinned caller/HLE source shows an expectation; the System Card 3.0 callee was never executed.
* **UNKNOWN**: no admissible evidence. A VERIFIED claim whose cited scenarios are missing or not PASS is
  downgraded to UNKNOWN.
* **BLOCKED**: a VERIFIED claim that cannot be checked because no valid evidence bundle exists (no
  `build/<mode>/cdread-probe/cdread-evidence.json`, a bundle that is valid JSON but not an object, a `tests`
  entry that is not an object, a different ROM, or a non-pinned Geargrafx). BLOCKED is not PASS. A bundle that
  is not valid JSON or not UTF-8 is not BLOCKED: the CLI stops with `CD_READ contract FAIL` (exit 1).

Machine check: `python3 tools/cd_read_contract.py [--mode release]` (`tests/host/test_cd_read_contract.py`).
Every line it prints carries the trust label `LOCAL_ONLY`. `python3 tools/cd_read_contract.py --compat` is the
compatibility gate CLI; `compat_status()` is always BLOCKED for VERIFIED claims, so it always exits 3.
It fails if the ledger is malformed, if a claim cites another emulator, or if any slot-3 field in
[api_contracts.json](../spec/api_contracts.json) becomes VERIFIED, executed or implemented before this
document is updated. It exits 3 with `BLOCKED` when any VERIFIED transport claim is not backed by the bundle,
and exits 1 with `LOCAL_ONLY` when the bundle is consistent. It never prints PASS and never exits 0 on a bundle,
like `tools/evidence_provenance.py`, which exits non-zero for everything but an attested PASS.
The host unit tests run without a bundle, so they pass locally and report the transport claims as BLOCKED
by design; the CI job `make dev-cdread-integration` is what produces the bundle.

## 4. Evidence rule and limits

* The integration run writes `cdread-evidence.json` only after every scenario passed. Scenario keys are
  created only by executed runs, so a name in source text, a comment or an unexecuted branch is never evidence.
* Each cited scenario has one fixed result kind (`SCENARIO_KIND` in `tools/cd_read_contract.py`). The bundle's
  `tests[name].status` must equal that kind exactly, otherwise the claim is UNKNOWN:

  | Kind | Scenarios | Meaning |
  | --- | --- | --- |
  | `PASS` | `single_sector_lba5`, `first_sector_lba0`, `last_sector_lba15`, `three_sectors_lba5`, `disc_end_boundary_lba13_x3` | Normal read: the guest got the right bytes, judged by the independent comparison. |
  | `PASS_EXPECTED_FAILURE` | `wrong_sector_requested_lba6`, `invalid_sector_lba16`, `invalid_sector_lba_max`, `read_past_disc_end_lba14_x3` | Deliberately bad request: the judge had to reject it (or the guest had to stop with the expected error). |
  | `OBSERVED_EMULATOR_BEHAVIOUR` | `read6_on_audio_track_lba` | Emulator observation only. Written after the suite's assertion held. |

  A positive read recorded as `PASS_EXPECTED_FAILURE`, a bad request recorded as `PASS`, or any `NOT_TESTED`
  does not verify the claim. `tests/host/test_cd_read_contract.py` ties the table to the integration suite's
  call sites.
* **Provenance is self-declared, not authenticated.** The bundle's `status`, `geargrafx_revision`,
  `source_archive_sha256`, `rom_sha256` and `tests` are plain JSON fields written by the same process that
  produced them. The checker compares them with the pinned values and the ROM on disk, which catches a stale or
  mismatched bundle. It does **not** prove that the pinned Geargrafx actually ran, or that the suite was not
  edited or the JSON not hand-written. A forged `tests` map with a matching revision and hash is consistent,
  so the local result is LOCAL_ONLY, never PASS or VERIFIED. The output line says "SELF-DECLARED".
* **Trust levels.**

  | Level | Meaning | Where it is used | Status today |
  | --- | --- | --- | --- |
  | `LOCAL_ONLY` | Self-declared JSON. Nothing authenticates the producer, the suite source or the run. | `tools/cd_read_contract.py` output, `effective_status()` | In use. Development check for a ROM built and tested locally or in one CI job. Exit 1. |
  | attested (#125) | GitHub artifact attestation (`actions/attest-build-provenance`) of `cdread-evidence.json` and the probe ROM, signed by `api-source.yml` at `refs/heads/main`, checked by `gh attestation verify` and `tools/evidence_provenance.py` (recomputes ROM and fixture hashes, checks scenario kinds, run correlation). | `.github/workflows/api-source.yml` jobs `attest-cdread` / `verify-cdread-attestation` (push to main) | On main. Its PASS is an attested *emulator diagnostic* only (`compatibility: NOT_PROMOTED`). It does not raise any ledger status (#153, below). |

  **This change (#153, PR #154) proves neither compatibility nor authenticity.** It only fixes the gate at BLOCKED
  and connects the #125 verifier for diagnostics.

  **Gate (#153): no VERIFIED from caller-supplied data.** `compat_status()` returns BLOCKED for every declared
  VERIFIED claim, whatever it is given, and takes no attestation, run record, branch rules, fixture hash,
  repository or commit. Reason: `gh attestation verify --format json` output, a workflow run record and a
  `rules/branches/main` listing are plain JSON once they reach a Python function. gh checks the Sigstore
  signature, but its JSON output carries no signature this module could check, so a hand-written file with the
  right repository, commit, ref, workflow, runner, trigger and subject digests is indistinguishable from a real
  one (PR #154 review reproduced VERIFIED from such a file). A "trusted path" (only the `verify-cdread-attestation`
  job's own gh output) cannot be told apart in-process either: the function cannot see which process wrote its
  input. Rather than accept that, the gate stays closed; its meaning is LOCAL_ONLY-equivalent (not evidence).

  `compat_reason(artifact_bytes, attestation, rom_bytes, run)` is diagnostic only. It runs the #125 verifier
  with this checkout's values: repository `mao2009/oss-pce-cd-bios` (a fork never matches), commit = `git rev-parse
  HEAD`, the pinned Geargrafx, the signer workflow, the denylist, and fixture hashes regenerated by
  `tools/make_cd_image.py` (never the caller's). It returns the #125 finding, or, when #125 finds the inputs
  consistent, `BLOCKED: attestation authenticity cannot be checked in-process ...`. It never changes a status, and
  its text never contains the words PASS or VERIFIED (the #125 reasons are quoted with those words masked).

  A claim keeps its declared status only when it is a plain dict whose `status`, read once, is exactly the str
  `HYPOTHESIS` or `UNKNOWN`; VERIFIED, look-alike strings (trailing space, zero-width characters, other case), str
  subclasses and other mappings are BLOCKED.

  T6 to T8 keep their declared HYPOTHESIS / UNKNOWN (a `claims` list that marks them VERIFIED gets BLOCKED), and the
  callee ABI (#139, `spec/api_contracts.json` slot 3) is not touched. No new status was added. `integration_id` is
  not defined by #125 or this ledger and is not an identity: #125 binds by the subject digest of the exact artifact
  bytes plus the `runInvocationURI` run, so a self-declared `integration_id` field changes nothing.

  What an effective VERIFIED would need (not implemented): the result must be produced where the signature is
  checked, for example `compat_status` running inside the `verify-cdread-attestation` job and publishing its own
  signed attestation of the ledger result, which a consumer then verifies with gh. Branch rules would also have to
  come from the same trusted job; the current main `pull_request` rule requires 0 approvals and bypass actors are
  not visible, so "PR required" does not mean "reviewed".

  `effective_status()` (the local check) still stops at LOCAL_ONLY. Measured on the forged
  bundle of `TrustBoundaryTests` (no attestation): `tools/evidence_provenance.py` gives FAIL (exit 1) because
  `disc.bin_sha256` / `disc.cue_sha256` are missing, required scenarios are missing and
  `primary_observations.RUN` is missing; this checker gives LOCAL_ONLY (exit 1). #125 returns LOCAL_ONLY only
  when every artifact check passes, so this ledger's LOCAL_ONLY has a looser acceptance condition; the trust
  meaning (no attestation, not compatibility evidence) is the same. `tests/host/test_cd_read_contract.py::TrustBoundaryTests` checks that a forged bundle
  with the pinned revision and ROM hash is never PASS/VERIFIED locally and stays BLOCKED for compatibility.
* **Relation to #115 and #119 (drift guard).** The scenario kinds follow the #119 result vocabulary
  (PASS / FAIL / BLOCKED). End-of-disc runs use error code 5, which #115 `tools/cd_status.py` classifies as
  PREMATURE_STATUS. The BLOCKED exit code 3 and the `LOCAL_ONLY` label match #119 `geargrafx_harness.py`
  (`EXIT`, `artifact_policy`). These files are on main (`tools/cd_status.py`,
  `tests/integration/geargrafx_harness.py`). `TrustBoundaryTests` restates the values instead of importing them
  (no import of integration code into the host test). The bundle is not the #116 runner attestation shape
  (`verified`, `checkpoints`) and must never be passed to `tools/compat_evidence.py` as one.
* Regex limits: `tools/cd_read_contract.real_core_requests` parses the integration source with regular
  expressions to rebuild the expected LBA/count model. It is a drift guard, not evidence. The host test
  fails if a `suite.positive(` or end-of-disc `suite.failing_guest(` call is not matched, but a call written
  in an unusual form (for example split over lines before the match or with computed arguments) can still
  escape the model. The bundle does not yet record LBA/count for end-of-disc runs, so their model rows are
  not cross-checked against a run.
* Not verified by any of this: real hardware, a second emulator, the System Card 3.0 callee ABI (`$E009`),
  interrupt-driven transfer, no-disc behaviour.

## 変更履歴

* 2026-10-29（#573）: Hu-Go! だけを根拠にしていた「Destination mode」の `_dh=1/2/4/5`、「Count 0 ほか」、「Flags」、「Synchronous completion」を UNKNOWN へ下げた。`_dh` 行は HuCC 呼出側由来のモードを HYPOTHESIS のまま残した。Hu-Go! は参照のみとし、根拠に使わない（PR #571 の 148 側と整合）。旧版の「訂正:」注記（`_dh=2` の出典は Hu-Go! HLE のみ）は本節へ移し、本文には HuCC の確認事実だけを残した。

## 1. Callee ABI (System Card 3.0 service). No VERIFIED item

Sources (provenance `PENDING`, see [abi-contracts.md](abi-contracts.md#cd_read--slot-03-candidate-e009)):
HuCC `pce-devel/huc@54f5c73606c1e7142e8095c727cab2767e2b3cb3` (`hucc-systemcard.asm` L283-459,
`core-kernel.asm` L486-552, `pcengine.inc` L330-343, L468-552) and Hu-Go! HLE
`mckayemu/hugo@64ed226da2288738781743eeef228c069e8973a4` (`bios.c` L378-573).

| Item | Claim | Status | Source |
| --- | --- | --- | --- |
| Entry | slot `$03`, CPU `$E009` (`$E000 + 3*slot`); MPR7 = bank 0 at call | HYPOTHESIS | HuCC `pcengine.inc`, `core-kernel.asm` |
| Block number | 24-bit `_cl:_ch:_dl` (high:mid:low) at `$20FC/$20FD/$20FE` | HYPOTHESIS | HuCC callers, `get_file_lba` |
| Block base | relative to a base set by CD_BASE (#11) vs absolute LBA | UNKNOWN | - |
| Destination mode, modes 0 / 3 / 6 / `$FE` / `$FF` | `_dh`: 0 local bytes `_ax`; 3 starting bank `_bl`, `_al` sectors; 6 RAM bank `_bl`, `_al` sectors; `$FE` VRAM `_bx` bytes `_ax`; `$FF` VRAM `_bx` sectors `_al` | HYPOTHESIS | HuCC 呼出側（`include/hucc/` の範囲） |
| Destination mode, modes 1 / 2 / 4 / 5 | `_dh`: 1 local sectors; 2, 4, 5 RAM bank `_bl`, `_al` sectors | UNKNOWN | Hu-Go! HLE のみ（参照のみ、ライセンス未確定。根拠に使わない）。HuCC は `_dh=2` を書かないことを include/hucc/ の範囲で確認（独立根拠なし） |
| Mode list complete / invalid mode result | - | UNKNOWN | - |
| Count 0, bank wrap, odd VRAM byte count, upper bounds | 未確認（Hu-Go! HLE は `$FF` count 0 で A=`$22` と示すが参照のみ） | UNKNOWN | 実コアの callee 実行が必要 |
| Return | A=0 success, nonzero error | HYPOTHESIS | HuCC 呼出側の TAX/BNE（呼出側の期待。呼出先の成功値の証明ではない）。Hu-Go! は参照のみ（根拠に使わない。ライセンス未確定）。根拠の弱さ: 呼出側の比較のみで、callee の戻り値は未測定 |
| Error codes (no disc, bad block, read error, busy/timeout) | - | UNKNOWN | - |
| Flags (N/Z/C/T/I/D) on return | 未確認（Hu-Go! HLE のみが記述。参照のみ、根拠に使わない） | UNKNOWN | - |
| X/Y and work-RAM `$20F8-$20FF` clobber/preserve | HuCC saves `_bx.._dl` around calls (caller protection, not a clobber list) | HYPOTHESIS | HuCC |
| Preserved registers / RAM | - | UNKNOWN | - |
| MPR | caller restores MPR3 itself; HuCC comments that CD_READ corrupts it on error | HYPOTHESIS | HuCC |
| Stack | JSR in / RTS out via HuCC `call_bios`; callee depth and balance | HYPOTHESIS / UNKNOWN | HuCC |
| Interrupts | HuCC overlay loader runs CLI before CD_READ; BIOS IRQ requirement | HYPOTHESIS / UNKNOWN | HuCC |
| Synchronous completion, timing | 未確認（Hu-Go! HLE のみが同期ループを記述。参照のみ、根拠に使わない） | UNKNOWN | - |

## 2. Transport layer (what any CD_READ must do on the bus). Emulator-verified part

T1 から T5 の Status 列は宣言上の状態である。T1 から T4 の VERIFIED は、対応する real-core シナリオ（§4 の表）の PASS が
bundle にあるかで検査される。bundle がなければ BLOCKED、シナリオの欠落や結果の不一致は UNKNOWN に下げられる。
bundle は自己申告のため、実効信頼状態は LOCAL_ONLY 止まりである。T5 の「Geargrafx behaviour only」は Geargrafx の挙動の測定であり、実機の挙動の証拠ではない。
T6 から T8 は宣言どおり HYPOTHESIS / UNKNOWN で、実行の証拠を持たない。

Executed by `src/cd_read_probe.s` (polling, IRQs off) through `$1800-$1804`; details in
[cd-read-diagnostic.md](cd-read-diagnostic.md). Model: `tools/cd_read_contract.transport_outcome`.

| Id | Claim | Status | Real-core scenarios |
| --- | --- | --- | --- |
| T1 | READ(6) LBA *n* on the fixture (MODE1/2352, `INDEX 01 00:00:00`) delivers BIN sector *n*, 2048 bytes; LBA field is 21 bits | VERIFIED | `single_sector_lba5`, `first_sector_lba0`, `last_sector_lba15`, `wrong_sector_requested_lba6` |
| T2 | count 1..3 delivers consecutive sectors, count*2048 bytes | VERIFIED | `three_sectors_lba5`, `disc_end_boundary_lba13_x3` |
| T3 | start LBA >= disc end: STATUS phase instead of DATA_IN, 0 bytes, destination untouched | VERIFIED | `invalid_sector_lba16`, `invalid_sector_lba_max` |
| T4 | request running off the end: sectors up to the end delivered intact, then STATUS | VERIFIED | `read_past_disc_end_lba14_x3` |
| T5 | READ(6) on a CD-DA LBA returns GOOD + raw PCM (no error); firmware must check track type itself | VERIFIED (Geargrafx behaviour only) | `read6_on_audio_track_lba` |
| T6 | status byte GOOD (T3) / CHECK CONDITION (T4) | HYPOTHESIS (Geargrafx source reading) | - |
| T7 | no disc / zero-sector image | UNKNOWN (Geargrafx does not run without media) | - |
| T8 | count 0 (SCSI: 256), count > 3, drive busy/spun down, retries, real hardware | UNKNOWN | - |

`transport_outcome` raises instead of guessing for T7/T8 inputs, and the host test checks it against every
positive and end-of-disc request in the real-core suite.

## 3. What is implemented, what is blocked

* Implemented: this ledger, the transport outcome model and their host tests. No ROM routine was added:
  every input/output/register/MPR/error item of the callee ABI is HYPOTHESIS or UNKNOWN, so wiring `$E009`
  would fix an unverified ABI.
* Blocked on source evidence (needs an admissible, independently corroborated System Card 3.0 behaviour
  record under #8, not a name or HLE): block-number base (#11 CD_BASE), destination-mode list and their
  count/address units, error codes and no-disc result, A/X/Y/P and `$20F8-$20FF` preservation, MPR state on
  success and error, stack depth, IRQ prerequisites, timing.
* Blocked on the emulator: no-disc, drive busy, interrupt-driven transfer, `$1808` auto-ACK; a second
  emulator and real hardware are NOT_TESTED.
