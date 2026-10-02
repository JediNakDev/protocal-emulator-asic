; 10BASE-T transmitter (Manchester, 10 Mb/s) at 40 MHz: 2 cycles per half-bit.
; The RP2040 builds the frame (preamble, SFD, headers, payload, CRC-32 FCS).
; Host frame: [bits-1, high byte] [low byte] [frame bytes, LSB first on the wire]
; Between frames the engine sends a 100 ns link pulse about every 15 ms.
; T0 = 400 (10 us): two ticks of idle give the >= 9.6 us interframe gap.
.program eth10_tx
.pin tdp 0
.pin tdm 1
.out tdp diff                   ; TD- is always the inverse of TD+ when sending
.shift out right
.autopull
    set pins, 0b00
    set pindirs, 0b11           ; idle: both low (0 V differential)
.wrap_target
idle:
    set y, 2
outer:
    mov x, ~null                ; 65536 x 3 cycles = 4.9 ms
inner:
    jmp !osre frame
    pull noblock
    jmp x-- inner
    jmp y-- outer
    set pins, 0b01    [3]       ; link pulse: TD+ high for 100 ns
    set pins, 0b00
    jmp idle
frame:
    out x, 8
    out x, 8                    ; x = bits - 1
first:
    jmp !osre bit               ; wait until the first data byte is in the OSR
    pull noblock
    jmp first
bit:
    mov pins, ~osr    [1]       ; first half: inverse of the bit
    out pins, 1                 ; second half: the bit (mid-bit transition)
    jmp x-- bit
    set pins, 0b01    [6]       ; TP_IDL: stay positive 300 ns
    nop               [4]
    set pins, 0b00    [tick]    ; idle, then the interframe gap
    nop               [tick]
.wrap
