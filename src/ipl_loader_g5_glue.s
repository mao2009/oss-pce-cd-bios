; IPL loader G5 glue (Issue #800, parent #635).
; DEV-ONLY, synthetic media only, NOT a compatible System Card BIOS,
; NOT a BIOS ABI, NOT release eligible.
;
; Provides the symbol the experimental $02/$03 bodies need when they
; are linked into the PUBLIC IPL loader image (tools/build_ipl_loader_g5.py):
;   experimental_selection: 11-byte XSEL record (magic "XSEL", version 1,
;     format 1, kind/track/LBA) derived from the same synthetic INDEX 01 LBA
;     as the loader's IPL_SEL. Layout matches src/experimental_bios_boot.s
;     so src/cd_base_proto.s links unchanged (it only reads track+HI/MID/LO).
;     The PUBLIC loader itself validates the INFO sector via IPL_SEL; this
;     record exists ONLY for the proto's INDEX01 comparison, never as a guest
;     TOC. Mismatch between IPL_SEL and this record is refused by the builder.
; The experimental_slot_33 REAL EX_DSPOFF processing entry (Issue #61,
; project-own, DEV ONLY) is the NEED_CR33 head of src/ex_cr_family.s
; (single source assembled+linked by the builder). The normal DEV ROM keeps
; its nonreturning $33 stub.
;
; Linked into the BOOT segment (>= $F000) alongside src/cd_base_proto.s and
; src/cd_read_link.s. The PUBLIC loader code lives in CODE (also >= $F000).
; No vectors, no reset, no READ(6) here: reset/vectors come from
; tests/integration/ipl_loader_rom.s.
.setcpu "huc6280"
.segment "BOOT"
.export experimental_selection

; Host-derived defines, same names as src/experimental_bios_boot.s.
.ifndef EXP_SELECTED_KIND
    .error "host-derived EXP_SELECTED_KIND is required"
.endif
.ifndef EXP_SELECTED_TRACK
    .error "host-derived EXP_SELECTED_TRACK is required"
.endif
.ifndef EXP_SELECTED_LBA_HI
    .error "host-derived EXP_SELECTED_LBA_HI is required"
.endif
.ifndef EXP_SELECTED_LBA_MID
    .error "host-derived EXP_SELECTED_LBA_MID is required"
.endif
.ifndef EXP_SELECTED_LBA_LO
    .error "host-derived EXP_SELECTED_LBA_LO is required"
.endif

experimental_selection:
    .byte "XSEL", 1, 1, EXP_SELECTED_KIND, EXP_SELECTED_TRACK
    .byte EXP_SELECTED_LBA_HI, EXP_SELECTED_LBA_MID, EXP_SELECTED_LBA_LO

; Real EX_DSPOFF body (Issue #61, project-own, DEV ONLY): the NEED_CR33 head
; of src/ex_cr_family.s, assembled+linked by the builder (the
; experimental_slot_33 label itself is defined there).
