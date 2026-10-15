.program can_line
; CAN bit timing for a node that transmits and acknowledges: the line engine
; of the pair can_line (engine 1) and can_node (engine 0). It samples RXD,
; follows the transmitter's edges, records every sampled bit for the Host,
; and tells can_node when a bit is sampled (flag 0) and when the next bit
; begins (flag 1).
; G2: tools/pe/g2.can_line table, owned by this engine, stepping on the feed,
;   emitting into the other engine (can_node); G2_IN0 = RXD, G2_IN1 = TXD.
;   Feeding a bit steps it in the cycle of `in pins, 1`, so the bit state
;   machine and the record see the same sample. G2_STATE starts at 0.
; Pins: INPIN base = RXD (pin 8); jmp pin = TXD (pin 0, which reads back the
;   driven value).
; Config: push threshold 15, no autopush, `in` shifting left, buffer mode
;   JOIN_RX; CLKDIV 0; 50 cycles per bit (1 Mbit/s at 50 MHz). Y = N1, which
;   the Host sets with a forced `set y, 8`; start at `idle`.
; Receive words: count-terminated records (tools/pe/proto/records.py) of the
;   raw (stuffed) bus bits, one record from a start of frame until the bus is
;   idle; the last word of a record may hold all 15 bits.
; Flags: sets 0 and 1; reads 2 (bus idle, owned by can_node); waits on 7,
;   which must never be set, as a timer.
; Bit timing, t = 0 at the sample point: the start of frame edge
;   hard-synchronizes, and a sample is taken 36 cycles (72%) after the edge
;   that last synchronized it. A recessive-to-dominant edge resynchronizes if
;   it is seen at t = 3..9 (early: the boundary strobe follows it) or at
;   t = 14..32 (late: after the strobe at t = 11); one seen at t = 10..13
;   counts as seen at 14. An edge caused by this node's own dominant TXD
;   never resynchronizes. Without an edge the next sample is at t = 50.
.define RXD 8
.define N1 8                   ; checks before the boundary strobe
.define N2 19                  ; checks from t = 14 to t = 32
close:                         ; end of a record
    mov x, isrcnt
    push noblock
    mov isr, ~null
    in x, 15
    push noblock               ; trailer 0x8000 | bits in the last word
idle:
    wait 0 gpio RXD    [2]     ; start of frame (c): hard synchronization
    jmp resync
early:                         ; c + 3: an edge before the boundary
    irq set 1                  ; the next bit begins
resync:                        ; c + 4, for an edge seen at c
    set x, N1          [29]    ; next sample at c + 36
bit:
    push iffull noblock        ; t = -2
    out g2, 1                  ; t = -1: the bit state machine steps at 0
    in pins, 1                 ; t = 0: the sample, into the record
    irq set 0                  ; t = 1: sampled; G2 outputs and emission done
    wait 0 gpio RXD timeout    ; t = 2..9: an early edge (X = Y = N1)
    jmp !x late                ; none
    jmp x!=y early
    nop                [5]     ; dominant at t = 2: no edge to follow
late:                          ; t = 11
    irq set 1                  ; the next bit begins
    set x, N2          [1]
    wait 0 gpio RXD timeout    ; t = 14..32: a late edge
    jmp pin other              ; TXD recessive: not this node's own edge
    wait 1 flag 7 timeout      ; own edge or none: idle until t = 34
tail:                          ; t = 35
    set x, 1
    wait 0 flag 2 timeout      ; X = 0 if the bus is idle
    jmp !x close
    set x, N1          [7]
    jmp bit            [1]     ; next sample at t = 50
other:
    jmp !x tail                ; no edge (t = 34)
    jmp resync                 ; an edge at c
