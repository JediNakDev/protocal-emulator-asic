.program can_rx
; CAN receiver (listen-only): records every frame on the bus, own frames included.
; G2: tools/pe/g2.can_destuffer table, owned by this engine, stepping on the
;   feed, emitting into the ISR; G2_IN0 = feed bit. G2_STATE starts at 0.
; Pins: INPIN base = RXD; jmp pin = 14 (G2 out1: six equal bits).
; Config: autopush 15, in shifting left, out shifting right; CLKDIV 0;
;   50 cycles per bit (1 Mbit/s at 50 MHz).
; Bit timing: the start-of-frame edge hard-synchronizes, and every later
;   recessive-to-dominant edge resynchronizes: a bit is sampled 36 cycles
;   (72%) after the edge, or 50 cycles after the previous sample when no
;   edge came. A timed wait looks for the edge from 3 to 32 cycles after
;   each sample (visible at the pin; it is dominant at 2 when the sampled bit
;   was), which follows a transmitter more than 2% fast or 3% slow across
;   the 10 bits without an edge that stuffing allows.
; Receive words: count-terminated records (tools/pe/proto/records.py), one
;   per frame: the destuffed bits, 15 per word, first bit in bit 14.
; Start at the first instruction: it loads Y.
.define RXD 8
    set y, 31                  ; the resynchronization window, in checks
.wrap_target
idle:
    wait 0 gpio RXD            ; start of frame: hard synchronization
    set x, 31          [2]
    nop                [31]    ; to the sample point
bit:
    mov osr, pins              ; sample point (t = 0): RXD into OSR bit 0
    out g2, 1                  ; the destuffer steps; data bits enter the ISR
    wait 0 gpio RXD timeout    ; t = 2..32: dominant, or an edge to it
    jmp x!=y moved
    jmp pin done               ; dominant at t = 2: no edge to follow
    set x, 31          [31]
    jmp bit            [12]    ; next sample at t = 50
moved:
    jmp pin done
    jmp !x late
    set x, 31          [30]    ; an edge at t = w
    jmp bit                    ; next sample at t = w + 36
late:
    set x, 31          [12]    ; no edge
    jmp bit                    ; next sample at t = 50
done:                          ; six equal bits: end of frame or error
    mov x, isrcnt              ; bits in the partial last word
    push
    mov isr, ~null
    in x, 15                   ; trailer 0x8000 | count, autopushed
.wrap
