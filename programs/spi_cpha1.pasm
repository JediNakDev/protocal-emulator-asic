; SPI controller with CPHA = 1 (modes 1 and 3), with chip select.
; Pins: MOSI = OUTPIN base, MISO = INPIN base, side-set base = SCK (bit 0)
;   and CS_N = SCK + 1 (bit 1), 2 pins, push-pull. Mode 1 (CPOL = 0): SCK
;   as is. Mode 3 (CPOL = 1): also set the SCK pin's PINCFG invert bit.
;   CS_N needs a pull-up so the peripheral stays deselected before
;   configuration.
; Config: autopush at 8, both shifting left, autopull off.
; Transmit words: one byte per word in bits 15:8; bit 7 set keeps CS_N low
;   for another byte of the same transaction (tools/pe/proto/spi.py).
;   Receive words: the byte read during it, in bits 7:0.
; Timing in cycles at CLKDIV 0 (SCK = clk/8): CS_N falls 4 cycles before the
;   first leading edge, rises 10 cycles after the last trailing edge and
;   stays high at least 8 cycles between transactions. MOSI changes on each
;   leading edge and MISO is sampled (synchronized) on the trailing edge, 4
;   cycles later. A late transmit word stretches the idle SCK between
;   bytes, with CS_N held low, which SPI allows.
.program spi_cpha1
.side_set 2
.wrap_target
    pull block        side 2 [7]   ; idle: CS_N high, at least 8 cycles
    nop               side 0 [2]   ; CS_N falls
byte:
    set x, 7          side 0
bit:
    out pins, 1       side 1 [3]   ; leading edge: MOSI changes
    in pins, 1        side 0 [2]   ; trailing edge: sample MISO
    jmp x-- bit       side 0
    out y, 1          side 0       ; Y = another byte follows
    jmp !y last       side 0
    pull block        side 0       ; SCK idle and CS_N low until it arrives
    jmp byte          side 0
last:
    nop               side 0 [3]   ; hold CS_N after the last trailing edge
.wrap
