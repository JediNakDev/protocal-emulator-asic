; I2C controller writes with START/STOP and clock stretching.
; SDA = OUTPIN, SETPIN and INPIN base; SCL = SDA + 1 and the side-set pin.
; Configure both lines open-drain, initially released, and shift left.
.program i2c_write
; Word from the Host: bit 15 START before the byte, bits 14:7 the byte,
; bit 6 STOP after it. One receive word per byte: bit 0 is the ACK level.
.side_set 1 opt
.wrap_target
next:
    pull block
    out x, 1
    jmp !x data
    set pins, 1        [3]   ; release SDA
    nop         side 1 [3]   ; release SCL
    wait 1 pin 1       [3]   ; SCL high, after any stretching
    set pins, 0        [3]   ; START
    nop         side 0 [3]
data:
    set y, 7
bit:
    out pins, 1        [3]   ; SDA changes only while SCL is low
    nop         side 1 [3]
    wait 1 pin 1       [3]
    jmp y-- bit side 0 [3]
    set pins, 1        [3]   ; release SDA for the ACK
    nop         side 1 [3]
    wait 1 pin 1       [3]
    in pins, 1
    push block  side 0 [3]
    out x, 1
    jmp !x next
    set pins, 0        [3]
    nop         side 1 [3]
    wait 1 pin 1       [3]
    set pins, 1        [7]   ; STOP
.wrap
