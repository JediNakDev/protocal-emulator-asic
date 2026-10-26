.program can_node
; CAN 2.0A/2.0B node with error confinement (ISO 11898-1): transmits with
; arbitration, receives, checks the CRC, acknowledges, signals errors with
; active or passive error flags, sends overload frames, keeps the transmit
; and receive error counters, becomes error passive and bus-off, and
; recovers. Engine 0, paired with can_line on engine 1, which times the bits
; and records them for the Host. Load at address 0: the program spans pages
; 0-7 with fixed handler addresses in page 0 (see `mov pc, ~y`), and
; can_line goes in the rest of page 7.
; Bit state machine (owned by can_line): emits each destuffed bit into this
;   engine's ISR, which steps the CRC; pin 13 is the last sampled bus bit and
;   pin 14 flags a stuff error or a bit error (tools/pe/g2.can_line).
; Pins: side-set base = TXD (pin 0, 1 bit, optional); jmp pin = 14; RXD is
;   pin 8.
; Config: EXECCFG jump base = `tbl` (15); autopull, pull threshold 15, `out`
;   shifting right, `in` shifting left, buffer mode FIFO_TX_RAM (transmit
;   queue in entries 0-3, RAM in 4-7); checksum feed from bits entering the
;   ISR, CRC mode, left, P = 0x4599 << 17; CLKDIV 0; STATUSCFG = 1 (transmit
;   queue empty). Start at `bol` with TXD recessive, flags clear and RAM 4 = 0
;   (one occurrence of 11 recessive bits: bus integration).
; RAM: 4 return address or bus-off count (scratch), 6 receive error counter
;   REC, 7 transmit error counter TEC. A counter C is stored as 255 - C, and
;   TEC reads 0xFFFF while bus-off. The Host may read them at any time.
; Flags: waits on 0 (sampled) and 1 (the next bit begins) from can_line;
;   owns 2 (bus idle, read by can_line), 4 (the last transmission has not
;   succeeded), 5 (error passive) and 6 (this node is the transmitter);
;   consumes 3, a transmit request from the Host.
; Transmit: the Host clears the transmit queue, pushes the frame after the
;   start of frame, stuffed, as 3-bit symbols, 5 per word (bits 2:0 first),
;   then sets flag 3 (tools/pe/proto/can.node_tx_words):
;   0 D dominant bit
;   1 A recessive bit in the arbitration field: a dominant bus loses
;     arbitration, or is a stuff error if it is a stuff bit
;   2 R recessive bit after the arbitration field: a dominant bus is a bit
;     error
;   3 E end: the CRC delimiter
;   7 Q (receiving; the program keeps the OSR full of Q)
;   The node starts the frame when the bus is idle, or joins a start of
;   frame in the third bit of intermission. Flags 6 and 4 set when it
;   begins; flag 4 clears if the frame is sent without error. Flag 6 clears
;   at the bus idle after the attempt, at once on lost arbitration or
;   transmit underrun (the queue running dry inside the frame, after which
;   the node releases TXD and receives the rest). Retransmission is the
;   Host's: clear the queue, push the frame and set flag 3 again.
; Bit timing: can_line sets flag 1 (boundary) at t = 11 after each sample
;   and flag 0 (sample) at t = 1; the node consumes each bit as `wait 1 flag
;   1` then `wait 1 flag 0`, drives TXD within 3 cycles of the boundary and
;   decides on the sample within the next 9 cycles.
.side_set 1 opt
.define RXD 8

; ------------------------------------------------------------------ page 0
; The bit loop from the start of frame to the end of the CRC.
; Y holds the ISR count (destuffed bits since the start of frame or since
; the handler of the last field cleared the ISR; at most 16) at which the
; current field ends; `mov pc, ~y` runs the handler at 63 - Y:
;   12 identifier (std) or base identifier (ext)    -> handler at 51
;    2 RTR or SRR, IDE                               -> handler at 61
;   16 extended identifier bits 17:2                 -> handler at 47
;    9 extended identifier bits 1:0, RTR, r1, r0, DLC -> handler at 54
;    7 RTR, IDE, r0, DLC (std)                       -> handler at 56
; While transmitting, Y stays 7 or 9 after the DLC: the count only grows.
                               ; 0-3: {RTR or SRR, IDE} after the identifier
    jmp std                    ; standard data frame
    jmp ext                    ; extended (SRR dominant)
    jmp std                    ; standard remote frame
ext:                           ; 3: extended
    mov isr, null
    set y, 16
    jmp bit
std:
    set y, 7                   ; RTR in ISR bit 6, DLC in bits 3:0
    jmp bit
