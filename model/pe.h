/*
 * Cycle-accurate C model of the programmable protocol engine (PE) on a
 * Tiny Tapeout 6x4 tile, plus the board it sits on (pull-ups, peers, host).
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#ifndef PE_H
#define PE_H

#include <stdbool.h>
#include <stdint.h>

/* ---- Architectural limits (the numbers the RTL must implement) ---------- */
#define PE_CORES 2       /* engines on the chip                              */
#define PE_IMEM 32       /* 16-bit instruction words per engine              */
#define PE_LPINS 4       /* logical pins per engine, mapped onto uio[7:0]    */
#define PE_FIFO 4        /* entries per FIFO (host->core TX, core->host RX)  */
#define PE_CFG_BYTES 13  /* configuration bytes per engine                   */
#ifndef PE_CLK_HZ
#define PE_CLK_HZ 50000000.0 /* override with -DPE_CLK_HZ=... */
#endif
#define PE_NS(ns) ((int)((ns) * (PE_CLK_HZ / 1e9) + 0.999)) /* ns -> cycles, rounded up */

/* ---- Instruction encoding ----------------------------------------------
 *  [15:13] opcode  [12:8] delay / side-set field  [7:0] arguments
 */
enum { OP_JMP, OP_WAIT, OP_IN, OP_OUT, OP_XCH, OP_PP, OP_MOV, OP_SET };

/* JMP [7:5] condition, [4:0] address */
enum { JC_ALWAYS, JC_NX, JC_XDEC, JC_NY, JC_YDEC, JC_PIN, JC_NPIN, JC_NOSRE };

/* WAIT [7] polarity, [6] resync timer on completion, [1:0] logical pin */

/* IN [7:5] source, [3:0] bit count (0 = 8) */
enum { IN_PINS = 0, IN_X = 1, IN_Y = 2, IN_NULL = 3, IN_ISR = 6, IN_OSR = 7 };

/* OUT [7:5] destination, [3:0] bit count (0 = 8) */
enum { OUT_PINS = 0, OUT_X = 1, OUT_Y = 2, OUT_NULL = 3, OUT_PINDIRS = 4,
       OUT_PC = 5, OUT_ISR = 6 };

/* XCH [3:0] bit count (0 = 8): OUT pins,n and IN pins,n in the same cycle */

/* PUSH/PULL [7] 1 = pull, [5] block */

/* MOV [7:5] destination, [4:3] operation, [2:0] source */
enum { MV_PINS = 0, MV_X = 1, MV_Y = 2, MV_NULL = 3, MV_PC = 5, MV_ISR = 6,
       MV_OSR = 7 };
enum { MOP_NONE, MOP_INV, MOP_REV };

/* SET [7:5] destination, [4:0] data */
enum { SET_PIN, SET_PINS, SET_PINDIRS, SET_X, SET_Y, SET_FLAG, SET_TIMER };

/* Delay / side-set field modes (cfg MODE[1:0]) */
enum { SIDE_NONE, SIDE_ON, SIDE_OPT };

/* ---- Per-engine configuration bytes (written by the host) -------------- */
enum {
  CFG_PMAP0 = 0,      /* 0..3: [2:0] uio index, [3] open-drain emulation   */
  CFG_PINSEL = 4,     /* [1:0] out, [3:2] in, [5:4] side, [7:6] jmp pin    */
  CFG_MODE = 5,       /* [1:0] side mode, [2] out LSB-first, [3] in LSB-first,
                         [4] autopull, [5] autopush                         */
  CFG_WRAP_BOT = 6,
  CFG_WRAP_TOP = 7,
  CFG_T0 = 8,         /* 16-bit timer period (ticks every T0 cycles)       */
  CFG_T1 = 10,        /* 16-bit first period after a resync / set timer,1  */
  CFG_ENTRY = 12,     /* PC after restart                                  */
};

/* ---- Host bus on ui_in / uo_out ----------------------------------------
 *  ui_in[7] toggle strobe, ui_in[6:4] command, ui_in[3:0] nibble
 */
