; DEV-ONLY EXPERIMENT stub for slot $02 (Issue #756). NOT System Card CD_BASE.
;
; SIMULATED return: an immediate RTS that preserves every register, flag, MPR
; and RAM byte (only PC/SP move, as JSR/RTS themselves dictate). This explores
; PURE CONTROL FLOW after a hypothetical slot-$02 return; it carries no claim
; about genuine CD_BASE return values, flags, MPR effects, timing, or errors.
;
; Assumptions (EXPLICIT, NOT genuine ABI):
;   - the caller observes entry regs/flags/MPR unchanged on return;
;   - no memory, MMIO, IRQ, or CD-device side effect is produced;
;   - any post-return game behavior is CONDITIONAL on this fabrication and is
;     reported as SIMULATED_*, never as API-entry confirmation or compatibility.
;
; This source is NEVER linked into the normal DEV ROM or normal loader BIOS:
; it lives outside src/overrides/ (which tools/build_rom.py::overrides() scans),
; and only tools/build_slot02_return_loader.py assembles it, behind an explicit
; opt-in, for a distinctly named experiment image.
.setcpu "huc6280"
.segment "CODE"

slot02_experiment_return:
    rts
