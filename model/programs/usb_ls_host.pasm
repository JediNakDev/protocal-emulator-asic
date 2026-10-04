; USB low-speed (1.5 Mb/s) host, half duplex on one engine.
; The RP2040 does the coding: it sends line states (after NRZI and bit
; stuffing, 1 = K, 0 = J), padded at the front with J to whole bytes, and it
; decodes what comes back. The engine does the timing: bit clock, EOP, bus
; turnaround, and mid-bit sampling that re-phases on every edge of D+.
;
; T0 = clk / 1.5 MHz (27 at 40 MHz, -1.2%), T1 = T0/2 - 1.
; Host packet: [line bits-1, high 7 bits << 1 | listen] [low 8 bits] [bits...]
; With listen = 1 the engine then receives one packet and pushes its D+
; samples (LSB first), then a 0 for the SE0 sample, a 1 marker, and zeros to
; the next byte boundary: the reply ends at the last 1 in the stream.
.program usb_ls_host
.pin dp 0
.pin dm 1
.out dp diff                    ; D- always mirrors D+ except during SE0
.in dp
.jmp_pin dp
.resync both                    ; only while the engine is not driving D+
.shift out right
.shift in right
.autopull
.autopush
.wrap_target
tx:
    set y, 0
    out y, 1                    ; listen for a reply after this packet?
    set x, 0
    out x, 7
    out x, 8                    ; x = line bits - 1
    set pins, 0b10              ; J
    set pindirs, 0b11 [tick]    ; drive J for one bit
txbit:
    out pins, 1                 ; 1 = K, 0 = J
    jmp x-- txbit     [tick]
    set pins, 0b00    [tick]    ; EOP: SE0 for two bit times
    nop               [tick]
    set pins, 0b10    [tick]    ; J for one bit
    set pindirs, 0b00           ; release: pull resistors hold J
    jmp !y tx
    wait 1 pin dp     [tick]    ; first K of SYNC; tick lands mid-bit
rxbit:
    in pins, 1                  ; D+
    mov x, pins                 ; D+ and D-
    jmp !x eop                  ; SE0
    jmp rxbit         [tick]
eop:
    set x, 1
    in x, 1                     ; end marker
    set x, 6
pad:
    in null, 1                  ; zeros up to the byte boundary (autopush)
    jmp x-- pad
    push
    wait 1 pin dm               ; back to J
.wrap
