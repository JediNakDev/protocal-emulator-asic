.program swd
; SWD host (probe side).
; Command words from the transmit queue (shift right):
;   bits 3:0  bit count - 1 (1-16 bits)
;   bit 4     1 = read, 0 = write
;   bit 5     1 = drive SWDIO during this command, 0 = release it
; A write command is followed by one data word, sent LSB first.
; A read command returns one receive word with its bits in the top bits,
; first bit lowest (shift right).
;
; Pins: SWDIO = OUTPIN/INPIN base (OUTPIN count 1), SWCLK = side-set pin.
; 8 cycles per SWCLK (6.25 MHz at 50 MHz).
; The host changes SWDIO while SWCLK is low; the target samples on the
; rising edge, drives its bits after a rising edge, and the host samples
; them just before the next rising edge.
.side_set 1 opt
.wrap_target
cmd:
    pull block        side 0
    out x, 4
    out y, 1
    out pindirs, 1
    jmp !y write
read:
    nop               side 0 [3]
    in pins, 1        side 1 [2]
    jmp x-- read      side 1
    push
    jmp cmd
write:
    pull block
wbit:
    out pins, 1       side 0 [3]
    jmp x-- wbit      side 1 [3]
.wrap
