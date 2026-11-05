.program i2s
; I2S controller transmitter: stereo, 16 bits per channel, 32 bit clocks per
; frame, UM11732 format: LRCLK low for left, changing one bit clock before
; the MSB; SD changes on the falling edge of BCLK for the rising edge.
; Pins: BCLK = side-set base (bit 0), LRCLK = BCLK + 1 (bit 1), SD = OUTPIN
;   base. Any output drive; P10-P12 have no input and need no direction.
; Config: `out` shifting left, autopull at 16, transmit-only buffer (8 words).
;   CLKDIV 2 at 50 MHz: 60 ns ticks.
; Transmit words: samples, left then right, alternating; two's complement
;   as given, first bit in bit 15.
; Timing, in ticks: 5 low and 6 high, 11 per bit: BCLK 1.515 MHz, 47,348 Hz
;   frame rate at 50 MHz. CLKDIV moves it only by whole multiples of 11 cycles.
; LRCLK idles high and one bit clock runs before the first word, so that a
; receiver, which latches LRCLK on BCLK, sees the edge before the first left.
; A transmit queue that runs dry stalls BCLK low at a bit boundary, and the
; frame resumes in step with the next word.
.side_set 2
    pull block       side 2      ; idle: BCLK low, LRCLK high
    nop              side 3 [5]  ; a clock latches LRCLK high
    nop              side 0 [4]  ; LRCLK falls a bit before the left MSB
    set x, 14        side 1 [5]
.wrap_target
left:
    out pins, 1      side 0 [4]
    jmp x-- left     side 1 [5]
    out pins, 1      side 2 [4]  ; left LSB, LRCLK rises
    set x, 14        side 3 [5]
right:
    out pins, 1      side 2 [4]
    jmp x-- right    side 3 [5]
    out pins, 1      side 0 [4]  ; right LSB, LRCLK falls
    set x, 14        side 1 [5]
.wrap