bit:                           ; 8: before the next bit boundary
    jmp !osre ready            ; symbols remain in the OSR
    mov x, status              ; all ones if the transmit queue is empty
    jmp !x ready
    irq clear 6                ; underrun: release TXD and receive the rest
    mov osr, ~null
ready:
    wait 1 flag 1              ; the boundary
    out pc, 3
tbl:                           ; 15
    jmp td            side 0   ; D
    jmp ta            side 1   ; A
    jmp tr            side 1   ; R
    jmp te            side 1   ; E: the CRC delimiter
    jmp tr            side 1   ; 4-6: unused, as R
    jmp tr            side 1
    jmp tr            side 1
    mov osr, ~null    side 1   ; Q: receiving
td:                            ; 23
    wait 1 flag 0              ; sampled
chk:
    jmp pin err                ; stuff error or bit error
    mov x, isrcnt
    jmp x!=y bit
    mov pc, ~y                 ; end of a field
ta:                            ; recessive, arbitration
    wait 1 flag 0
    set x, 1
    wait 1 gpio 13 timeout     ; X = 0 if the bus is dominant
    jmp x-- chk
    jmp pin stufferr           ; a recessive stuff bit overwritten
    irq clear 6                ; lost arbitration: receive the frame
    mov osr, ~null
    jmp chk
tr:                            ; recessive, after arbitration
    wait 1 flag 0
    set x, 1
    wait 1 gpio 13 timeout
    jmp x-- chk
    jmp err                    ; bit error
te:
    set page, txdelim
    jmp txdelim
stufferr:                      ; stuff error in arbitration: TEC unchanged (3b)
    set page, err_nc
    jmp err_nc
.org 47
f16:                           ; 47: extended identifier bits 17:2 done
    mov isr, null
    set y, 9
    jmp bit
    jmp bit                    ; 50: unused
f12:                           ; 51: identifier done
    mov isr, null              ; `mov pc, isr` goes to 0-3 after 2 more bits
    set y, 2
    jmp bit
f9:                            ; 54: DLC done (ext)
    jmp f7
    jmp bit                    ; 55: unused
f7:                            ; 56: DLC done (std)
    set x, 1
    wait 0 flag 6 timeout      ; X = 0 if transmitting
    jmp !x bit
    set page, f7rx
    jmp f7rx
f2:                            ; 61: RTR or SRR, IDE done
    mov pc, isr
err:                           ; 62
    set page, err_c
    jmp err_c
.wrap                          ; never reached: no instruction falls through 63

; ------------------------------------------------------------------ page 1
; Receiving the data bytes, the CRC and its delimiter; transmitting the CRC
; delimiter and the acknowledgement slot.
.org 64
f7rx:                          ; data bytes = RTR ? 0 : min(DLC, 8)
    mov osr, isr
    out x, 3
    out y, 1
    jmp !y small
    set x, 8
small:
    out null, 2
    out y, 1
    jmp !y data
    set x, 0                   ; remote frame
data:
    set y, 8
byte:                          ; X data bytes remain, then the CRC
    mov osr, x
    jmp x-- field              ; X is reloaded from the OSR after the field
    set y, 15                  ; the CRC
field:                         ; a field of Y bits
    mov isr, null
fbit:
    wait 1 flag 1
    wait 1 flag 0
    jmp pin rxerr              ; stuff error
    mov x, isrcnt
    jmp x!=y fbit
    mov x, osr
    jmp x-- byte
    in lfsrh, 16
    mov y, isr                 ; 0 if the CRC is right
    mov isr, null
delim:                         ; the CRC delimiter, after a stuff bit if the
    wait 1 flag 1              ; CRC ends with five equal bits
    wait 1 flag 0
    jmp pin rxerr              ; a stuff error after the CRC
    mov x, isrcnt              ; 0 until the delimiter; a stuff bit is not counted
    jmp !x delim
    set x, 1
    wait 1 gpio 13 timeout     ; X = 0 if dominant
    jmp !x rxerr               ; form error
    jmp !y ackf
    wait 1 flag 1              ; CRC error: not acknowledged, and the error
    wait 1 flag 0              ; flag follows the acknowledgement delimiter
    wait 1 flag 1
    wait 1 flag 0
rxerr:
    set page, err_c
    jmp err_c
ackf:                          ; received with the right CRC
    wait 1 flag 1
    nop               side 0   ; acknowledge
    wait 1 flag 0
    set x, 1
    wait 1 gpio 13 timeout     ; X = 0 if dominant, as driven
    jmp x-- rxerr              ; bit error
    wait 1 flag 1
    nop               side 1   ; acknowledgement delimiter
    set page, rxok
    jmp rxok
