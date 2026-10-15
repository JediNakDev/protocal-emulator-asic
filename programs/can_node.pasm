.program can_node
; CAN node: transmits with arbitration, follows every frame's fields, checks
; the CRC and acknowledges frames received without error. Engine 0, paired
; with can_line on engine 1, which times the bits and records them for the
; Host. Load at address 0: the program spans both pages (99 words), and the
; field handlers sit at fixed page addresses (see `mov pc, ~y`).
; Bit state machine (owned by can_line): emits each destuffed bit into this
;   engine's ISR, which steps the CRC; pin 13 is the last sampled bus bit and
;   pin 14 flags a stuff error or a bit error (tools/pe/g2.can_line).
; Pins: side-set base = TXD (pin 0, 1 bit, optional); jmp pin = 14.
; Config: EXECCFG jump base = `tbl` (2); autopull, pull threshold 8, `out`
;   shifting right, `in` shifting left, buffer mode JOIN_TX; checksum feed
;   from bits entering the ISR, CRC mode, left, P = 0x4599 << 17; CLKDIV 0.
;   Start at `quiet`, with TXD recessive.
; Flags: waits on 0 (sampled) and 1 (the next bit begins) from can_line;
;   owns 2 (bus idle, read by can_line) and 6 (transmitting); consumes 3, a
;   transmit request from the Host.
; Transmit: the Host pushes the frame from the first identifier bit to the
;   last CRC bit, stuffed, as 2-bit symbols, 4 per word (bits 1:0 first), then
;   sets flag 3 through FLAG_SET (tools/pe/proto/can.node_tx_words). The node
;   starts the frame once the bus is idle. After a lost arbitration the rest
;   of the frame stays queued until the Host clears the queue (CMD):
;   0 D dominant bit
;   1 A recessive bit: if the bus is dominant, arbitration is lost (or a bit
;     error happened): the node continues as a receiver and acknowledges
;   2 E end of the frame: the CRC delimiter, then the acknowledgement slot
;   3 Q (only while receiving; the program keeps the OSR full of Q)
;   The Host sees from can_line's record whether the frame was
;   acknowledged or lost. Flag 6 clears once the frame or the error ends.
; Fields: Y holds the ISR count (destuffed bits since the start of frame or
;   since the handler of the last field cleared the ISR; at most 16) at which
;   the current field ends; `mov pc, ~y` runs the handler at 63 - Y.
;   12 identifier (std) or base identifier (ext)    -> handler at 51
;    2 RTR or SRR, IDE                               -> handler at 61
;   16 extended identifier bits 17:2                 -> handler at 47
;    9 extended identifier bits 1:0, RTR, r1, r0, DLC -> handler at 54
;    7 RTR, IDE, r0, DLC (std)                       -> handler at 56
;   While transmitting, Y stays 7 or 9 after the DLC: the count only grows.
;   Receiving, the program counts the data bytes and the 15 CRC bits on page
;   1, checks the CRC, waits for the CRC delimiter (a stuff bit may come
;   first) and acknowledges a frame received without error.
; An error (pin 14) ends the frame without an error frame: the node waits
; 12 bit boundaries and returns to idle.
.side_set 1 opt
bit:                           ; 0
    wait 1 flag 1              ; the bit begins
    out pc, 2
tbl:                           ; 2
    jmp td            side 0   ; D
    jmp ta            side 1   ; A
    jmp txend         side 1   ; E: the CRC delimiter
    mov osr, ~null    side 1   ; Q: receiving
td:                            ; 6
    wait 1 flag 0              ; sampled
chk:
    jmp pin err                ; stuff error or bit error
    mov x, isrcnt
    jmp x!=y bit
    mov pc, ~y                 ; end of a field
    jmp bit                    ; 11: unused
                               ; 12-15: {RTR or SRR, IDE} after the identifier
    jmp std                    ; standard data frame
    jmp ext                    ; extended (SRR dominant)
    jmp std                    ; standard remote frame
ext:                           ; 15: extended
    mov isr, null
    set y, 16
    jmp bit
quiet:                         ; 18: the bus is idle
    irq set 2                  ; can_line ends its record unless a frame starts
    irq clear 0                ; its last sample
    irq clear 6
    mov isr, null
    mov osr, null
    out lfsr, 16               ; clear the CRC
    mov osr, ~null             ; receive Q
poll:
    set x, 1
    wait 1 flag 3 timeout      ; a transmit request?
    jmp !x listen
    out null, 8       side 0   ; start of frame; the first `out pc` pulls
    irq set 6
listen:
    set x, 1
    wait 0 flag 0 timeout      ; X = 0 once a start of frame is sampled
    jmp x-- poll
sof:
    irq clear 2
    set y, 12
    jmp td                     ; the start of frame bit, counted
ta:                            ; 36: recessive, transmitting
    wait 1 flag 0
    set x, 1
    wait 1 gpio 13 timeout     ; X = 0 if the bus is dominant
    jmp x-- chk
    irq clear 6                ; lost arbitration: receive the frame
    mov osr, ~null
    jmp chk
std:                           ; 43
    set y, 7                   ; RTR in ISR bit 6, DLC in bits 3:0
    jmp bit
txend:                         ; 45: Y is 7 or 9, so no acknowledgement
    set page, delim
    jmp delim
f16:                           ; 47: extended identifier bits 17:2 done
    mov isr, null
    set y, 9
    jmp bit
    jmp bit                    ; 50: unused
f12:                           ; 51: identifier done
    mov isr, ~y                ; 63 - 12 = 51, so after 2 more bits
    set y, 2                   ; `mov pc, isr` goes to 12-15
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
    set page, eof
    jmp eof
.wrap                          ; never reached: no instruction falls through 63
f7rx:                          ; 64: data bytes = RTR ? 0 : min(DLC, 8)
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
    jmp !x crc
field:                         ; a field of Y bits
    mov isr, null
fbit:
    wait 1 flag 1
    wait 1 flag 0
    jmp pin eof                ; stuff error
    mov x, isrcnt
    jmp x!=y fbit
    mov x, osr
    jmp x-- byte
    in lfsrh, 16
    mov y, isr                 ; 0 if the CRC is right
    mov isr, null
delim:                         ; until the bit after the CRC delimiter begins
    wait 1 flag 1
    mov x, isrcnt              ; 0 until the delimiter; a stuff bit is not counted
    jmp !x delim
    jmp y-- eof                ; acknowledgement slot
    nop               side 0   ; acknowledge
eof:
    set x, 11
eofl:                          ; acknowledgement delimiter, end of frame,
    wait 1 flag 1              ; intermission, idle
    jmp x-- eofl      side 1
    set page, quiet
    jmp quiet
crc:
    set y, 15
    jmp field
