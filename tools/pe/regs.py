# SPDX-License-Identifier: Apache-2.0
"""Register map of the protocol emulator. Mirrors docs/spec.md."""

# Global registers
ID = 0x00
VERSION = 0x01
CTRL = 0x02
CMD = 0x03
FLAGS = 0x04
FLAG_SET = 0x05
IRQ_MASK = 0x06
STICKY = 0x07
STICKY_MASK = 0x08
HOSTCFG = 0x09
IMEM_ADDR = 0x0A
IMEM_DATA = 0x0B
G2_ADDR = 0x0C
G2_DATA = 0x0D
G2_CTRL = 0x0E
G2_IN0 = 0x0F
G2_IN1 = 0x10
G2_STATE = 0x11
NEGSEL = 0x12
DBG_SEL = 0x13
PINS_L = 0x14
PINS_H = 0x15
COUNTER = 0x16
PINCFG = 0x20  # PINCFG0..PINCFG12 at 0x20..0x2C

ID_VALUE = 0x50
VERSION_VALUE = 0x01

# CMD bits
CMD_RESTART0 = 1 << 0
CMD_RESTART1 = 1 << 1
CMD_STEP0 = 1 << 2
CMD_STEP1 = 1 << 3
CMD_CLEAR0 = 1 << 4
CMD_CLEAR1 = 1 << 5
CMD_DIVSYNC = 1 << 6

# STICKY bits
STK_RX_OVF0 = 1 << 0
STK_RX_OVF1 = 1 << 1
STK_TX_OVF0 = 1 << 2
STK_TX_OVF1 = 1 << 3
STK_RX_UNF0 = 1 << 4
STK_RX_UNF1 = 1 << 5
STK_CAP_OVR0 = 1 << 6
STK_CAP_OVR1 = 1 << 7

# Engine register offsets
ENGINE_BASE = (0x40, 0x60)
CLKDIV = 0x00
WRAP_BOTTOM = 0x02
WRAP_TOP = 0x03
SHIFTCTRL = 0x04
THRESH = 0x05
OUTPIN = 0x06
SETPIN = 0x07
INPIN = 0x08
SIDEPIN = 0x09
EXECCFG = 0x0A
STATUSCFG = 0x0B
LFSRCFG = 0x0C
CAPCFG = 0x0D
CAPFLAG = 0x0E
INSTR = 0x0F
DBG = 0x0F
FIFO = 0x10
LEVELS = 0x11
RAM_ADDR = 0x12
RAM_DATA = 0x13
LFSR_POLY = 0x14
LFSR_VALUE = 0x18
CAPTURE = 0x1C

# SHIFTCTRL fields
AUTOPUSH = 1 << 0
AUTOPULL = 1 << 1
IN_RIGHT = 1 << 2
OUT_RIGHT = 1 << 3


def fifo_mode(mode):
    return (mode & 7) << 4


FIFO_SPLIT = 0
FIFO_JOIN_TX = 1
FIFO_JOIN_RX = 2
FIFO_TX_RAM = 3
FIFO_RX_RAM = 4
FIFO_RAM = 5

# PINCFG fields
OWNER_E0 = 0
OWNER_E1 = 1
OWNER_G2_OUT0 = 2
OWNER_G2_OUT1 = 3
DRIVE_OFF = 0 << 2
DRIVE_PUSH_PULL = 1 << 2
DRIVE_OPEN_DRAIN = 2 << 2
DRIVE_ALWAYS = 3 << 2
PIN_INVERT = 1 << 4
PIN_BYPASS = 1 << 5


def pin_filter(code):
    """code 1, 2, 3 selects a 2, 4 or 8 cycle glitch filter."""
    return (code & 3) << 6


# LFSRCFG fields
LFSR_FEED_OUT = 1
LFSR_FEED_IN = 2
LFSR_CRC = 0 << 2
LFSR_SCRAMBLE = 1 << 2
LFSR_DESCRAMBLE = 2 << 2
LFSR_ADDITIVE = 3 << 2
LFSR_RIGHT = 1 << 4

# G2_CTRL fields
G2_ENABLE = 1 << 0
G2_OWNER1 = 1 << 1
G2_STEP_CYCLE = 0 << 2
G2_STEP_TICK = 1 << 2
G2_STEP_FEED = 2 << 2
G2_EMIT = 1 << 4
G2_SRC_FEED_BIT = 16
G2_SRC_FEED_FULL = 17

# Pin space
PIN_G2_OUT0 = 13
PIN_G2_OUT1 = 14
PIN_NEG = 15


def engine_reg(engine, offset):
    return ENGINE_BASE[engine] + offset


def is_port(addr):
    if addr in (IMEM_DATA, G2_DATA):
        return True
    return addr >= 0x40 and (addr & 0x1F) in (INSTR, FIFO, RAM_DATA)
