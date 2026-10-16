; Original DEVELOPMENT-ONLY payload for the load-and-execute experiment (Issues #14 and #5).
; Assembled separately and stored in the synthetic CD data sector (after an 8-byte header).
; It runs from RAM at $3000 (MPR1 = work RAM) and writes an observable marker, then returns
; through the mailbox vector that stage 0 wrote at $3F10. Addresses match src/cd_load_exec_probe.s.
.setcpu "huc6280"
.export payload_entry, payload_done   ; exported so release builds (no -g) keep the labels
.segment "PAYLOAD"
MARK   = $3F00
RETVEC = $3F10

payload_entry:
    lda #$A5
    sta MARK
    lda #$5A
    sta MARK+1
    tma #$02                 ; A = MPR1 as seen by the payload
    sta MARK+4
    tsx
    txa                      ; A = stack pointer as seen by the payload
    sta MARK+5
    lda #$3C
    sta MARK+6
payload_done:
    jmp (RETVEC)
