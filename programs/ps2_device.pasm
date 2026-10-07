.program ps2_device
; PS/2 device (keyboard or mouse side): generates the clock in both directions.
; Pins (both open-drain with pull-ups): DATA = SETPIN/OUTPIN/INPIN base and
; jmp pin, CLK = side-set pin and pin CLK below.
; STATUSCFG: N = 1, transmit queue (status is all ones when it is empty).
; Transmit words: an 11-bit frame, LSB first: start 0, 8 data bits,
;   odd parity, stop 1 (tools/pe/proto/ps2.py builds it).
; Receive words: a host command, 10 bits in bits 15:6 (shift right):
;   8 data bits, parity, stop.
; Timing: CLK low 8 ticks, high 8-9 ticks. Set CLKDIV for 10-16.7 kHz:
;   about 249 at 50 MHz.
.define CLK 3
.side_set 1 opt
.wrap_target
idle:
    jmp pin check_tx           ; DATA high: the host is not requesting to send
    wait 1 gpio CLK            ; request: wait until the host releases CLK
    set x, 9           [7]     ; 8 data + parity + stop
rbit:
    nop          side 0 [7]    ; CLK low: the host changes DATA
    nop          side 1 [3]
    in pins, 1         [3]     ; read DATA while CLK is high
    jmp x-- rbit
    set pins, 0        [3]     ; acknowledge: DATA low for one more clock
    nop          side 0 [7]
    nop          side 1 [3]
    set pins, 1        [3]
    push
    jmp idle
check_tx:
    mov x, status              ; all ones when there is nothing to send
    jmp !x send
    jmp idle
send:
    wait 1 gpio CLK            ; the host is not inhibiting
    pull block
    set x, 10
sbit:
    out pins, 1        [3]     ; DATA changes while CLK is high
    nop          side 0 [7]    ; CLK low: the host samples DATA
    jmp x-- sbit side 1 [3]
.wrap
