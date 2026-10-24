; MA_SQRT — slot $44, candidate entry $E0CC. DEV ROM only (Issue #78).
;
; Project-own routine, NOT the reference System Card MA_SQRT ABI (remains UNVERIFIED).
; Integer square root by the bit-by-bit restoring method:
;   rem = 0; root = 0
;   repeat 8 times:
;       pair = top2bits(n);  n <<= 2
;       rem = (rem << 2) | pair
;       trial = (root << 2) + 1   (= 4*root + 1, fits in 9 bits)
;       if rem >= trial: rem -= trial; root = (root << 1) | 1
;       else: root = root << 1
;   return root
;
;   in : X:A = input (unsigned 16-bit; X high, A low); every value 0..65535 is valid
;   out: X:A = floor(sqrt(input)) (unsigned; X = 0, A = root 0..255)
;   kept: Y and P (caller's flags restored by PLP; B is not a stored flag)
;   clobbered: A and X (the result)
;   uses: zero page $F7-$FF only. No I/O, no MPR access, I is never changed.
;   precondition: S at routine entry >= $04 (JSR pushes 2 return bytes; php+phy
;                 push 2 more; minimum SP during routine is entry_SP - 4).
;   ABI remains UNVERIFIED and this ROM stays DEV ONLY (release_eligible: false).
;
; Zero-page layout:
;   $F7  trial_lo   trial = 4*root+1, low byte
;   $F8  trial_hi   trial = 4*root+1, high byte (0 or 1)
;   $FA  bits       loop counter (8 iterations)
;   $FB  root       current root (built bit by bit)
;   $FC  rem_lo     remainder low byte
;   $FD  rem_hi     remainder high byte
;   $FE  n_hi       input high byte
;   $FF  n_lo       input low byte
;
; Stack usage:
;   php pushes P (1 byte), phy pushes Y (1 byte). These are popped by plp/ply
;   before rts. No other stack operations. JSR return address (2 bytes) is pushed
;   by the caller and popped by rts. Maximum stack depth during routine is
;   entry_SP - 4.
;
; 16-bit overflow safety:
;   trial = 4*root + 1 <= 509 (root <= 127), fits in 9 bits.
;   rem < 4^8 = 65536 always, fits in 16 bits.
;
;   Zero Page $00-$07 is deliberately NOT used: adt_dma_routine.s clobbers
;   ZP $00-$0B. $F7-$FF is used instead.
;
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_44
api_slot_44:
    php                 ; save caller's P
    cld                 ; binary arithmetic
    phy                 ; save caller's Y
    sta $FF             ; n_lo = A
    txa                 ; A = n_hi
    sta $FE             ; n_hi = X
    lda #0
    sta $FD             ; rem_hi = 0
    sta $FC             ; rem_lo = 0
    sta $FB             ; root = 0
    sta $F8             ; trial_hi = 0
    lda #1
    sta $F7             ; trial_lo = 1  (= 4*0 + 1)
    lda #8
    sta $FA             ; bits = 8
loop:
    ; Capture pair = top 2 bits of n (n_hi bit 7 = n bit 15, n_hi bit 6 = n bit 14)
    lda #0
    sta $F7             ; scratch = 0  (reuse trial_lo slot temporarily)
    lda $FE             ; A = n_hi
    asl a               ; C = n bit 15
    rol $F7             ; scratch = n bit 15
    asl a               ; C = n bit 14
    rol $F7             ; scratch = {n bit 15, n bit 14} = pair
    ;
    ; n <<= 2
    asl $FF             ; n_lo <<= 1
    rol $FE             ; n_hi <<= 1 | C
    asl $FF             ; n_lo <<= 1
    rol $FE             ; n_hi <<= 1 | C
    ;
    ; rem = (rem << 2) | pair
    asl $FC             ; rem_lo <<= 1, C = bit 7
    rol $FD             ; rem_hi <<= 1 | C
    asl $FC             ; rem_lo <<= 1, C = bit 7
    rol $FD             ; rem_hi <<= 1 | C
    ; rem_lo low 2 bits are 0, so OR pair into rem_lo:
    lda $FC             ; A = rem_lo
    ora $F7             ; A = rem_lo | pair
    sta $FC             ; rem_lo = result
    ;
    ; trial = (root << 2) + 1  (= 4*root + 1, 9-bit: trial_hi:trial_lo)
    lda $FB             ; A = root
    asl a               ; C = root bit 7
    asl a               ; C = root bit 6 = trial_hi
    ora #1              ; A = trial_lo = (root << 2) | 1
    sta $F7             ; trial_lo
    bcc trial_hi_zero   ; C = 0 => trial_hi = 0
    lda #1
    sta $F8             ; trial_hi = 1
    bra trial_hi_done
trial_hi_zero:
    lda #0
    sta $F8             ; trial_hi = 0
trial_hi_done:
    ;
    ; if rem >= trial: rem -= trial; root = (root << 1) | 1
    ; else: root = root << 1
    ; Compare rem (16-bit) vs trial (9-bit: trial_hi:trial_lo)
    lda $FD             ; rem_hi
    cmp $F8             ; rem_hi vs trial_hi
    bcc rem_lt          ; rem_hi < trial_hi => rem < trial
    bne rem_ge          ; rem_hi > trial_hi => rem >= trial
    ; rem_hi == trial_hi: compare low bytes
    lda $FC             ; rem_lo
    cmp $F7             ; rem_lo vs trial_lo
    bcc rem_lt          ; rem_lo < trial_lo => rem < trial
rem_ge:
    ; rem >= trial: rem -= trial (16-bit minus 9-bit)
    sec
    lda $FC             ; rem_lo
    sbc $F7             ; rem_lo -= trial_lo
    sta $FC             ; rem_lo
    lda $FD             ; rem_hi
    sbc $F8             ; rem_hi -= trial_hi + borrow
    sta $FD             ; rem_hi
    ; root = (root << 1) | 1
    lda $FB             ; root
    asl a               ; root <<= 1
    ora #1              ; root = (root << 1) | 1
    sta $FB             ; root
    bra next_bit
rem_lt:
    ; root = root << 1
    lda $FB             ; root
    asl a               ; root <<= 1
    sta $FB             ; root
next_bit:
    ; bits--
    dec $FA             ; bits--
    bne loop            ; if bits != 0, continue (8 iterations total)
    ;
    ; Return: A = root, X = 0
    lda $FB             ; A = root (floor(sqrt(n)))
    ldx #0              ; X = 0 (root fits in a byte)
    ply                 ; Y = caller's Y
    plp                 ; P = caller's P
    rts                 ; RTS pops JSR return address -> SP balanced to caller
