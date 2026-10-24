.program usb_ls_rx
; Receive side of programs/usb_ls_host.pasm. G2 emits decoded bits (with
; stuff bits) into this engine's ISR; when the host program sets flag 2 at
; the end of a reply, or at its deadline, this closes the record.
; Config: autopush 15, in shifting left; CLKDIV 7 (G2 steps on this
;   engine's ticks: 4 per bit at 48 MHz).
; Receive words: count-terminated records (tools/pe/proto/records.py), one
;   per reply: bits in arrival order, 15 per word. A record without a SYNC
;   means no reply arrived before the deadline.
.wrap_target
    wait 1 flag 2
    mov x, isrcnt              ; bits in the partial last word
    push
    mov isr, ~null
    in x, 15                   ; trailer 0x8000 | count, autopushed
.wrap
