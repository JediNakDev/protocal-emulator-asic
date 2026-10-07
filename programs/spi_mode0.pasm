; SPI mode 0 controller. MOSI = OUTPIN base, SCK = side-set, MISO = INPIN base.
; Configure autopull/autopush at 8 bits, both shifting left.
; TX bytes occupy bits 15:8; RX bytes occupy bits 7:0.
; The Host controls chip select separately.
; SCK = clk/8 at CLKDIV=0, with synchronized MISO.
.program spi_mode0
.side_set 1
    out pins, 1 side 0 [3]
    in pins, 1  side 1 [3]
