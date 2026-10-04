.program can_rx
; CAN receiver (listen-only): records every frame on the bus, own frames included.
; G2: tools/pe/g2.can_destuffer table, owned by this engine, stepping on the
;   feed, emitting into the ISR; G2_IN0 = feed bit. G2_STATE starts at 0.
; Pins: INPIN base = RXD; jmp pin = 14 (G2 out1: six equal bits).
; Config: autopush 15, in shifting left, out shifting right; CLKDIV 0;
;   50 cycles per bit (1 Mbit/s at 50 MHz). Bits are sampled 36 cycles (72%) after the
;   start-of-frame edge; there is no resynchronization within a frame.
; Receive words: 15 destuffed bits per word, first bit in bit 14, bit 15 zero.
;   The last data word ends with a 1 marker and zero padding; then 0xFFFF
;   separates frames. The reserved top bit keeps every payload distinct.
.define RXD 8
.wrap_target
idle:
    wait 0 gpio RXD            ; start of frame: hard synchronization
    nop               [2]
    nop               [31]     ; to the sample point
bit:
    mov osr, pins              ; RXD into OSR bit 0
    out g2, 1                  ; the destuffer steps; data bits enter the ISR
    nop               [1]
    jmp pin done               ; six equal bits: end of frame or error
    nop               [12]
    jmp bit           [31]     ; 50 cycles per bit
done:
    mov osr, ~null
    in osr, 1                  ; end marker
    set x, 14
pad:
    in null, 1                 ; flush the marker with autopush
    jmp x-- pad
    mov isr, ~null
    push                       ; frame separator
.wrap
