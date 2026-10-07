.program jtag
; JTAG controller (debugger side).
; Every TCK cycle takes a TDI/TMS pair from the transmit queue and returns
; the TDO bit sampled just before TCK rises.
;
; Pins: TDI = OUTPIN base, TMS = OUTPIN base + 1 (OUTPIN count 2),
;       TCK = side-set pin, TDO = INPIN base.
; Config: autopull and autopush at 16, both shifting right,
;         8 cycles per TCK (6.25 MHz at 50 MHz).
; Transmit words: bit 2i = TDI and bit 2i+1 = TMS of cycle i (8 cycles per word).
; Receive words: TDO of 16 cycles, the first in bit 0.
; TDI and TMS change on the falling edge of TCK; the target samples them
; on the rising edge and changes TDO on the falling edge.
.side_set 1
.wrap_target
    out pins, 2  side 0 [3]
    in pins, 1   side 1 [3]
.wrap
