/*
 * Chip model: two protocol engines, the host bus on ui_in/uo_out, and the
 * uio pad logic (registered outputs, 2-FF input synchronisers).
 *
 * Engine rule: all state lives in pe_core (flip-flops). core_step() executes
 * exactly one clock cycle and keeps nothing in C variables across cycles.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "pe.h"

#include <string.h>

static uint16_t cfg16(const pe_core *c, int at) {
  return (uint16_t)(c->cfg[at] | c->cfg[at + 1] << 8);
}
static int lphys(const pe_core *c, int lp) { return c->cfg[CFG_PMAP0 + (lp & 3)] & 7; }
static int lod(const pe_core *c, int lp) { return (c->cfg[CFG_PMAP0 + (lp & 3)] >> 3) & 1; }
static int pin_in(const pe_core *c, uint8_t sync, int lp) { return (sync >> lphys(c, lp)) & 1; }

static void fifo_reset(pe_fifo *f) { memset(f, 0, sizeof *f); }
static void fifo_push(pe_fifo *f, uint8_t v) {
  f->d[f->wr] = v;
  f->wr = (f->wr + 1) % PE_FIFO;
  f->n++;
}
static uint8_t fifo_pop(pe_fifo *f) {
  uint8_t v = f->d[f->rd];
  f->rd = (f->rd + 1) % PE_FIFO;
  f->n--;
  return v;
}

static uint8_t rev8(uint8_t v) {
  uint8_t r = 0;
  for (int i = 0; i < 8; i++) r |= ((v >> i) & 1) << (7 - i);
  return r;
}

static void core_restart(pe_core *c) {
  c->pc = c->cfg[CFG_ENTRY] & (PE_IMEM - 1);
  c->x = c->y = 0;
  c->isr = c->isr_cnt = c->osr = c->osr_cnt = 0;
  c->cnt = 0;
  c->dly = 0;
  c->tickwait = false;
  c->val = c->dir = 0;
  c->flags = 0;
  c->starved = false;
  fifo_reset(&c->txf);
  fifo_reset(&c->rxf);
}

void chip_reset(pe_chip *ch) {
  memset(ch, 0, sizeof *ch);
  for (int i = 0; i < PE_CORES; i++) core_restart(&ch->core[i]);
}

/* ---- one engine, one cycle --------------------------------------------- */

typedef struct {   /* combinational write tracking for the current cycle */
  uint8_t val, dir;
} wtrack;

static void wval(board *b, pe_core *c, int ci, wtrack *w, int lp, int v) {
  lp &= 3;
  if (w->val & (1 << lp))
    board_error(b, "core%d pc=%d: logical pin %d written twice in one cycle", ci, c->pc, lp);
  w->val |= 1 << lp;
  c->val = (uint8_t)((c->val & ~(1 << lp)) | ((v & 1) << lp));
}
static void wdir(pe_core *c, wtrack *w, int lp, int v) {
  lp &= 3;
  w->dir |= 1 << lp;
  c->dir = (uint8_t)((c->dir & ~(1 << lp)) | ((v & 1) << lp));
}

static void isr_shift(pe_core *c, uint8_t data, int n) {
  bool right = (c->cfg[CFG_MODE] >> 3) & 1;
  uint8_t mask = (uint8_t)((1u << n) - 1);
  data &= mask;
  if (n == 8)
    c->isr = data;
  else if (right)
    c->isr = (uint8_t)((c->isr >> n) | (data << (8 - n)));
  else
    c->isr = (uint8_t)((c->isr << n) | data);
  c->isr_cnt = (uint8_t)(c->isr_cnt + n > 8 ? 8 : c->isr_cnt + n);
  if (((c->cfg[CFG_MODE] >> 5) & 1) && c->isr_cnt >= 8) {
    fifo_push(&c->rxf, c->isr); /* space was checked before execution */
    c->isr = c->isr_cnt = 0;
  }
}

static uint8_t osr_take(pe_core *c, int n) {
  bool right = (c->cfg[CFG_MODE] >> 2) & 1;
  if (((c->cfg[CFG_MODE] >> 4) & 1) && c->osr_cnt == 0) {
    c->osr = fifo_pop(&c->txf); /* availability was checked before execution */
    c->osr_cnt = 8;
  }
  uint8_t d;
  if (n == 8) {
    d = c->osr;
    c->osr = 0;
  } else if (right) {
    d = c->osr & (uint8_t)((1u << n) - 1);
    c->osr = (uint8_t)(c->osr >> n);
  } else {
    d = (uint8_t)(c->osr >> (8 - n));
    c->osr = (uint8_t)(c->osr << n);
  }
  c->osr_cnt = (uint8_t)(c->osr_cnt > n ? c->osr_cnt - n : 0);
  return d;
}