txdelim:                       ; E: the CRC delimiter, recessive
    wait 1 flag 0
    set x, 1
    wait 1 gpio 13 timeout
    jmp !x rxerr               ; form error
    wait 1 flag 1              ; acknowledgement slot: recessive
    wait 1 flag 0
    wait 1 gpio 13 timeout     ; X is still 1
    jmp x-- ackerr             ; recessive: not acknowledged
    wait 1 flag 1
    set x, 1
    set y, 6                   ; the delimiter and end of frame bits 1-6
    set page, fixed
    jmp fixed
ackerr:
    set page, err_ack
    jmp err_ack

; ------------------------------------------------------------------ page 2
; The acknowledgement, the success counts, the acknowledgement delimiter
; and the end of frame.
.org 128
rxok:
    set y, 6                   ; received: REC - 1, or 127 if above 127
    mov osr, ram
    out null, 7
    mov x, osr                 ; 0 if the stored value is below 128
    jmp x-- dec
    mov osr, ~null             ; stored 128: REC 127
    out null, 9
    mov x, ~osr
    jmp x-- inc
txok:                          ; sent: TEC - 1
    irq clear 4
    set y, 7
dec:                           ; counter Y - 1 unless 0: stored + 1 unless 255
    mov osr, ~ram
    out x, 8
    jmp !x act
    mov x, ~ram
    jmp x-- inc
inc:
    mov osr, ~x
    out ram, 16
act:
    set y, 7                   ; error active again if both counters are
    mov osr, ram               ; below 128
    out null, 7
    mov x, osr
    jmp !x actd
    set y, 6
    mov osr, ram
    out null, 7
    mov x, osr
    jmp !x actd
    irq clear 5
actd:
    set x, 1
    wait 0 flag 6 timeout      ; X = 0 if transmitting
    jmp !x txint
    set x, 1
    set y, 6                   ; the delimiter and end of frame bits 1-6
fixed:                         ; bits that must be recessive; the boundary of
    wait 1 flag 0              ; the first is consumed; X = 1, Y = bits - 1
    wait 1 gpio 13 timeout
    jmp !x serr                ; form error
    jmp y-- fixn
    set x, 1
    wait 0 flag 6 timeout
    jmp x-- rxint              ; a receiver: end of frame bit 7 can be dominant
    wait 1 flag 1
    wait 1 flag 0              ; end of frame bit 7
    set x, 1
    wait 1 gpio 13 timeout
    jmp !x serr
    jmp txok
fixn:
    wait 1 flag 1
    jmp fixed
txint:
    set y, 1                   ; intermission bits 1 and 2
    jmp intf
rxint:
    set y, 2                   ; end of frame bit 7, intermission bits 1 and 2
intf:
    set page, inter
    jmp inter
serr:
    set page, err_c
    jmp err_c

; ------------------------------------------------------------------ page 3
; Intermission, bus idle and the start of frame.
.org 192
inter:                         ; Y + 1 bits in which a dominant bit is an
    wait 1 flag 1              ; overload condition: end of frame bit 7 (a
    wait 1 flag 0              ; receiver) or delimiter bit 8, then
    set x, 1                   ; intermission bits 1 and 2
    wait 1 gpio 13 timeout
    jmp !x ovlf
    jmp y-- inter
ib3:                           ; t = 7 after the sample of intermission bit 2
    irq set 2                  ; can_line ends its record unless a frame starts
    mov isr, null
    mov osr, null
    out lfsr, 16               ; clear the CRC (its low 17 bits are always 0)
    mov osr, ~null             ; receive Q
    set x, 1
    wait 0 flag 6 timeout      ; X = 0 if this node was the transmitter
    jmp x-- b3
    irq clear 6                ; the attempt is over
    set x, 1
    wait 0 flag 5 timeout      ; error passive: suspend transmission
    jmp !x suspend
b3:
    irq clear 1                ; after can_line's last boundary strobe
    set x, 1
    wait 0 flag 3 timeout      ; X = 0 if a frame waits
    jmp x-- b3w
prep:                          ; transmit: join a start of frame in bit 3, or
    irq set 6                  ; send one when the bus is idle
    irq set 4
    irq clear 3
    out null, 16               ; empty the OSR: the first `out pc` pulls
b3w:                           ; intermission bit 3: a dominant bit is a
    set x, 31                  ; start of frame
    wait 1 flag 0 timeout
    jmp x-- sof
idle:
    set x, 1
    wait 0 flag 6 timeout      ; X = 0 if a transmission is prepared
    jmp !x txsof
    set x, 1
    wait 0 flag 3 timeout
    jmp !x prep
    set x, 31
    wait 1 flag 0 timeout
    jmp x-- sof
    jmp idle
