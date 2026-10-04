; UART 8N1 TX. OUTPIN base and optional side-set both select TX.
; Configure OUT_RIGHT and push-pull output, initially high.
; Send one byte per transmit word in bits 7:0.
; One bit takes 8 engine ticks (16 clk cycles with CLKDIV=1).
.program uart_tx
.side_set 1 opt
    pull       side 1 [7]   ; idle high while waiting for data
    set x, 7   side 0 [7]   ; start bit
bitloop:
    out pins, 1
    jmp x-- bitloop [6]
