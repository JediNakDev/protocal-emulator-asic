; SPI controller, mode 0, MSB first, SCK = clk/4 (12.5 MHz at 50 MHz).
; Host frame: [nbytes-1] then nbytes data bytes; one byte comes back per byte
; sent. CS is asserted for the whole frame.
;
; MISO for bit k is sampled by the xch that drives bit k+1, i.e. on the cycle
; SCK falls again. The first bit of a frame is sent with a plain out (nothing
; to sample yet) and the last bit is collected by the trailing "in".
; Every bit is 4 cycles, including byte boundaries: no inter-byte gap unless
; a FIFO runs dry, in which case SCK is held high (legal for SPI).
.program spi_master
.pin sck 0
.pin mosi 1
.pin miso 2
.pin cs 3
.side_set sck
.out mosi
.in miso
.shift out left
.shift in left
.autopull
.autopush
    set pins, 0b1000     side 0     ; CS high, MOSI low, SCK low
    set pindirs, 0b1011  side 0
.wrap_target
    out y, 8             side 0     ; y = nbytes - 1
    set pin cs, 0        side 0 [1]
    out pins, 1          side 0 [1] ; first bit
    set x, 5             side 1 [1]
bit:
    xch                  side 0 [1] ; next bit out, previous bit in
    jmp x-- bit          side 1 [1]
    xch                  side 0     ; last bit of the byte
    set x, 6             side 0
    jmp y-- bit          side 1 [1]
    in pins, 1           side 0 [1] ; collect the final bit
    set pin cs, 1        side 0 [3]
.wrap