static void core_step(board *b, int ci, uint8_t sync) {
  pe_core *c = &b->chip.core[ci];
  if (!c->run) return;

  /* timer: free-running down-counter, one tick every T0 cycles */
  uint16_t t0 = cfg16(c, CFG_T0), t1 = cfg16(c, CFG_T1);
  bool tick = c->cnt == 0;
  c->cnt = tick ? (uint16_t)(t0 - 1) : (uint16_t)(c->cnt - 1);
  c->starved = false;

  if (c->dly) {
    c->dly--;
    c->st_dly++;
    return;
  }
  if (c->tickwait) {
    c->st_dly++;
    if (tick) c->tickwait = false;
    return;
  }

  uint16_t w = c->imem[c->pc];
  int op = w >> 13, f = (w >> 8) & 31, a = w & 0xff;
  uint8_t mode = c->cfg[CFG_MODE], ps = c->cfg[CFG_PINSEL];
  int smode = mode & 3, outp = ps & 3, inp = (ps >> 2) & 3, sidep = (ps >> 4) & 3,
      jmpp = (ps >> 6) & 3;
  bool autopull = (mode >> 4) & 1, autopush = (mode >> 5) & 1;
  int side = -1, dl;
  if (smode == SIDE_ON) {
    side = f >> 4;
    dl = f & 15;
  } else if (smode == SIDE_OPT) {
    if (f & 16) side = (f >> 3) & 1;
    dl = f & 7;
  } else {
    dl = f;
  }
  bool dtick = dl == (smode == SIDE_NONE ? 31 : smode == SIDE_ON ? 15 : 7);
  int n = (a & 15) ? (a & 15) : 8;
  if (n > 8) n = 8;
  wtrack wt = {0, 0};

  /* ---- stall conditions (an instruction that cannot complete has no
   * effect; WAIT is the exception: its side-set is applied while waiting) */
  bool stall = false;
  switch (op) {
  case OP_WAIT:
    if (side >= 0) wval(b, c, ci, &wt, sidep, side);
    if (pin_in(c, sync, a & 3) != (a >> 7)) stall = true;
    break;
  case OP_PP:
    if ((a & 0x80) && (a & 0x20) && c->txf.n == 0) stall = c->starved = true;
    if (!(a & 0x80) && (a & 0x20) && c->rxf.n == PE_FIFO) stall = true;
    break;
  case OP_OUT:
  case OP_XCH:
  case OP_IN:
    if (op != OP_IN && autopull && c->osr_cnt == 0 && c->txf.n == 0) stall = c->starved = true;
    if (op != OP_OUT && autopush && c->isr_cnt + n >= 8 && c->rxf.n == PE_FIFO) stall = true;
    break;
  }
  if (stall) {
    c->st_stall++;
    return;
  }

  /* ---- execute --------------------------------------------------------- */
  if (side >= 0 && op != OP_WAIT) wval(b, c, ci, &wt, sidep, side);
  int wrap_top = c->cfg[CFG_WRAP_TOP] & (PE_IMEM - 1);
  int next = c->pc == wrap_top ? (c->cfg[CFG_WRAP_BOT] & (PE_IMEM - 1)) : (c->pc + 1) % PE_IMEM;

  switch (op) {
  case OP_JMP: {
    bool take = false;
    switch (a >> 5) {
    case JC_ALWAYS: take = true; break;
    case JC_NX: take = c->x == 0; break;
    case JC_XDEC: take = c->x != 0; c->x--; break;
    case JC_NY: take = c->y == 0; break;
    case JC_YDEC: take = c->y != 0; c->y--; break;
    case JC_PIN: take = pin_in(c, sync, jmpp); break;
    case JC_NPIN: take = !pin_in(c, sync, jmpp); break;
    case JC_NOSRE: take = c->osr_cnt != 0; break;
    }
    if (take) next = a & (PE_IMEM - 1);
    break;
  }
  case OP_WAIT:
    if (a & 0x40) c->cnt = (uint16_t)(t1 - 1); /* resync the timer to the edge */
    break;
  case OP_IN: {
    uint8_t d = 0;
    switch (a >> 5) {
    case IN_PINS:
      for (int i = 0; i < n; i++) d |= (uint8_t)(pin_in(c, sync, inp + i) << i);
      break;
    case IN_X: d = c->x; break;
    case IN_Y: d = c->y; break;
    case IN_ISR: d = c->isr; break;
    case IN_OSR: d = c->osr; break;
    default: d = 0; break;
    }
    isr_shift(c, d, n);
    break;
  }
  case OP_OUT: {
    uint8_t d = osr_take(c, n);
    switch (a >> 5) {
    case OUT_PINS:
      for (int i = 0; i < n; i++) wval(b, c, ci, &wt, outp + i, (d >> i) & 1);
      break;
    case OUT_PINDIRS:
      for (int i = 0; i < n; i++) wdir(c, &wt, outp + i, (d >> i) & 1);
      break;
    case OUT_X: c->x = d; break;
    case OUT_Y: c->y = d; break;
    case OUT_PC: next = d & (PE_IMEM - 1); break;
    case OUT_ISR: c->isr = d; c->isr_cnt = (uint8_t)n; break;
    default: break;
    }
    break;
  }
  case OP_XCH: {
    uint8_t s = 0;
    for (int i = 0; i < n; i++) s |= (uint8_t)(pin_in(c, sync, inp + i) << i);
    uint8_t d = osr_take(c, n);
    for (int i = 0; i < n; i++) wval(b, c, ci, &wt, outp + i, (d >> i) & 1);
    isr_shift(c, s, n);
    break;
  }
  case OP_PP:
    if (a & 0x80) { /* pull */
      if (c->txf.n) c->osr = fifo_pop(&c->txf);
      else c->osr = c->x;
      c->osr_cnt = 8;
    } else { /* push */
      if (c->rxf.n < PE_FIFO) fifo_push(&c->rxf, c->isr);
      else c->flags |= FLAG_OVF;
      c->isr = c->isr_cnt = 0;
    }
    break;
  case OP_MOV: {
    uint8_t v = 0;
    switch (a & 7) {
    case MV_PINS: v = (uint8_t)pin_in(c, sync, inp); break;
    case MV_X: v = c->x; break;
    case MV_Y: v = c->y; break;
    case MV_ISR: v = c->isr; break;
    case MV_OSR: v = c->osr; break;
    default: v = 0; break;
    }
    if (((a >> 3) & 3) == MOP_INV) v = (uint8_t)~v;
    if (((a >> 3) & 3) == MOP_REV) v = rev8(v);
    switch (a >> 5) {
    case MV_PINS: wval(b, c, ci, &wt, outp, v & 1); break;
    case MV_X: c->x = v; break;
    case MV_Y: c->y = v; break;
    case MV_PC: next = v & (PE_IMEM - 1); break;
    case MV_ISR: c->isr = v; c->isr_cnt = 0; break;
    case MV_OSR: c->osr = v; c->osr_cnt = 8; break;
    default: break;
    }
    break;
  }
  case OP_SET: {
    int d = a & 31;
    switch (a >> 5) {
    case SET_PIN: wval(b, c, ci, &wt, d & 3, (d >> 2) & 1); break;
    case SET_PINS: /* the side-set pin belongs to side-set */
      for (int i = 0; i < PE_LPINS; i++)
        if (smode == SIDE_NONE || i != sidep) wval(b, c, ci, &wt, i, (d >> i) & 1);
      break;
    case SET_PINDIRS:
      for (int i = 0; i < PE_LPINS; i++) wdir(c, &wt, i, (d >> i) & 1);
      break;
    case SET_X: c->x = (uint8_t)d; break;
    case SET_Y: c->y = (uint8_t)d; break;
    case SET_FLAG: c->flags |= (uint8_t)(d & 3); break;
    case SET_TIMER: c->cnt = (uint16_t)(((d & 1) ? t1 : t0) - 1); break;
    }
    break;
  }
  }

  /* two driven logical pins on the same uio pad written in one cycle */
  uint8_t wd = wt.val & c->dir;
  for (int i = 0; i < PE_LPINS; i++)
    for (int j = i + 1; j < PE_LPINS; j++)
      if ((wd >> i & 1) && (wd >> j & 1) && lphys(c, i) == lphys(c, j))
        board_error(b, "core%d pc=%d: uio[%d] written twice in one cycle", ci, c->pc,
                    lphys(c, i));

  c->pc = (uint8_t)next;
  if (dtick) c->tickwait = true;
  else c->dly = (uint8_t)dl;
  c->st_exec++;
}

