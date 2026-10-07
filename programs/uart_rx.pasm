; UART 8N1 RX. INPIN base and JMP_PIN both select RX.
; Configure IN_RIGHT; received bytes occupy bits 15:8 of each word.
; A low stop bit sets flag 4 and drops the byte.
; One bit takes 8 engine ticks (16 clk cycles with CLKDIV=1).
.program uart_rx
start:
    wait 0 pin 0            ; start bit edge
    set x, 7 [10]           ; to the middle of data bit 0
bitloop:
    in pins, 1
    jmp x-- bitloop [6]
    jmp pin good            ; stop bit must be high
    irq set 4               ; framing error
    wait 1 pin 0
    jmp start
good:
    push
