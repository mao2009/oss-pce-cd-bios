; Issue #862 L5 TEST-ONLY synthetic IPL payload (no commercial content).
; Byte-identical PRES behavior to tests/integration/ipl_payload.s, plus a
; staging report block written to RB spare ($2F34-$2F38) BEFORE the done
; marker: the $2502/$2503/$2504 cells and the entry MPR0/MPR1 as observed at
; IPLJMP. The loader under test maps MPR1=$F8 (work RAM), so the absolute
; reads below observe the loader-owned staging cells, i.e. the last body
; sector's bytes at BUF offsets $0302-$0304 (last-sector-wins).
; Assembled once per load address; PAY_TAG distinguishes variants.
; Never linked into any BIOS/ROM; synthetic harness only.
.setcpu "huc6280"
.segment "CODE"

.ifndef PAY_TAG
    .error "PAY_TAG is required"
.endif

PRES = $2F80
L5RPT = $2F34

payload_entry:
    lda #PAY_TAG
    sta PRES                 ; +0 tag: proves this payload ran
    lda $3FFF
    sta PRES+1               ; +1 byte at logical $3FFF (end of work RAM window)
    lda $4000
    sta PRES+2               ; +2 byte at logical $4000 (MPR2 boundary)
    lda $5FFF
    sta PRES+3               ; +3 byte at logical $5FFF
    lda $6000
    sta PRES+4               ; +4 byte at logical $6000 (MPR3 boundary)
    tma #$04
    sta PRES+5               ; +5 MPR2 bank as loaded
    lda $4000
    sta PRES+6               ; +6 $4000 before bank switch
    lda #$81                 ; switch MPR2 to bank $81, write at its offset $1F00 (outside every test load)
    tam #$04
    lda #$EE
    sta $5F00
    lda PRES+5
    tam #$04                 ; restore the loaded bank
    lda $4000
    sta PRES+7               ; +7 $4000 after the bank switch round trip
    ; +9/+10: 16-bit sum of the 8 KiB window $4000-$5FFF through the payload's own mapping (independent of the loader)
    lda #$00
    sta $02
    lda #$40
    sta $03
    lda #$00
    sta $04
    sta $05
    ldx #$20
    ldy #0
sumloop:
    lda ($02),y
    clc
    adc $04
    sta $04
    bcc sumnc
    inc $05
sumnc:
    iny
    bne sumloop
    inc $03
    dex
    bne sumloop
    lda $04
    sta PRES+9
    lda $05
    sta PRES+10
    ; +11..+14: MPR3..MPR6 as loaded (TMA readback, observed by the judge; the judge checks all of MPR2..6)
    tma #$08
    sta PRES+11
    tma #$10
    sta PRES+12
    tma #$20
    sta PRES+13
    tma #$40
    sta PRES+14
    ; Issue #862 L5 report (RB spare, outside PRES and the loader R_* block):
    ; shadow cells + entry MPRs at IPLJMP. No flag use afterwards, so the
    ; TMA flag effects are harmless (the done marker store follows).
    lda $2502
    sta L5RPT
    lda $2503
    sta L5RPT+1
    lda $2504
    sta L5RPT+2
    tma #$01
    sta L5RPT+3              ; entry MPR0 (loader reset set $FF, never remapped)
    tma #$02
    sta L5RPT+4              ; entry MPR1 (must be $F8 work RAM for the cells above)
    lda #$5A
    sta PRES+8               ; +8 done marker
payload_halt:
    bra payload_halt
