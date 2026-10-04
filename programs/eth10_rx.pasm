.program eth10_rx
; 10BASE-T receiver. G2 (tools/pe/g2.manchester_receiver), owned by this
; engine, steps every cycle on G2_IN0 = the RX pin and G2_IN1 = pin 15
; (NEGSEL = the RX pin) and emits decoded bits into the ISR.
; Config: autopush 16, in shifting left. G2 out1 (pin 14) is high while idle.
; Receive words: bits in arrival order, first in bit 15; at the end of a
; frame the last word ends with a 1 marker and zero padding, and flag 3 is
; set. The Host takes the words received up to that point as one frame and
; clears flag 3; it must do so before the next frame's first 16 bits arrive
; (interframe gap plus 1.6 us).
.wrap_target
    wait 0 gpio 14             ; locked onto a frame
    wait 1 gpio 14             ; carrier gone: end of frame
    mov osr, ~null
    in osr, 1                  ; end marker
    set x, 15
pad:
    in null, 1
    jmp x-- pad
    mov isr, null
    irq set 3
.wrap
