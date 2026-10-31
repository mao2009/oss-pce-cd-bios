; EXPERIMENTAL DEVELOPMENT-ONLY 256 KiB System-Card-sized image reset path.
; This is original project code. It is NOT a compatible System Card BIOS, makes NO ABI claim,
; and is NOT a callable BIOS service. It deliberately reuses only the project's confirmed
; host-to-guest synthetic selection contract:
; reset -> minimum CPU/MPR/stack setup -> validate a host-populated selection
; record -> READ(6) at its selected INDEX 01 LBA -> staging RAM $2800 -> copied
; payload RAM $3000 -> payload handoff. This does not parse a CUE or a TOC.
;
; The normal DEV ROM remains on src/boot.s's stop loop. This unit is linked exclusively by
; tools/build_experimental_bios_boot.py with EXPERIMENTAL_BIOS_BOOT set on the vector owner.
.setcpu "huc6280"
.segment "BOOT"
.export experimental_bios_reset, experimental_bios_init, experimental_bios_selection_halt, experimental_selection
.import cd_entry, cd_set_read_lba

; The build tool places only original, host-analysed synthetic CUE information
; here. Record: "XSEL", version 1, format 1, kind 1 (MODE1/2352 INDEX 01),
; track 1..99, then a 21-bit absolute LBA in high/middle/low order. The ROM
; validates its fixed prefix and every variable field; kind 0 deliberately
; denotes an audio/no-data selection and is refused.
.ifndef EXP_SELECTED_KIND
EXP_SELECTED_KIND = 1
.endif
.ifndef EXP_SELECTED_TRACK
EXP_SELECTED_TRACK = 1
.endif
.ifndef EXP_SELECTED_LBA_HI
EXP_SELECTED_LBA_HI = 0
.endif
.ifndef EXP_SELECTED_LBA_MID
EXP_SELECTED_LBA_MID = 0
.endif
.ifndef EXP_SELECTED_LBA_LO
EXP_SELECTED_LBA_LO = 5
.endif

; Keep the vector target to one instruction so the real-core trace can distinguish reset from init.
experimental_bios_reset:
    sei
experimental_bios_init:
    cld
    lda #$FF
    tam #$01                 ; MPR0: hardware I/O, including CD-ROM2 at $1800
    lda #$F8
    tam #$02                 ; MPR1: work RAM at logical $2000-$3FFF
    ldx #$FF
    txs                      ; stack is logical $21FF in that work-RAM mapping
    ldx #0
@record_prefix:
    lda experimental_selection,x
    cmp experimental_selection_prefix,x
    bne experimental_bios_selection_halt
    inx
    cpx #6                   ; magic, version, and record format
    bne @record_prefix
    ldx experimental_selection+6 ; kind must be MODE1/2352 INDEX 01
    cpx #1
    bne experimental_bios_selection_halt
    ldx experimental_selection+7 ; track number is a host selection sanity check
    beq experimental_bios_selection_halt
    cpx #100
    bcs experimental_bios_selection_halt
    lda experimental_selection+8 ; LBA high byte contains only READ(6)'s 5 bits
    and #$E0
    bne experimental_bios_selection_halt
    lda experimental_selection+10 ; helper consumes low/mid/high, not a fixed LBA
    ldx experimental_selection+9
    ldy experimental_selection+8
    jsr cd_set_read_lba
    jmp cd_entry

; Fail closed before any READ(6). This does not represent a System Card error
; or reset contract.
experimental_bios_selection_halt:
    bra experimental_bios_selection_halt

experimental_selection:
    .byte "XSEL", 1, 1, EXP_SELECTED_KIND, EXP_SELECTED_TRACK
    .byte EXP_SELECTED_LBA_HI, EXP_SELECTED_LBA_MID, EXP_SELECTED_LBA_LO
experimental_selection_prefix:
    .byte "XSEL", 1, 1
