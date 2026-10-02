; UART transmitter, 8N1, LSB first.
; T0 = clk / baud (434 for 115200 at 50 MHz). The timer tick is the bit
; clock, so every bit lasts exactly T0 cycles regardless of instruction count.
.program uart_tx
.pin tx 0
.out tx
.shift out right
    set pins, 1             ; idle high
    set pindirs, 1
.wrap_target
    pull                    ; wait for a byte from the host
    set x, 7        [tick]  ; align to the bit clock
    set pin tx, 0   [tick]  ; start bit
bit:
    out pins, 1             ; data bit
    jmp x-- bit     [tick]
    set pin tx, 1           ; stop bit (lasts until the next start bit's tick)
.wrap