/* ---- host bus ----------------------------------------------------------- */

static void host_cmd(pe_chip *ch, int cmd, int nib) {
  switch (cmd) {
  case HC_LO: ch->hold = (uint8_t)nib; break;
  case HC_HI: {
    uint8_t v = (uint8_t)(nib << 4 | ch->hold);
    pe_core *c = &ch->core[ch->sel & 1];
    switch (ch->sel & 0xc) {
    case SEL_TX:
      if (c->txf.n < PE_FIFO) fifo_push(&c->txf, v);
      else c->flags |= FLAG_OVF;
      break;
    case SEL_IMEM:
      if (ch->wptr < 2 * PE_IMEM) {
        uint16_t *m = &c->imem[ch->wptr >> 1];
        *m = (ch->wptr & 1) ? (uint16_t)((*m & 0xff) | v << 8) : (uint16_t)((*m & 0xff00) | v);
      }
      ch->wptr++;
      break;
    case SEL_CFG:
      if (ch->wptr < PE_CFG_BYTES) c->cfg[ch->wptr] = v;
      ch->wptr++;
      break;
    }
    break;
  }
  case HC_SEL: ch->sel = (uint8_t)nib; ch->wptr = 0; break;
  case HC_CTRL:
    for (int i = 0; i < PE_CORES; i++) {
      if ((nib >> (2 + i)) & 1) core_restart(&ch->core[i]);
      ch->core[i].run = (nib >> i) & 1;
    }
    break;
  case HC_POP: {
    pe_core *c = &ch->core[nib & 1];
    if (c->rxf.n) ch->uo = fifo_pop(&c->rxf);
    break;
  }
  case HC_STAT: ch->uo = pe_status(ch); break;
  case HC_CLRF:
    for (int i = 0; i < PE_CORES; i++)
      if ((nib >> i) & 1) ch->core[i].flags = 0;
    break;
  }
}

