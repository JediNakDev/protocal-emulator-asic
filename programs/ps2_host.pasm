.program ps2_host
; PS/2 host (computer side): the device generates the clock.
; Pins (both open-drain with pull-ups): DATA = SETPIN/OUTPIN/INPIN base,
; CLK = side-set pin, jmp pin and pin CLK below.
; STATUSCFG: N = 1, transmit queue.
; Transmit words: a command, 10 bits LSB first: 8 data bits, odd parity, stop 1.
; Receive words: a device frame, 10 bits in bits 15:6 (shift right):
;   8 data bits, parity, stop; or, after a command, the acknowledge bit
;   alone in bit 15 (0 = acknowledged).
; The inhibit lasts 32 x 8 ticks; set CLKDIV so that is at least 100 us.
.define CLK 3
.side_set 1 opt
.wrap_target
idle:
    mov x, status              ; all ones when there is nothing to send
    jmp !x send
    jmp pin idle               ; CLK high: the device is quiet
    set x, 9                   ; CLK fell: this clock carries the start bit
rbit:
    wait 1 gpio CLK
    wait 0 gpio CLK
    in pins, 1                 ; DATA after each falling edge
    jmp x-- rbit
    push
    wait 1 gpio CLK
    jmp idle
send:
    pull block
    set y, 31          side 0  ; inhibit: hold CLK low
inhibit:
    jmp y-- inhibit    [7]
    set pins, 0                ; request to send: DATA low
    nop                side 1 [7]
    set x, 9                   ; 8 data + parity + stop
tbit:
    wait 0 gpio CLK            ; the device pulls CLK low
    out pins, 1                ; change DATA while CLK is low
    wait 1 gpio CLK
    jmp x-- tbit
    wait 0 gpio CLK            ; 11th clock: the device acknowledges
    in pins, 1
    wait 1 gpio CLK
    push
.wrap
