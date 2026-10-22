; Issue #501: DEVELOPMENT-ONLY internal boot path. NOT a BIOS, NOT compatible with any retail or System Card
; behaviour, NOT DEV_CALLABLE, NOT a verified ABI. Host never writes guest RAM: every byte is moved by
; guest HuC6280 code that issues READ(6) through $1800-$1804.
;
; Path: reset -> minimal MPR0/MPR1/SP -> cd_media_state (READ(6) LBA 0, must return $01 MEDIA_SIGNATURE_OK)
;       -> jmp cd_entry (src/cd_load_exec_probe.s: READ(6) LBA 5, 2048 bytes to $2800, copy to $3000, jmp).
;
; Result-block allocation (no shared bytes between the two blocks):
;   $2300..$231F  cd_load_exec_probe.s   load/execute result (RES), cleared by cd_entry
;   $2800..$2FFF  staging sector (BUF), shared sequentially: media read, then the requested sector
;   $3000..$30FF  payload (EXEC), src/cd_load_exec_payload.s
;   $3F00..$3F06  payload marker, $3F10 RETVEC, $3F12 EXECVEC (cd_load_exec_probe.s)
;   $3F40..$3F46  cd_media_state result (MS_RES, this path only); outside the $3F00 marker area
; The probe dumps $2800-$3FFF, so the host can check both result blocks.
;
; Built only with -D DEV_BOOT_INTERNAL on cd_load_exec_probe.s (reset vector) and -D MS_RES=$3F40 on
; cd_media_state.s. tools/build_dev_boot_internal.py performs that build.
.setcpu "huc6280"
.segment "BOOT"
.export dev_boot_reset, dev_boot_entry, dev_media_halt
.import cd_entry, cd_media_state

; The reset vector targets this one-byte SEI. The probe executes the reset instruction before its first label
; check, so dev_boot_entry (the next address) is the first labelled checkpoint in both cold and warm runs.
dev_boot_reset:
    sei
dev_boot_entry:
    cld
    lda #$FF
    tam #$01                 ; MPR0 = hardware IO bank ($1800 CD-ROM2)
    lda #$F8
    tam #$02                 ; MPR1 = work RAM ($2000-$3FFF)
    ldx #$FF
    txs                      ; stack inside the work-RAM page, as in src/reset_entry.s
    jsr cd_media_state       ; A = media state; result block at MS_RES
    cmp #$01                 ; only MEDIA_SIGNATURE_OK may continue to the READ(6) of the payload sector
    bne dev_media_halt
    jmp cd_entry             ; cd_entry re-initialises MPR2..MPR7, clears $2300 and issues READ(6) LBA 5

; Not a success label: an unrecognised or unreadable synthetic disc stops here, never reaching READ(6) LBA 5.
dev_media_halt:
    bra dev_media_halt
