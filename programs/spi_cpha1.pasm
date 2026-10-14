; SPI controller with CPHA = 1 (modes 1 and 3). MOSI = OUTPIN base,
; SCK = side-set, MISO = INPIN base.
; Configure autopush at 8 bits and pull threshold 8, both shifting left;
; autopull off. TX bytes occupy bits 15:8; RX bytes occupy bits 7:0.
; MOSI changes on the leading SCK edge and MISO is sampled on the trailing
; one. Mode 1 (CPOL = 0): SCK push-pull. Mode 3 (CPOL = 1): also set the SCK
; pin's PINCFG invert bit. SCK idles at its CPOL level while the transmit
; queue is empty, since the stalled `pull` drives it.
; The Host controls chip select separately.
; SCK = clk/8 at CLKDIV=0, with synchronized MISO.
.program spi_cpha1
.side_set 1
.wrap_target
    pull ifempty block side 0
    out pins, 1        side 1 [3]   ; leading edge: MOSI changes
    in pins, 1         side 0 [2]   ; trailing edge: sample MISO
.wrap
