.program can_tx
; CAN transmitter with arbitration. The Host stuffs the frame and computes
; the CRC (tools/pe/proto/can.py); this program times the bits.
; Transmit words: 2-bit symbols, first in bits 1:0 (shift right), 8 per word:
;   0 dominant bit
;   1 recessive bit; if the bus is dominant at the sample point, arbitration
;     is lost (or a bit error happened): stop, set flag 0, wait for the Host
;   2 ACK slot: recessive, push the bus level (0 = acknowledged)
;   3 recessive bit without a check (delimiters, end of frame, padding)
; Pins: SETPIN base = TXD; INPIN base and jmp pin = RXD.
; Config: EXECCFG jump base = address of `table`; autopull 16, out shifting
;   right; CLKDIV 0; 50 cycles per bit.
; After losing, the Host clears the queue, restarts the engine, forces
; `jmp next` and clears flag 0.
next:
    out pc, 2
table:
    jmp dominant
    jmp recessive
    jmp ack_slot
    jmp quiet
dominant:
    set pins, 0       [31]
    jmp next          [15]
recessive:
    set pins, 1       [31]
    nop               [6]
    jmp pin next      [8]      ; sample point: bus recessive as sent
    irq wait 0                 ; lost arbitration or bit error
    jmp next
ack_slot:
    set pins, 1       [31]
    nop               [6]
    in pins, 1
    push              [6]
    jmp next
quiet:
    set pins, 1       [31]
    jmp next          [15]
