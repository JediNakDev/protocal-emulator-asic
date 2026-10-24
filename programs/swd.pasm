.program swd
; SWD host (probe side), ARM ADIv5 wire protocol (tools/pe/proto/swd.py).
; The program runs whole transactions and checks the target's ACK itself:
; after WAIT, FAULT or no answer it skips the data phase, so the target never
; sees data bits as a request and the two sides never drive SWDIO together.
; Commands (transmit words, shift right):
;   bit 0 = 0, sequence: bits 4:1 = n - 1; the next word holds n bits
;     (1-16) to drive, first bit lowest: line resets, idle cycles, other
;     sequences.
;   bit 0 = 1, transaction: bits 8:1 = the request byte (first bit lowest),
;     bit 9 = 1 to read (it must match the request's RnW bit). A write is
;     followed by three words: data[15:0], data[31:16], parity in bit 0;
;     after a failed ACK the program drops them.
; Receive words: per transaction, the ACK in bits 15:13 (first bit lowest:
;   OK = 0x2000); after an OK read, data[15:0], data[31:16], and the parity
;   bit in bit 15.
; Each transaction ends with the turnaround back to the host and SWDIO
; driven low; the Host adds idle cycles with sequences.
; Pins: SWDIO = OUTPIN/INPIN base (OUTPIN and SETPIN count 1), SWCLK =
;   side-set pin.
; Config: autopull and autopush at 16, `out` and `in` shifting right; CLKDIV 0:
;   8 cycles per SWCLK (6.25 MHz at 50 MHz). ORUNDETECT must be 0, its reset
;   value, so that a failed transfer has no data phase.
; The host changes SWDIO while SWCLK is low; the target samples on the
; rising edge, drives its bits after a rising edge, and the host samples
; them just before the next rising edge.
.side_set 1 opt
.wrap_target
cmd:
    pull block        side 0
    out y, 1
    set pindirs, 1
    jmp !y sequence
    set x, 7                   ; transaction: the request
req:
    out pins, 1       side 0 [3]
    jmp x-- req       side 1 [3]
    set pindirs, 0    side 0 [3]   ; turnaround to the target
    set x, 2          side 1 [3]
ack:
    nop               side 0 [3]
    in pins, 1        side 1 [2]
    jmp x-- ack       side 1
    mov x, ::isr               ; the ACK, first bit in bit 0: OK = 4
    push              side 0
    set y, 4
    jmp x!=y failed
    out y, 1
    jmp !y write
    set x, 31                  ; read: 32 data bits, autopushed per 16
rbit:
    nop               side 0 [3]
    in pins, 1        side 1 [2]
    jmp x-- rbit      side 1
    nop               side 0 [3]   ; parity
    in pins, 1        side 1 [2]
    push              side 1
turnaround:                    ; one clock with SWDIO released, then drive
    nop               side 0 [3]
    nop               side 1 [3]
    set pindirs, 1    side 0
    jmp cmd           side 0
write:
    nop               side 0 [3]   ; turnaround back to the host
    nop               side 1 [2]
    set pindirs, 1    side 1
    pull block                 ; data[15:0]; the rest is autopulled
    set x, 31
wbit:
    out pins, 1       side 0 [3]
    jmp x-- wbit      side 1 [3]
    out pins, 1       side 0 [3]   ; parity
    jmp cmd           side 1 [3]
failed:                        ; WAIT, FAULT or no answer: no data phase
    out y, 1
    jmp y-- turnaround         ; a read
    pull block                 ; a write: drop its three words
    pull block
    pull block
    jmp turnaround
sequence:
    out x, 4
    pull block
seqbit:
    out pins, 1       side 0 [3]
    jmp x-- seqbit    side 1 [3]
.wrap