enum {
  HC_LO = 0,    /* hold = nibble                                           */
  HC_HI = 1,    /* byte = {nibble, hold} -> selected target, pointer++     */
  HC_SEL = 2,   /* nibble[3:2] 0 TX FIFO, 1 IMEM, 2 CFG; nibble[0] core    */
  HC_CTRL = 3,  /* nibble[1:0] run mask, [3:2] restart mask                */
  HC_POP = 4,   /* nibble[0] core: uo_out = RX FIFO head (popped)          */
  HC_STAT = 5,  /* uo_out = status byte                                    */
  HC_CLRF = 6,  /* nibble[1:0] clear sticky flags of core 0/1              */
};
#define SEL_TX 0x0
#define SEL_IMEM 0x4
#define SEL_CFG 0x8

/* Status nibble per core (core 0 in [3:0], core 1 in [7:4]) */
#define ST_RXNE 0x1  /* RX FIFO not empty                                  */
#define ST_TXFULL 0x2
#define ST_IDLE 0x4  /* stalled on an empty TX FIFO (pull / autopull)      */
#define ST_FLAG 0x8  /* any sticky flag (set flag / RX overflow)           */

#define FLAG_USER 0x1
#define FLAG_OVF 0x2

/* ---- Engine state: every bit here is a flip-flop in the RTL ------------ */
typedef struct {
  uint8_t d[PE_FIFO];
  uint8_t rd, wr, n;
} pe_fifo;

typedef struct {
  /* storage */
  uint16_t imem[PE_IMEM];
  uint8_t cfg[PE_CFG_BYTES];
  pe_fifo txf, rxf;
  /* architectural registers */
  uint8_t pc;
  uint8_t x, y;
  uint8_t isr, isr_cnt;
  uint8_t osr, osr_cnt;
  uint16_t cnt;      /* the timer                                          */
  uint8_t dly;       /* remaining delay cycles                             */
  bool tickwait;     /* waiting for the next timer tick                    */
  uint8_t val, dir;  /* logical pin output values and directions           */
  uint8_t flags;     /* sticky flags                                       */
  bool run;
  bool starved;      /* last cycle stalled on an empty TX FIFO             */
  /* statistics (not hardware) */
  uint64_t st_exec, st_stall, st_dly;
} pe_core;

typedef struct {
  pe_core core[PE_CORES];
  /* host interface */
  uint8_t ui_q1, ui_q2;   /* 2-FF synchroniser on ui_in                     */
  uint8_t t_prev;
  uint8_t hold, sel, wptr;
  uint8_t uo;             /* uo_out register                               */
  uint8_t run_mask;
  /* protocol pins */
  uint8_t uio_q1, uio_q2; /* 2-FF synchroniser on uio_in                    */
  uint8_t uio_out, uio_oe;/* registered pad outputs                        */
} pe_chip;

/* ---- Board ------------------------------------------------------------- */
struct board;
typedef struct peer {
  const char *name;
  void (*step)(struct peer *p, struct board *b, uint8_t wire);
  void (*report)(struct peer *p);
  uint8_t oe, out;        /* drive for the next cycle                      */
} peer;

#define MAX_PEERS 6
typedef struct board {
  pe_chip chip;
  uint64_t cycle;
  uint8_t ui_in;          /* driven by the host                            */
  uint8_t host_q1, host_q2; /* host-side synchroniser on uo_out            */
  uint8_t wire;           /* resolved uio levels this cycle                */
  peer *peers[MAX_PEERS];
  int npeers;
  int errors;
  bool quiet;
} board;

/* chip.c */
void chip_reset(pe_chip *c);
void chip_step(board *b);
uint8_t pe_status(const pe_chip *c);

/* board.c */
void board_init(board *b);
void board_add_peer(board *b, peer *p);
void board_step(board *b);
void board_run(board *b, uint64_t n);
void board_error(board *b, const char *fmt, ...);

/* asm.c */
typedef struct {
  char name[32];
  uint16_t code[PE_IMEM];
  int len;
  uint8_t cfg[PE_CFG_BYTES];   /* PMAP od bits, PINSEL, MODE, WRAP, ENTRY  */
  char pin[PE_LPINS][32];      /* logical pin names                        */
} pe_prog;
int pe_assemble(const char *src, pe_prog *out, char *err, int errlen);
int pe_assemble_file(const char *path, pe_prog *out);
void pe_disasm(const pe_prog *p, uint16_t w, char *buf, int len);

#endif
