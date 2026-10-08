.program replay
; Waveform replay with cycle-exact timing, for example of a waveform recorded
; by capture.pasm and possibly edited (tools/pe/proto/waveform.py).
; Transmit words: bit 0 the pin level, bits 15:1 a count N; the level holds
;   for N + 3 cycles, so a word covers 3 to 32,770 cycles.
; Config: OUTPIN base = the pin, count 1; autopull with pull threshold 16,
;   `out` shifting right; buffer mode 1 (transmit queue only); CLKDIV 0.
; The queue must not run dry while playing: an empty queue stretches the
; current level until the next word arrives.
.wrap_target
    out pins, 1
    out x, 15
hold:
    jmp x-- hold
.wrap
