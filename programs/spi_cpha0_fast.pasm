; SPI controller with CPHA = 0 (modes 0 and 2) at 4 cycles per bit.
; MOSI = OUTPIN base, SCK = side-set, MISO = INPIN base.
; Configure autopull/autopush at 8 bits, both shifting left.
; TX bytes occupy bits 15:8; RX bytes occupy bits 7:0.
; Mode 2 (CPOL = 1): also set the SCK pin's PINCFG invert bit.
; The Host controls chip select separately.
; SCK = clk/4 at CLKDIV=0; MISO requires synchronizer bypass.
.program spi_cpha0_fast
.side_set 1
    out pins, 1 side 0 [1]
    in pins, 1  side 1 [1]
