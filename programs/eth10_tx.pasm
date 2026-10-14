.program eth10_tx
; 10BASE-T transmitter: Manchester at 4 cycles per bit (10 Mbit/s at 40 MHz),
; with normal link pulses while idle.
; The Host builds the frame with preamble, SFD and FCS (tools/pe/proto/eth.py).
; Transmit words: a command word = number of bits - 1, then the frame bits,
;   first bit in bit 0 (shift right).
; Pins: side-set = TD+ (bit 0) and TD- (bit 1), 2 pins.
; Config: EXECCFG jump base = address of `table`; autopull 16, out shifting
;   right; CLKDIV 0; STATUSCFG N = 1 on the transmit queue (status is all
;   ones while it is empty).
; IEEE 802.3: a 0 is high then low on TD+, a 1 is low then high. After the
; last bit TD+ stays high for 12 cycles (TP_IDL), then both pins go low.
; The transmit queue must not run dry inside a frame.
; Idle: a frame starts within 10 cycles of its command word arriving. After
; 65,536 polls without one (16.4 ms at 40 MHz, inside 802.3's 8-24 ms), a
; normal link pulse drives TD+ high and TD- low for 4 cycles (100 ns).
.side_set 2
.wrap_target
idle:
    mov y, ~null      side 0
poll:
    mov x, status     side 0       ; all ones while nothing is queued
    jmp !x start      side 0
    jmp y-- poll      side 0 [7]   ; 10 cycles per poll
    nop               side 1 [3]   ; normal link pulse
    jmp idle          side 0
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
