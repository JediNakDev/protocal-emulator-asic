; UART receiver, 8N1, LSB first.
; T0 = clk / baud, T1 = T0/2 - 1 (first tick lands mid start bit).
; "wait ... resync" restarts the timer on the start edge: clock recovery.
; Framing errors set the user flag and drop the byte; overflow sets FLAG_OVF.
.program uart_rx
.pin rx 0
.in rx
.jmp_pin rx
.shift in right
.wrap_target
start:
    wait 0 pin rx resync [tick] ; start edge, then to mid start bit
    jmp pin start               ; glitch: line went back high
    set x, 7        [tick]      ; to mid bit 0
bit:
    in pins, 1
    jmp x-- bit     [tick]      ; last pass waits to mid stop bit
    jmp !pin bad
    push noblock                ; good frame
.wrap
bad:
    set flag, 1                 ; framing error
    mov isr, null
    wait 1 pin rx               ; wait for the line to go idle
    jmp start
