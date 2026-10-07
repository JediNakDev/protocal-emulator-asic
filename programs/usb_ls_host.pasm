.program usb_ls_host
; Low-speed USB host, bit level. The Host builds packets (SYNC, CRC, bit
; stuffing, NRZI, end of packet) with tools/pe/proto/usb.py.
; Command words (shift right): bits 7:0 = number of bits to send - 1,
;   bit 8 = receive a reply after sending. Each command is followed by
;   line states, 2 bits per bit: bit 0 = D+, bit 1 = D- (J = 2, K = 1, SE0 = 0).
; Replies are received by G2 (tools/pe/g2.usb_nrzi_dpll) into engine 1,
;   running programs/usb_ls_rx.pasm. This program raises the G2 receive
;   enable (side-set pin) and waits for the reply's end of packet.
; Pins: D+ = OUTPIN base, D- = base + 1 (OUTPIN and SETPIN count 2);
;   INPIN base = D+; side-set = receive enable pin (G2_IN1).
; Config: autopull 16, out shifting right; CLKDIV 3, so one tick is 4 cycles
;   and a bit is 8 ticks (1.5 Mbit/s at 48 MHz). The side-set enable leaves
;   only 3 delay bits, which is why the divider is used. A missing reply
;   waits forever: the Host recovers with a forced jmp.
.side_set 1 opt
.wrap_target
cmd:
    pull block
    out x, 8
    out y, 1
    out null, 7
    set pindirs, 3
tx:
    out pins, 2       [6]
    jmp x-- tx
    set pindirs, 0             ; release: the device pull-up holds J
    jmp !y cmd
    set x, 0                   ; pattern: D+ and D- both low (SE0)
    set y, 3          side 1   ; enable reception
    wait 1 pattern             ; end of packet
    irq set 2         side 0   ; disable reception; engine 1 closes the record
    wait 0 pattern             ; end of SE0
.wrap
