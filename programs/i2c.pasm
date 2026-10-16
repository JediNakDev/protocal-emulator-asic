.program i2c
; I2C controller: START, repeated START, STOP, writes, reads and clock
; stretching, with UM10204 timing at 100 kHz (Standard-mode) or 400 kHz
; (Fast-mode).
; Pins: SDA = OUTPIN, SETPIN and INPIN base (counts 1); SCL = SDA + 1 and the
;   side-set pin. Both open-drain with pull-ups, released before enabling.
; Config: `out` and `in` shifting left; autopull and autopush off.
;   CLKDIV 19 (20-cycle ticks) at 50 MHz for 100 kHz, CLKDIV 4 for 400 kHz.
; Transmit words, one per byte:
;   bit 15     START (or repeated START) before the byte; required from idle
;   bits 14:6  the nine SDA bits of the byte, first bit highest: to write b,
;              b then 1 (released for the target's ACK); to read, 0xFF then
;              0 to acknowledge or 1 (NACK) for the last byte
;   bit 5      STOP after the byte
;   (tools/pe/proto/i2c.py builds them.)
; Receive words, one per byte: the nine SDA levels sampled while SCL was
;   high, first in bit 8: the byte written (or read) in bits 8:1 and the
;   ACK bit in bit 0.
; Timing, in ticks: SCL low 14 (SDA changes after 5), high 11 after the line
;   is seen high, so 25 per bit; START hold 11, repeated START setup 13,
;   STOP setup 11, bus free 16 after STOP. A target that stretches SCL
;   extends the low phase. Between bytes the controller holds SCL low while
;   the transmit queue is empty.
.side_set 1
.wrap_target
idle:                                  ; both lines released
    pull block            side 1
    out x, 1              side 1       ; START: implied from idle
    jmp start             side 1
held:                                  ; inside a transaction, SCL low
    pull block            side 0
    out x, 1              side 0
    jmp !x byte           side 0
    set pins, 1           side 0 [7]   ; repeated START: release SDA
    nop                   side 1       ; release SCL
    wait 1 pin 1          side 1 [12]  ; SCL high; setup time
start:
    set pins, 0           side 1 [10]  ; SDA falls while SCL is high; hold
byte:
    set x, 8              side 0
bit:
    nop                   side 0 [4]   ; hold SDA after SCL fell
    out pins, 1           side 0 [7]   ; SDA changes while SCL is low
    nop                   side 1       ; release SCL
    wait 1 pin 1          side 1       ; SCL high, after any stretching
    in pins, 1            side 1 [8]   ; sample SDA
    jmp x-- bit           side 0
    push                  side 0
    out x, 1              side 0       ; STOP after this byte?
    jmp !x held           side 0
    set pins, 0           side 0 [9]   ; SDA low while SCL is low
    nop                   side 1       ; release SCL
    wait 1 pin 1          side 1 [10]  ; SCL high; setup time
    set pins, 1           side 1 [15]  ; SDA rises while SCL is high: STOP
.wrap
