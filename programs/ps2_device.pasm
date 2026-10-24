.program ps2_device
; PS/2 device (keyboard or mouse side): generates the clock in both directions.
; Pins (both open-drain with pull-ups): DATA = SETPIN/OUTPIN/INPIN base,
; CLK = side-set pin, jmp pin and pin CLK below.
; STATUSCFG: N = 1, transmit queue (status is all ones when it is empty).
; Config: `out` and `in` shifting right; CLKDIV 249 at 50 MHz: 5 us ticks.
; Transmit words: an 11-bit frame, LSB first: start 0, 8 data bits,
;   odd parity, stop 1 (tools/pe/proto/ps2.py builds it).
; Receive words: a host command, 10 bits in bits 15:6 (shift right):
;   8 data bits, parity, stop; or 0xFFFF, a frame aborted because the host
;   held CLK low (inhibit) while the device released it. The Host sends
;   the frame again.
; Timing: CLK low 8 ticks (40 us) and high 8 ticks, 9 before the
;   acknowledge. Sending, DATA changes 6 ticks after CLK rises and 2 before
;   it falls; receiving, DATA is read 4 ticks after CLK rises. Before a
;   frame, CLK must have been high for 10 ticks (50 us).
.define DATA 4
.define CLK 3
.side_set 1 opt
.wrap_target
idle:
    jmp pin clk_high
    jmp idle                   ; CLK low: the host inhibits
clk_high:
    set x, 1
    wait 1 gpio DATA timeout   ; one check: is DATA high?
    jmp !x receive             ; DATA low with CLK high: request to send
    mov x, status              ; all ones when there is nothing to send
    jmp !x send
    jmp idle
send:
    set x, 10
    wait 0 gpio CLK timeout    ; CLK must stay high for 10 checks
    jmp !x go
    jmp idle
go:
    pull block
    set y, 10
sbit:
    out pins, 1        [1]     ; DATA changes while CLK is high
    nop          side 0 [7]    ; CLK low: the host samples DATA
    nop          side 1 [2]
    jmp pin high               ; CLK held low: the host inhibits
    set pins, 1                ; abort: release DATA
    mov isr, ~null
    push
    jmp idle
high:
    jmp y-- sbit       [1]
    jmp idle
receive:
    set y, 9           [7]     ; 8 data + parity + stop
rbit:
    nop          side 0 [7]    ; CLK low: the host changes DATA
    nop          side 1 [3]
    in pins, 1         [2]     ; read DATA while CLK is high
    jmp y-- rbit
    set pins, 0                ; acknowledge: DATA low for one more clock
    nop          side 0 [7]
    nop          side 1 [3]
    set pins, 1        [3]
    push
.wrap
