; EX_JOYSNS — slot $21 (33), DEV ROM only.
;
; DEV_EXPERIMENTAL project contract, NOT the reference System Card EX_JOYSNS ABI.
; Genuine ABI (inputs/outputs, work-RAM, multi-pad, trigger/delta state, timing, flags)
; is UNKNOWN/UNVERIFIED. release_eligible: false.
;
; DEV contract (real joypad I/O processing):
;   - Performs a pad-1 read cycle via I/O port $1000 with MPR0 mapped to $FF:
;       STA #$01 / $1000  (SEL=1), LDA $1000 -> direction nibble (low 4 bits)
;       STA #$00 / $1000  (SEL=0), LDA $1000 -> button nibble (low 4 bits)
;   - Returns A = (dir_nibble << 4) | (btn_nibble & $0F). Bits as read (active-low).
;   - X, Y preserved. MPR0 preserved. IRQs masked during I/O window (SEI/CLI via PLP).
;   - No VDC/work-RAM writes. Side effect: real I/O to $1000 (scan/SEL state).
;
; Stack: max 6 bytes below JSR target (php,phy,phx,pha,tma-pha,dir-temp) transient; balanced.
; Stack is $2100-$21FF (MPR1); when MPR0=$FF, absolute $01xx would hit I/O page, so use $21xx.
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_21
api_slot_21:
    php
    sei                 ; mask IRQs while MPR0 = I/O
    phy
    phx
    pha                 ; save caller A (discarded, result returned in A)
    tma #$01
    pha                 ; save caller MPR0
    lda #$FF
    tam #$01            ; MPR0 -> I/O page

    lda #$01
    sta a:$1000         ; SEL=1: directions
    lda a:$1000
    asl a
    asl a
    asl a
    asl a               ; dir nibble to high nibble
    pha                 ; temp: dir high nibble

    lda #$00
    sta a:$1000         ; SEL=0: buttons
    lda a:$1000
    and #$0F            ; button nibble
    tsx
    ora a:$2101,x       ; combine with stacked dir temp (stack page $21xx)
    tay                 ; result in Y

    pla                 ; drop dir temp
    pla                 ; saved MPR0
    tam #$01            ; restore MPR0
    pla                 ; discard saved caller A
    plx                 ; restore X
    tya                 ; result -> A
    ply                 ; restore Y
    plp                 ; restore caller flags/I state
    cmp #$00            ; set N/Z from result A (C/flags otherwise DEV-defined)
    rts
