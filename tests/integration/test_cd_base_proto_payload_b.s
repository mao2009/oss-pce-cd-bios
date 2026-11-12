; EXPERIMENTAL CD_BASE proto V1 test payload B: abnormal taxonomy.
; 4 failing calls (track/flags/reserved/range), records A + final SP/state/MPR.
; Fits 1..255 bytes. NOT a BIOS, NOT an ABI claim.
.setcpu "huc6280"
.segment "PAYLOAD"
.export payload_entry, payload_done

RESBASE = $3100
MARK = $3F00
RETVEC = $3F10
.ifndef SEQ2
.error "SEQ2 ($E006 entry) required"
.endif
.ifndef IDXHI
.error "IDXHI required"
.endif
.ifndef IDXMID
.error "IDXMID required"
.endif
.ifndef IDXLO
.error "IDXLO required"
.endif
.ifndef SELTRACK
.error "SELTRACK required"
.endif

payload_entry:
    cld
    lda #$00
    ldx #$07
@zst:
    sta $2400,x
    dex
    bpl @zst
    lda #$FF
    sta $2404
    lda #SELTRACK
    sta $20F8
    lda #$00
    sta $20F9
    sta $20FA
    sta $20FF
    lda #$80
    sta $20FB
    lda #IDXLO
    sta $20FC
    lda #IDXMID
    sta $20FD
    lda #IDXHI
    sta $20FE
    lda #$00
    sta $20F8
    jsr SEQ2
    sta RESBASE+4
    php
    pla
    sta RESBASE+8
    lda #SELTRACK
    sta $20F8
    lda #$00
    sta $20FB
    jsr SEQ2
    sta RESBASE+5
    lda #$80
    sta $20FB
    lda #$01
    sta $20F9
    jsr SEQ2
    sta RESBASE+6
    lda #$00
    sta $20F9
    sta $20FC
    sta $20FD
    lda #$20
    sta $20FE
    jsr SEQ2
    sta RESBASE+7
    tsx
    txa
    sta RESBASE+10
    ldx #$07
@cpst:
    lda $2400,x
    sta RESBASE+11,x
    dex
    bpl @cpst
    ldx #$07
@cpin:
    lda $20F8,x
    sta RESBASE+19,x
    dex
    bpl @cpin
    tma #$01
    sta RESBASE+27
    tma #$02
    sta RESBASE+28
    tma #$04
    sta RESBASE+29
    tma #$08
    sta RESBASE+30
    tma #$10
    sta RESBASE+31
    tma #$20
    sta RESBASE+32
    tma #$40
    sta RESBASE+33
    tma #$80
    sta RESBASE+34
    lda #$AA
    sta RESBASE
    lda #$A5
    sta MARK
    lda #$5A
    sta MARK+1
    tma #$02
    sta MARK+4
    tsx
    txa
    sta MARK+5
    lda #$3C
    sta MARK+6
payload_done:
    jmp (RETVEC)
