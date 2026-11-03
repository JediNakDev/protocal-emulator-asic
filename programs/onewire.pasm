.program onewire
; 1-Wire controller, standard speed: reset with presence detect, and write
; and read time slots, 1 to 8 per transmit word.
; Pins: DQ = OUTPIN, SETPIN and INPIN base, open-drain with an external
;   pull-up, released in the pin register before enabling.
; Config: `out` and `in` shifting right; autopull and autopush off.
;   CLKDIV 149 at 50 MHz: 3 us ticks. G1 on engine `in` bits, right, CRC,
;   polynomial 0x8C, gives the Maxim CRC8 of everything sampled; the Host
;   clears LFSR_VALUE after a command byte and reads 0 after a good ROM code.
; Transmit words:
;   bit 0      1 for reset and presence detect; the other bits are ignored
;   bits 3:1   time slots in this word, less one
;   bits 11:4  the level of each slot, first slot in bit 4: to write, the
;              bits of the byte, least significant first; to read, ones
; Receive words, one per transmit word:
;   after a reset: bit 14 the level 69 us after release (0 = presence), bit
;     15 the level at the end of the recovery (0 = bus stuck low)
;   after slots: the level sampled in each slot, last slot in bit 15, so n
;     slots are in bits 15:(16 - n); these also echo the bits written
; Timing, in ticks: reset low 162 (486 us), then 160 before the end sample
;   (480 us). A slot is 24 (72 us): low 2 (6 us) for a 1 or read, 21 (63 us)
;   for a 0, sample at 4 (12 us), recovery 3 (9 us). Between words DQ stays
;   released, as long as the Host needs.
.wrap_target
    pull block
    out x, 1
    jmp !x slots
    set y, 31
    set pins, 0 [1]            ; reset: DQ low
low:
    jmp y-- low [4]
    set pins, 1 [22]           ; release; the device answers
    in pins, 1                 ; presence
    set y, 26
recovery:
    jmp y-- recovery [4]
    in pins, 1                 ; the bus must be back high
    jmp done
slots:
    out y, 3
bit:
    set pins, 0 [1]            ; every slot starts low
    out pins, 1 [1]            ; a 1 or read releases DQ here, a 0 holds it
    in pins, 1 [16]            ; sample
    set pins, 1 [1]            ; recovery
    jmp y-- bit
done:
    push
.wrap
