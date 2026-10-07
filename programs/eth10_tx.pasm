.program eth10_tx
; 10BASE-T transmitter: Manchester at 4 cycles per bit (10 Mbit/s at 40 MHz).
; The Host builds the frame with preamble, SFD and FCS (tools/pe/proto/eth.py).
; Transmit words: a command word = number of bits - 1, then the frame bits,
;   first bit in bit 0 (shift right).
; Pins: side-set = TD+ (bit 0) and TD- (bit 1), 2 pins.
; Config: EXECCFG jump base = address of `table`; autopull 16, out shifting
;   right; CLKDIV 0.
; IEEE 802.3: a 0 is high then low on TD+, a 1 is low then high. After the
; last bit TD+ stays high for 12 cycles (TP_IDL), then both pins go low.
; The transmit queue must not run dry inside a frame.
.side_set 2
.wrap_target
start:
    pull block        side 0
    mov x, osr        side 0
    out null, 16      side 0
    out pc, 1         side 0   ; first bit
table:
    jmp h0            side 1 [1]   ; first half of a 0
    jmp h1            side 2 [1]   ; first half of a 1
h0:
    jmp x-- d0        side 2       ; second half of a 0
    jmp tp_idl        side 2
d0:
    out pc, 1         side 2
h1:
    jmp x-- d1        side 1       ; second half of a 1
    jmp tp_idl        side 1
d1:
    out pc, 1         side 1
tp_idl:
    nop               side 1 [7]
    nop               side 1 [3]
    nop               side 0
.wrap
