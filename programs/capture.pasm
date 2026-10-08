.program capture
; Waveform capture: the time of every edge on one pin, exact to the cycle,
; whatever the protocol. Pair it with replay.pasm; tools/pe/proto/waveform.py
; converts between the two.
; The edge capture unit (G3) stamps each edge with the 32-bit cycle counter
; and sets flag 0; this program forwards the stamps to the Host.
; Receive words: the pin level at the start (bit 0), then for every edge its
;   capture time, low half first.
; Config: INPIN base = the pin; CAPCFG = the pin, both edges, set flag;
;   CAPFLAG 0; autopush with push threshold 16, `in` shifting left; buffer
;   mode 2 (receive queue only); CLKDIV 0. Start while the line is idle.
; Each edge takes 3 cycles. Edges closer than that can be lost or paired
; with the wrong time; a 4-cycle glitch filter on the pin rules that out.
; If the Host falls behind and the receive queue fills, the next edge sets
; the capture overrun sticky bit: an edge was lost.
    in pins, 1
    push
.wrap_target
    wait 1 flag 0       ; an edge; completing clears the flag
    in capl, 16
    in caph, 16
.wrap
