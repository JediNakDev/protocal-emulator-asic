.program usb_ls_rx
; Receive side of programs/usb_ls_host.pasm. G2 emits decoded bits (with
; stuff bits) into this engine's ISR; when the host program sets flag 2 at
; the end of a packet, this closes the record.
; Config: autopush 16, in shifting left; CLKDIV 7 (G2 steps on this
;   engine's ticks: 4 per bit at 48 MHz).
; Receive words: bits in arrival order, first in bit 15; the last data word
;   ends with a 1 marker and zero padding; 0xFFFF separates packets.
.wrap_target
    wait 1 flag 2
    mov osr, ~null
    in osr, 1                  ; end marker
    set x, 15
pad:
    in null, 1
    jmp x-- pad
    mov isr, ~null
    push                       ; packet separator
.wrap