txsof:
    set y, 2
    nop               side 0   ; start of frame
sofw:                          ; until can_line samples it
    set x, 31
    wait 1 flag 0 timeout
    jmp x-- sof
    jmp y-- sofw
    nop               side 1   ; never sampled: the attempt failed
    irq clear 6
    jmp idle
suspend:                       ; intermission bit 3 and 8 more bits: receive
    irq clear 1                ; only
    set y, 12
susw:
    set x, 31
    wait 1 flag 0 timeout
    jmp x-- sof
    jmp y-- susw
    jmp idle
sof:                           ; sampled: the next boundary sends or receives
    irq clear 2                ; the first identifier bit
    set y, 12
    set page, bit
    jmp bit
ovlf:
    set page, ovl
    jmp ovl

; ------------------------------------------------------------------ page 4
; Error and overload flags. An error found at a sample starts a flag at the
; next boundary: active (6 dominant bits) or passive (recessive until six
; equal bits), by the error state then; the counters change while the first
; flag bit is on the bus. The OSR is all ones for an overload flag, or while
; exception 3a holds, and 0 for other error flags.
.org 256
err_c:                         ; rule 1 or 3: a receiver +1, a transmitter +8
    set y, errcount
    jmp errgo
err8:                          ; a bit error in an active error flag or an
    set y, add8role            ; overload flag: +8 (rules 4 and 5)
errgo:                         ; Y = counting routine
    set x, 1
    wait 0 flag 5 timeout      ; X = 0 if error passive
    jmp !x errgp
    wait 1 flag 1
    nop               side 0   ; active error flag
    mov x, y
    set y, ~r_af
    jmp go
errgp:
    wait 1 flag 1
    nop               side 1   ; passive error flag
    mov x, y
    set y, ~r_pf
go:
    set page, call
    jmp call
err_nc:                        ; an error flag without counting (3b)
    set y, ret
    jmp errgo
err_ack:                       ; the transmitter was not acknowledged
    set x, 1
    wait 0 flag 5 timeout
    jmp !x p3a
    set y, addT8               ; error active: +8
    jmp errgo
p3a:                           ; error passive: +8 only if a dominant bit is
    wait 1 flag 1              ; seen during the passive flag (3a)
    nop               side 1
    mov osr, ~null
    set page, pf
    jmp pf
ovl:                           ; overload flag
    wait 1 flag 1
    nop               side 0
    mov osr, ~null
af:                            ; active error or overload flag: the first
    set y, 5                   ; bit is on the bus
afl:
    wait 1 flag 0
    set x, 1
    wait 1 gpio 13 timeout     ; X = 0 if dominant, as driven
    jmp x-- err8               ; bit error
    jmp y-- afn
    wait 1 flag 1              ; six bits sent
    nop               side 1
    set page, edl
    jmp edl
afn:
    wait 1 flag 1
    jmp afl
busoff:                        ; TEC beyond 255: release the bus
    nop               side 1
    irq clear 6
    mov osr, ~null
    set y, 4
    out ram, 7                 ; 128 occurrences, counted down from 127
    mov osr, ~null
    set y, 7
    out ram, 16                ; TEC reads 0xFFFF
    set page, bol
    jmp bol

; ------------------------------------------------------------------ page 5
; Passive error flag; error and overload delimiters.
.org 320
pf:                            ; passive error flag: ends after six equal bits
    wait 1 flag 0
    set x, 1
    wait 1 gpio 13 timeout
    jmp !x pfd
pfr5:
    set x, 1
    set y, 4                   ; a run of recessive bits
pfr:
    wait 1 flag 1
    wait 1 flag 0
    wait 1 gpio 13 timeout
    jmp !x pfd
    jmp y-- pfr
    jmp pfend
pfd:                           ; a dominant bit
    mov x, osr
    jmp !x pfdm
    set y, ~r_pfd              ; 3a no longer holds: TEC + 8
    set x, addT8
    jmp go5
pfdm:
    set y, 4                   ; a run of dominant bits
pfdl:
    wait 1 flag 1
    wait 1 flag 0
    set x, 1
    wait 1 gpio 13 timeout
    jmp x-- pfr5
    jmp y-- pfdl
pfend:
    wait 1 flag 1
edl:                           ; delimiter: the first bit after the flag
    wait 1 flag 0
    set x, 1
    wait 1 gpio 13 timeout
    jmp x-- edr                ; recessive: delimiter bit 1
    mov x, osr
    jmp !x edr2                ; after an error flag
edc1:                          ; one dominant bit after the flag
    set y, 6
