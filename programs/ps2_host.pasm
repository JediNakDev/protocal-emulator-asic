.program ps2_host
; PS/2 host (computer side): the device generates the clock.
; Pins (both open-drain with pull-ups): DATA = SETPIN/OUTPIN/INPIN base,
; CLK = side-set pin, jmp pin and pin CLK below.
; STATUSCFG: N = 1, transmit queue.
; Config: `out` and `in` shifting right; CLKDIV 199 at 50 MHz: 4 us ticks.
; Transmit words: a command, 10 bits LSB first: 8 data bits, odd parity, stop 1.
; Receive words: a device frame, 10 bits in bits 15:6 (shift right):
;   8 data bits, parity, stop; or, after a command, the acknowledge bit
;   alone in bit 15 (0 = acknowledged); or 0xFFFF, a failure: the device
;   did not start clocking within 4096 ticks (16.4 ms; it must within 15 ms)
;   of a request to send, or a clock phase of a frame or command lasted
;   more than 31 ticks (124 us; the limit is 50 us). The lines are released
;   and, once CLK is high, the program waits for the next frame or command.
; Inhibit: CLK low for 27 ticks (108 us; at least 100 us is required) before
;   DATA falls, and released one tick later.
.define CLK 3
.side_set 1 opt
.wrap_target
idle:
    mov x, status              ; all ones when there is nothing to send
    jmp !x send
    jmp pin idle               ; CLK high: the device is quiet
    set y, 9                   ; CLK fell: this clock carries the start bit
rbit:
    set x, 31
    wait 1 gpio CLK timeout
    jmp !x fail
    set x, 31
    wait 0 gpio CLK timeout
    jmp !x fail
    in pins, 1                 ; DATA after each falling edge
    jmp y-- rbit
    push
    set x, 31
    wait 1 gpio CLK timeout    ; the end of the stop bit's clock
    jmp idle
send:
    pull block
    set y, 25          side 0  ; inhibit: hold CLK low
inhibit:
    jmp y-- inhibit
    set pins, 0                ; request to send: DATA low
    set x, 8           side 1  ; release CLK
    mov x, ::x                 ; 4096 checks for the device's first clock
    wait 0 gpio CLK timeout
    jmp !x release
    set y, 9                   ; 8 data + parity + stop
tbit:
    out pins, 1                ; change DATA while CLK is low
    set x, 31
    wait 1 gpio CLK timeout
    jmp !x release
    set x, 31
    wait 0 gpio CLK timeout
    jmp !x release
    jmp y-- tbit
    in pins, 1                 ; 11th clock: the device acknowledges
    set x, 31
    wait 1 gpio CLK timeout
    push
.wrap
release:
    set pins, 1        side 1  ; release both lines
fail:
    mov isr, ~null             ; failure word 0xFFFF
    push
    wait 1 gpio CLK            ; one failure per stuck-low CLK
    jmp idle
