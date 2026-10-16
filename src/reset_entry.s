; Issue #143: minimal original reset entry. Not a complete BIOS; no API/CD/IRQ service.
; Only facts sourced in spec/rom-contract.json are used:
;   reset_mapping (MPR7=0, vectors $FFF6..$FFFF), cpu_reset_flags (I=1, D=0),
;   cd_io (bank $FF, needs MPR0), work_ram (bank $F8), cpu_stack (logical $2100..$21FF via MPR1).
; NOT initialized (UNKNOWN): MPR2..MPR6, CSH speed, stack-pointer ABI, interrupt ABI.
.setcpu "huc6280"
.segment "BOOT"
.export reset_entry, reset_mpr_ready, reset_ready, reset_unexpected_interrupt
reset_entry:
    sei
    cld
    lda #$FF
    tam #$01                 ; MPR0 = hardware IO bank
    lda #$F8
    tam #$02                 ; MPR1 = work RAM bank, i.e. CPU stack page $2100
reset_mpr_ready:
    ldx #$FF                 ; SP choice inside the stack page; not a Hudson/System Card ABI
    txs
.ifdef CD_MEDIA_STATE
.import cd_media_state
    jsr cd_media_state       ; Issue #144 observation point (opt-in build); result in RAM, not boot success
.endif
reset_ready:
    bra reset_ready          ; verifiable control point; never reports boot success
reset_unexpected_interrupt:
    ldx #$EE
@halt:
    bra @halt

.segment "VECTORS"
    .word reset_unexpected_interrupt ; IRQ2 / BRK
    .word reset_unexpected_interrupt ; IRQ1
    .word reset_unexpected_interrupt ; Timer
    .word reset_unexpected_interrupt ; NMI
    .word reset_entry                ; Reset