eddl:
    wait 1 flag 1
    wait 1 flag 0
    set x, 1
    wait 1 gpio 13 timeout
    jmp x-- edr
    jmp y-- eddl
    set y, ~r_edc0             ; eight dominant bits: +8 (rule 6)
    set x, add8role
go5:
    set page, call
    jmp call
edc0:
    set y, 7
    jmp eddl
edr2:
    set x, 1
    wait 0 flag 6 timeout      ; X = 0 if transmitting
    jmp !x edc1
    set y, ~r_edc1             ; a receiver: +8 (rule 2)
    set x, add8role
    jmp go5
edr:                           ; delimiter bit 1 sampled recessive
    set x, 1
    set y, 5
edrl:                          ; bits 2-7
    wait 1 flag 1
    wait 1 flag 0
    wait 1 gpio 13 timeout
    jmp !x eform               ; form error
    jmp y-- edrl
    set y, 2                   ; bit 8 and intermission bits 1 and 2
    set page, inter
    jmp inter
eform:
    set page, err_c
    jmp err_c

; ------------------------------------------------------------------ page 6
; Counter routines. `call` with Y = ~return (a stub in this page) and X =
; routine: saves the return in RAM 4, updates the counters and flag 5, and
; returns with `mov pc, ~ram`. An increment can only enter error passive.
.org 384
call:
    mov osr, y
    set y, 4
    out ram, 16
    mov pc, x
ret:
    set y, 4
    mov pc, ~ram
errcount:                      ; rule 1 or 3
    set x, 1
    wait 0 flag 6 timeout      ; X = 0 if transmitting
    jmp !x addT8
addR1:                         ; REC + 1, at most 255
    set y, 6
    mov x, ram
    jmp !x rw
    jmp x-- rw
addT8:                         ; TEC + 8; beyond 255 is bus-off
    set y, 7
    mov x, ram
    mov osr, x
    out null, 3
    mov y, osr
    jmp !y tbo                 ; stored value below 8
    set y, 7
    jmp sub8
add8role:                      ; + 8 to the counter of this node's role
    set x, 1
    wait 0 flag 6 timeout
    jmp !x addT8
addR8:                         ; REC + 8, at most 255
    set y, 6
    mov x, ram
    mov osr, x
    out null, 3
    mov y, osr
    jmp !y rsat
    set y, 6
sub8:                          ; stored - 8
    jmp x-- s1
s1: jmp x-- s2
s2: jmp x-- s3
s3: jmp x-- s4
s4: jmp x-- s5
s5: jmp x-- s6
s6: jmp x-- s7
s7: jmp x-- rw
rsat:
    set x, 0
rw:                            ; write counter Y; error passive if it is at
    mov osr, x                 ; least 128
    out ram, 16
    mov osr, x
    out null, 7
    mov x, osr
    jmp !x pas
    jmp ret
pas:
    irq set 5
    jmp ret
tbo:
    set page, busoff
    jmp busoff
r_af:
    mov osr, null
    set page, af
    jmp af
r_pf:
    mov osr, null
    set page, pf
    jmp pf
r_pfd:
    mov osr, null
    set page, pfdm
    jmp pfdm
r_edc1:
    set page, edc1
    jmp edc1
r_edc0:
    set page, edc0
    jmp edc0

; ------------------------------------------------------------------ page 7
; Bus integration and bus-off recovery: counts occurrences of 11 bit times
; with RXD recessive (RAM 4 holds the occurrences left, minus 1), keeping
; flag 2 set while the bus is idle so that can_line records each frame.
.org 448
bol:                           ; entry; RAM 4 = 0 for bus integration
    set y, 15
bw:                            ; 16 x 33 cycles: an occurrence 10.7 bit times
    set x, 30                  ; after the edge, so that the 11 bits between
                               ; frames on a busy bus count
    wait 0 gpio RXD timeout    ; X = 0 if RXD stayed recessive
    jmp x-- bact
    jmp y-- bw
    irq set 2                  ; the bus is idle
    set y, 4
    mov x, ram
    jmp x-- bmore
    mov osr, ~null             ; done: error active, both counters 0
    set y, 6
    out ram, 8                 ; 0x00FF
    set y, 7
    out ram, 8
    irq clear 5
    irq clear 0                ; the samples of the frames counted
    set page, ib3
    jmp ib3
bmore:                         ; idle on: 550 cycles (11 bit times) from one
    mov osr, x                 ; occurrence to the next
    out ram, 16        [7]
    jmp bol            [7]
bact:                          ; RXD dominant: the count restarts
    irq clear 2                ; can_line records the frame
    wait 1 gpio RXD
    jmp bol
