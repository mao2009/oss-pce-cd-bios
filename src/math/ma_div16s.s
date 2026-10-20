; MA_DIV16S (project-internal, not in the jump table, reference ABI UNVERIFIED): signed 16-bit
; division with remainder (Issue #76, candidate slot $42).
;
; Original OSS implementation written from this project's OWN contract (tools/ma_div16s_model.py),
; not from proprietary BIOS code. The reference BIOS MA_DIV16S calling convention is UNVERIFIED;
; this register/stack ABI is a project definition only. NOT connected to the API jump table.
;
; In:  X:A = dividend (signed 16-bit; X high, A low).
;      Caller pushes the divisor before JSR: divisor low first, then divisor high (high on top).
; Out: X:A = quotient, signed, truncated toward zero (X high, A low).
;      The two divisor stack bytes are overwritten with the remainder: the top byte (pulled
;      first) is the remainder high, the byte below it the remainder low. Remainder sign = dividend sign.
;      C = 1: divisor was zero. Quotient = 0, remainder = dividend (project definition).
;      V = 1: overflow, dividend -32768 / divisor -1 = +32768 is not representable. X:A = $8000.
;      N, Z: reflect the returned quotient.
;      Y, I, D and T are preserved. B is ignored by PLP.
; Stack: 7 bytes below the caller's frame plus the JSR return address. No zero page, no RAM.
; Precondition: MPR1 maps the stack page at logical $2100 (RAM bank $F8).
.setcpu "huc6280"
.segment "MATHLIB"
.export ma_div16s
ma_div16s:
    php
    cld
    pha                  ; dividend low  (becomes quotient low)
    phx                  ; dividend high (becomes quotient high)
    lda #$00
    pha                  ; flags: $01 C (div0), $02 quotient negative, $04 dividend negative, $40 V
    lda #16
    pha                  ; restoring-division loop counter
    lda #$00
    pha                  ; remainder high
    pha                  ; remainder low
    tsx                  ; $2101 R lo, $2102 R hi, $2103 count, $2104 flags, $2105 N hi,
                         ; $2106 N lo, $2107 P, $2108/$2109 return, $210A divisor hi, $210B divisor lo
    lda $210A,x
    ora $210B,x
    bne @divide
    lda $2105,x          ; divide by zero: remainder := dividend, quotient := 0, C := 1
    sta $210A,x
    lda $2106,x
    sta $210B,x
    stz $2105,x
    stz $2106,x
    lda $2104,x
    ora #$01
    sta $2104,x
    jmp @finish
@divide:
    lda $2105,x          ; |dividend| (remember its sign)
    bpl @dividend_pos
    lda $2106,x
    eor #$FF
    clc
    adc #1
    sta $2106,x
    lda $2105,x
    eor #$FF
    adc #0
    sta $2105,x
    lda $2104,x
    ora #$04
    eor #$02             ; quotient sign = dividend sign XOR divisor sign
    sta $2104,x
@dividend_pos:
    lda $210A,x          ; |divisor|
    bpl @divisor_pos
    lda $210B,x
    eor #$FF
    clc
    adc #1
    sta $210B,x
    lda $210A,x
    eor #$FF
    adc #0
    sta $210A,x
    lda $2104,x
    eor #$02
    sta $2104,x
@divisor_pos:
@loop:                   ; restoring division: (R:N) <<= 1; if R >= D then R -= D, N |= 1
    asl $2106,x
    rol $2105,x          ; C = msb of the dividend, shifted out
    rol $2101,x
    rol $2102,x
    lda $2102,x
    cmp $210A,x
    bcc @no_sub
    bne @sub
    lda $2101,x
    cmp $210B,x
    bcc @no_sub
@sub:
    sec
    lda $2101,x
    sbc $210B,x
    sta $2101,x
    lda $2102,x
    sbc $210A,x
    sta $2102,x
    lda $2106,x
    ora #$01
    sta $2106,x
@no_sub:
    dec $2103,x
    bne @loop
    lda $2104,x          ; quotient: negate when its sign flag is set
    and #$02
    beq @qpos
    lda $2106,x
    eor #$FF
    clc
    adc #1
    sta $2106,x
    lda $2105,x
    eor #$FF
    adc #0
    sta $2105,x
    bra @qdone
@qpos:
    lda $2105,x          ; +32768 only arises from -32768 / -1
    cmp #$80
    bne @qdone
    lda $2106,x
    bne @qdone
    lda $2104,x
    ora #$40
    sta $2104,x
@qdone:
    lda $2104,x          ; remainder: negate when the dividend was negative
    and #$04
    beq @rpos
    lda $2101,x
    eor #$FF
    clc
    adc #1
    sta $2101,x
    lda $2102,x
    eor #$FF
    adc #0
    sta $2102,x
@rpos:
    lda $2101,x          ; store remainder into the caller's divisor slots
    sta $210B,x
    lda $2102,x
    sta $210A,x
@finish:
    lda $2107,x          ; P := caller I/D/T kept, then C/V from flags, then N/Z from quotient
    and #$2C
    sta $2107,x
    lda $2104,x
    and #$41
    ora $2107,x
    sta $2107,x
    lda $2105,x
    and #$80
    ora $2107,x
    sta $2107,x
    lda $2105,x
    ora $2106,x
    bne @nz
    lda $2107,x
    ora #$02
    sta $2107,x
@nz:
    pla                  ; discard remainder low, remainder high, count, flags
    pla
    pla
    pla
    pla                  ; quotient high
    tax
    pla                  ; quotient low
    plp
    rts
