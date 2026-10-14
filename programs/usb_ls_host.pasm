.program usb_ls_host
; Low-speed USB host, bit level. The Host builds packets (SYNC, CRC, bit
; stuffing, NRZI, end of packet) with tools/pe/proto/usb.py.
; Command words (shift right): bits 7:0 = number of line states to send - 1,
;   bits 15:8 = reply deadline N in ticks, 0 when no reply is expected.
;   Each command is followed by line states, 2 bits per state: bit 0 = D+,
;   bit 1 = D- (J = 2, K = 1, SE0 = 0).
; Replies are received by G2 (tools/pe/g2.usb_nrzi_dpll) into engine 1,
;   running programs/usb_ls_rx.pasm. This program raises the G2 receive
;   enable (side-set pin) and waits for the reply's end of packet.
; Deadline: after sending, the program checks for the reply's first K once
;   per tick, from tick 11 to tick 10 + N after the tick that sent the last
;   line state (the J after SE0). Without one, reception closes with a record
;   that has no SYNC, and the next command runs. usb.REPLY_DEADLINE = 127
;   gives up 17 bit times after the end of SE0 (measured: replies starting
;   16.75 bit times after it are received, at 17.25 they are missing), inside
;   the 16-18 bit-time window of USB 2.0 section 7.1.19.1.
; Pins: D+ = OUTPIN base, D- = base + 1 (OUTPIN and SETPIN count 2);
;   INPIN base = D+; side-set = receive enable pin (G2_IN1).
; Config: autopull 16, out shifting right; CLKDIV 3, so one tick is 4 cycles
;   and a bit is 8 ticks (1.5 Mbit/s at 48 MHz). The side-set enable leaves
;   only 3 delay bits, which is why the divider is used.
.side_set 1 opt
.wrap_target
cmd:
    pull block
    out x, 8
    out y, 8
    set pindirs, 3
tx:
    out pins, 2       [6]
    jmp x-- tx
    set pindirs, 0             ; release: the device pull-up holds J
    jmp !y cmd
    mov x, y          side 1   ; the deadline; enable reception
    wait 1 pin 0 timeout       ; K: the reply's SYNC has started
    jmp !x no_reply
    set x, 0                   ; pattern: D+ and D- both low (SE0)
    set y, 3
    wait 1 pattern             ; end of packet
    irq set 2         side 0   ; disable reception; engine 1 closes the record
    wait 0 pattern             ; end of SE0
    jmp cmd
no_reply:
    irq set 2         side 0   ; closes a record without a SYNC
.wrap
