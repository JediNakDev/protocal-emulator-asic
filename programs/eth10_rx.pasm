.program eth10_rx
; 10BASE-T receiver. Clock recovery (G7) locks onto the mid-bit transitions
; of the RX pin, sampled twice per cycle (NEGSEL = the RX pin), and strobes
; the second half of each bit cell (tools/pe/proto/eth.RX_CLOCK_RECOVERY).
; G2 (tools/pe/g2.recovered_bits), owned by this engine, steps on each strobe
; and emits the recovered bits into the ISR.
; Config: autopush 15, in shifting left. G2 out1 (pin 14) is high while the
; clock recovery is not locked: before a frame and after its last bit.
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
