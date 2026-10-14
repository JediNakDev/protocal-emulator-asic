; SPI controller with CPHA = 0 (modes 0 and 2). MOSI = OUTPIN base,
; SCK = side-set, MISO = INPIN base.
; Configure autopull/autopush at 8 bits, both shifting left.
; TX bytes occupy bits 15:8; RX bytes occupy bits 7:0.
; MOSI changes while SCK is idle and MISO is sampled on the leading edge.
; Mode 0 (CPOL = 0): SCK push-pull. Mode 2 (CPOL = 1): also set the SCK
; pin's PINCFG invert bit.
; The Host controls chip select separately.
; SCK = clk/8 at CLKDIV=0, with synchronized MISO.
.program spi_cpha0
.side_set 1
    out pins, 1 side 0 [3]
    in pins, 1  side 1 [3]
