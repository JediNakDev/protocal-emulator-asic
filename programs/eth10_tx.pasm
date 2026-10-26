.program eth10_tx
; 10BASE-T transmitter: Manchester at 5 cycles per bit (10 Mbit/s at 50 MHz),
; with normal link pulses while idle.
; The Host builds the frame with preamble, SFD and FCS (tools/pe/proto/eth.py).
; Transmit words: a command word = number of bit pairs - 1, then the frame
;   bits, first bit in bit 0 (shift right). Frames are whole bytes, so the
;   bit count is even.
; Pins: side-set = TD+ (bit 0), TD- (bit 1) and S (bit 2), 3 pins.
; Config: EXECCFG jump base = address of `table`; autopull 16, out shifting
;   right; CLKDIV 0; STATUSCFG N = 1 on the transmit queue (status is all
;   ones while it is empty); NEGOUT = S, NEGMASK = TD+ and TD-; S drive off.
; IEEE 802.3: a 0 is high then low on TD+, a 1 is low then high.
; Half a bit is 2.5 cycles. Bit boundaries come from the side-set values of
; TD+ and TD-; mid-bit transitions come from S, which the pin block samples
; on the falling edge and XORs into TD+ and TD- half a cycle later. S rises
; in the middle of the first bit of each pair and falls in the middle of the
; second, so the program handles the bits in pairs (`out pc, 2`, 10 cycles).
; An instruction issued in cycle c drives TD+ and TD- from c + 1 and the S
; phase from c + 1.5.
; After the last bit TD+ stays high for 15 cycles (TP_IDL, 300 ns), then both
; pins go low. The transmit queue must not run dry inside a frame.
; Idle: a frame starts within 10 cycles of its command word arriving. After
; 65,536 polls without one (13.1 ms at 50 MHz, inside 802.3's 8-24 ms), a
; normal link pulse drives TD+ high and TD- low for 5 cycles (100 ns).
.side_set 3
.wrap_target
idle:
    mov y, ~null      side 0
poll:
    mov x, status     side 0 [3]   ; all ones while nothing is queued
    jmp !x start      side 0 [3]
    jmp y-- poll      side 0 [1]   ; 10 cycles per poll
    nop               side 1 [3]   ; normal link pulse
    jmp idle          side 1
start:
    pull block        side 0
    mov x, osr        side 0
    out null, 16      side 0
    out pc, 2         side 0   ; first pair
table:                         ; first half of the first bit of a pair
    jmp h00           side 1 [1]   ; 0 then 0
    jmp h10           side 2 [1]   ; 1 then 0
    jmp h01           side 1 [1]   ; 0 then 1
    jmp h11           side 2 [1]   ; 1 then 1
h00:                           ; S rises: second half of the first bit
    jmp o0            side 5 [2]
h10:
    jmp o0            side 6 [2]
h01:
    jmp o1            side 5 [2]
h11:
    jmp o1            side 6 [2]
o0:                            ; second bit 0; S falls at its middle
    nop               side 6 [1]
    jmp x-- d0        side 2
    jmp tp_idl        side 2 [1]
d0:
    out pc, 2         side 2 [1]
o1:                            ; second bit 1
    nop               side 5 [1]
    jmp x-- d1        side 1
    jmp tp_idl        side 1 [1]
d1:
    out pc, 2         side 1 [1]
tp_idl:
    nop               side 1 [3]
    nop               side 1 [3]
    nop               side 1 [3]
    nop               side 1 [2]
.wrap