uint8_t pe_status(const pe_chip *ch) {
  uint8_t s = 0;
  for (int i = 0; i < PE_CORES; i++) {
    const pe_core *c = &ch->core[i];
    int v = (c->rxf.n ? ST_RXNE : 0) | (c->txf.n == PE_FIFO ? ST_TXFULL : 0) |
            ((!c->run || (c->starved && c->txf.n == 0)) ? ST_IDLE : 0) | (c->flags ? ST_FLAG : 0);
    s |= (uint8_t)(v << (4 * i));
  }
  return s;
}

/* ---- one chip clock ----------------------------------------------------- */

void chip_step(board *b) {
  pe_chip *ch = &b->chip;
  uint8_t ui = ch->ui_q2, uio = ch->uio_q2;

  if ((ui >> 7) != ch->t_prev) {
    ch->t_prev = ui >> 7;
    host_cmd(ch, (ui >> 4) & 7, ui & 15);
  }
  for (int i = 0; i < PE_CORES; i++) core_step(b, i, uio);

  /* pad outputs (registered: visible on the wire next cycle) */
  uint8_t out = 0, oe = 0, owner[8];
  memset(owner, 0xff, sizeof owner);
  for (int i = 0; i < PE_CORES; i++) {
    const pe_core *c = &ch->core[i];
    for (int lp = 0; lp < PE_LPINS; lp++) {
      int p = lphys(c, lp), v = (c->val >> lp) & 1, d = (c->dir >> lp) & 1;
      int e = lod(c, lp) ? (d && !v) : d, o = lod(c, lp) ? 0 : v;
      if (!e) continue;
      if ((oe >> p) & 1) {
        if (owner[p] != i || ((out >> p) & 1) != o)
          board_error(b, "uio[%d] driven by core%d and core%d/lp", p, owner[p], i);
      }
      oe |= (uint8_t)(1 << p);
      out = (uint8_t)((out & ~(1 << p)) | (o << p));
      owner[p] = (uint8_t)i;
    }
  }
  ch->uio_out = out;
  ch->uio_oe = oe;

  ch->ui_q2 = ch->ui_q1;
  ch->ui_q1 = b->ui_in;
  ch->uio_q2 = ch->uio_q1;
  ch->uio_q1 = b->wire;
}
