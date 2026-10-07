; I2C controller, open-drain emulated with output enables, clock stretching.
; T0 = SCL low time, T1 = SCL high time. The timer re-phases when SCL is seen
; rising, so a target stretching SCL delays the high phase instead of eating it.
; 400 kHz at 40 MHz: T0 = 60, T1 = 36 -> 1.50 us low, 1.00 us high.
;
; Host command byte: [7] ack bit to send, [6:5] op
;   op 0 START (or repeated START), op 1 STOP,
;   op 2 XFER: next FIFO byte is sent (0xFF to read). Returns two bytes:
;        the byte seen on SDA, then the ACK bit seen (0 = ACK).
.program i2c_master
.pin sda 0 od
.pin scl 1 od
.side_set scl
.jmp_pin scl
.resync rise
.out sda
.in sda
.shift out left
.shift in left
.autopush
    jmp start                       ; op 0
    jmp stop                        ; op 1
    jmp xfer                        ; op 2
    jmp xfer                        ; op 3 (alias)
.entry
    set pins, 0b01       side 1     ; release SDA and SCL
    set pindirs, 0b11
.wrap_target
top:
    pull
    out y, 1                        ; ack bit for XFER
    out pc, 2                       ; dispatch
start:
    set pin sda, 1       [tick]     ; release SDA while SCL is low (or idle)
    wait 1 pin scl side 1 [tick] ; SCL high, tSU;STA
    set pin sda, 0       [tick]     ; START, tHD;STA
    jmp top              side 0 [3]
xfer:
    pull
    set x, 7
bit:
    out pins, 1          [tick]     ; SDA = bit, rest of SCL low time
    wait 1 pin scl side 1 [tick] ; release SCL, honour stretching
    in pins, 1           side 0 [3] ; sample, SCL low, hold
    jmp x-- bit
    mov pins, y          [tick]     ; ACK/NACK (1 releases SDA for the target)
    wait 1 pin scl side 1 [tick]
    in pins, 1           side 0 [3]
    push
    jmp top
stop:
    set pin sda, 0       [tick]
    wait 1 pin scl side 1 [tick] ; tSU;STO
    set pin sda, 1       [tick]     ; STOP, then tBUF
.wrap
