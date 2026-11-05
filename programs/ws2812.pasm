.program ws2812
; WS2812B transmitter: one data line, no clock, one bit per 1.26 us (793.7
; kHz, -0.79% from 800 kHz), the code of each bit in its high time.
; Pins: DIN = side-set pin, push-pull. An output-only pin (P10-P12) idles low
;   from reset, so the strip sees no glitch before configuration.
; Config: `out` shifting left, autopull at 16, transmit-only buffer (8 words).
;   CLKDIV 2 at 50 MHz: 60 ns ticks.
; Transmit words: first the frame length in bits, less one (24 per pixel, at
;   most 65,536 bits); then the bits, packed into 16-bit words, first bit in
;   bit 15: G7..G0 R7..R0 B7..B0 for the first pixel, then the next. Unused
;   bits of the last word are discarded.
; Timing, in ticks: 0 code 7 high and 14 low, 1 code 14 high and 7 low, 21 per
;   bit, so 420/840 ns and 840/420 ns. After the last bit DIN stays low 369 us
;   (6,150 ticks), which latches the pixels; the next frame then starts when
;   its first word arrives.
; A transmit queue that runs dry inside a frame holds DIN low until the next
;   word comes. The queue covers 160 us; a pause of 50 us or more on the wire
;   ends the frame early at the pixels.
.side_set 1
.wrap_target
    pull block       side 0      ; idle low; drops the rest of the last word
    out y, 16        side 0      ; frame length
bit:
    out x, 1         side 0 [5]  ; low tail of the previous bit, 7 with `tail`
    jmp !x zero      side 1 [6]  ; high 7 ticks, for either code
    jmp tail         side 1 [6]  ; 1 code: high 7 more
zero:
    nop              side 0 [6]  ; 0 code: low 7 more
tail:
    jmp y-- bit      side 0
    set x, 16        side 0
    mov x, ::x       side 0      ; 2048
gap:
    jmp x-- gap      side 0 [2]  ; 3 ticks each: the reset gap
.wrap
