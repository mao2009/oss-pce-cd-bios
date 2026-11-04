; EXPERIMENTAL DEVELOPMENT-ONLY 256 KiB System-Card-sized image reset path.
; This is original project code. It is NOT a compatible System Card BIOS, makes NO ABI claim,
; and is NOT a callable BIOS service. It deliberately reuses only the project's confirmed
; synthetic-media diagnostic flow:
; reset -> minimum CPU/MPR/stack setup -> LBA 0 synthetic-media check -> READ(6) LBA 5
; -> staging RAM $2800 -> copied payload RAM $3000 -> payload handoff.
;
; The normal DEV ROM remains on src/boot.s's stop loop. This unit is linked exclusively by
; tools/build_experimental_bios_boot.py with EXPERIMENTAL_BIOS_BOOT set on the vector owner.
.setcpu "huc6280"
.segment "BOOT"
.export experimental_bios_reset, experimental_bios_init, experimental_bios_media_halt
.import cd_entry, cd_media_state

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
    jsr cd_media_state       ; original diagnostic: READ(6) LBA 0 and synthetic magic check
    cmp #$01                 ; only MEDIA_SIGNATURE_OK may request the payload sector
    bne experimental_bios_media_halt
    jmp cd_entry             ; existing diagnostic: read LBA 5, RAM copy, then payload handoff

; Fail closed. This does not represent a System Card error or reset contract.
experimental_bios_media_halt:
    bra experimental_bios_media_halt
