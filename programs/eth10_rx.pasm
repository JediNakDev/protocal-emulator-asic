.program eth10_rx
; 10BASE-T receiver. G2 (tools/pe/g2.manchester_receiver), owned by this
; engine, steps every cycle on G2_IN0 = the RX pin and G2_IN1 = pin 15
; (NEGSEL = the RX pin) and emits decoded bits into the ISR.
; Config: autopush 15, in shifting left. G2 out1 (pin 14) is high while idle.
; Receive words: count-terminated records (tools/pe/proto/records.py), one
; per carrier burst: 15 bits per word in arrival order, the first in bit 14;
; the partial last word; then the trailer 0x8000 | its bit count. The Host
; finds frame boundaries in the stream whenever it reads it. A link pulse
; makes a record of a few bits without an SFD.
; Flag 3 is set after each record, for a Host that wants an interrupt.
.wrap_target
    wait 0 gpio 14             ; locked onto a frame
    wait 1 gpio 14             ; carrier gone: end of frame
    mov x, isrcnt              ; bits in the partial last word
    push                       ; the partial word, possibly empty
    mov isr, ~null
    in x, 15                   ; trailer 0x8000 | count, autopushed
    irq set 3
.wrap
