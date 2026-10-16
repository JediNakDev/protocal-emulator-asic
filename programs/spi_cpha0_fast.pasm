; SPI controller with CPHA = 0 (modes 0 and 2) at 4 cycles per bit, with
; chip select. As spi_cpha0, but SCK = clk/4 at CLKDIV 0, and MISO requires
; the synchronizer bypass.
; Timing in cycles: CS_N falls 6 cycles before the first leading edge, rises
;   6 cycles after the last trailing edge and stays high at least 8 cycles
;   between transactions. MOSI changes 2 cycles before each leading edge,
;   where MISO is sampled.
.program spi_cpha0_fast
.side_set 2
.wrap_target
    pull block        side 2 [7]   ; idle: CS_N high, at least 8 cycles
    nop               side 0 [2]   ; CS_N falls
byte:
    set x, 7          side 0
bit:
    out pins, 1       side 0 [1]   ; MOSI changes
    in pins, 1        side 1       ; leading edge: sample MISO
    jmp x-- bit       side 1
    out y, 1          side 0       ; trailing edge; Y = another byte follows
    jmp !y last       side 0
    pull block        side 0       ; SCK idle and CS_N low until it arrives
    jmp byte          side 0
last:
    nop               side 0 [3]   ; hold CS_N after the last trailing edge
.wrap
