/*
 * Peer models and protocol checkers. See peers.h.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "peers.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ======================================================================== */
/* UART                                                                      */
/* ======================================================================== */

static void uart_step(peer *pp, board *b, uint8_t wire) {
  uart_peer *u = (uart_peer *)pp;
  uint64_t c = b->cycle;

  if (u->rx_pin >= 0) {
    uint8_t v = (wire >> u->rx_pin) & 1;
    if (u->rst == 0) {
      if (u->rprev && !v) {
        u->rst = 1;
        u->rstart = c;
        u->rbit = 0;
        u->rbyte = 0;
      }
    } else {
      if (v != u->rprev) { /* edges must sit on the bit grid */
        double e = (double)(c - u->rstart);
        double k = floor(e / u->rx_period + 0.5);
        double d = fabs(e - k * u->rx_period);
        if (d > u->max_dev) u->max_dev = d;
      }
      if (c == u->rstart + (uint64_t)((u->rbit + 0.5) * u->rx_period)) {
        if (u->rbit == 0 && v) {
          u->rst = 0; /* glitch */
        } else if (u->rbit >= 1 && u->rbit <= 8) {
          u->rbyte |= (uint8_t)(v << (u->rbit - 1));
        } else if (u->rbit == 9) {
          if (!v) u->ferr++;
          else {
            if (u->nrx < (int)sizeof u->rx) u->rx[u->nrx++] = u->rbyte;
            if (u->echo) uart_peer_send(u, u->rbyte, false);
          }
          u->rst = 0;
        }
        u->rbit++;
      }
    }
    u->rprev = v;
  }

  if (u->tx_pin >= 0) {
    int level = 1;
    if (!u->tbusy && u->txh != u->txt && c >= u->tnext) {
      u->tcur = u->txq[u->txh];
      u->txh = (u->txh + 1) % 1024;
      u->tbusy = true;
      u->tstart = c + 1;
    }
    if (u->tbusy) {
      int bi = (int)floor((double)(c + 1 - u->tstart) / u->tx_period);
      if (bi == 0) level = 0;
      else if (bi <= 8) level = (u->tcur >> (bi - 1)) & 1;
      else if (bi == 9) level = (u->tcur & 0x100) ? 0 : 1;
      else {
        u->tbusy = false;
        u->tnext = c + (uint64_t)u->gap;
      }
    }
    u->base.oe = (uint8_t)(1 << u->tx_pin);
    u->base.out = (uint8_t)(level << u->tx_pin);
  }
}

void uart_peer_send(uart_peer *u, uint8_t v, bool bad_stop) {
  u->txq[u->txt] = (uint16_t)(v | (bad_stop ? 0x100 : 0));
  u->txt = (u->txt + 1) % 1024;
}

void uart_peer_init(uart_peer *u, int rx_pin, int tx_pin, double rx_period, double tx_period) {
  memset(u, 0, sizeof *u);
  u->base.name = "uart_peer";
  u->base.step = uart_step;
  u->rx_pin = rx_pin;
  u->tx_pin = tx_pin;
  u->rx_period = rx_period;
  u->tx_period = tx_period;
  u->rprev = 1;
  if (tx_pin >= 0) {
    u->base.oe = (uint8_t)(1 << tx_pin);
    u->base.out = (uint8_t)(1 << tx_pin);
  }
}

/* ======================================================================== */
/* SPI NOR flash: W25Q128JV                                                  */
/* ======================================================================== */

#define FLASH_SIZE (16u << 20)

static uint8_t flash_next_out(spi_flash *f, uint8_t in, int idx) {
  /* returns the byte to shift out during byte idx+1, after byte idx came in */
  static const uint8_t jedec[3] = {0xEF, 0x40, 0x18};
  f->outen = true;
  switch (f->cmd) {
  case 0x9F: return idx < 3 ? jedec[idx] : 0x00;
  case 0x05: return (uint8_t)((f->busy_until > 0 ? 1 : 0) | (f->wel ? 2 : 0));
  case 0x90: return (idx >= 3) ? ((idx - 3) % 2 == 0 ? 0xEF : 0x17) : 0xFF;
  case 0x03:
  case 0x0B: {
    int first = f->cmd == 0x03 ? 3 : 4;
    if (idx >= 1 && idx <= 3) f->addr = (f->addr << 8 | in) & (FLASH_SIZE - 1);
    if (idx >= first) {
      uint8_t v = f->mem[f->addr];
      f->addr = (f->addr + 1) & (FLASH_SIZE - 1);
      return v;
    }
    break;
  }
  case 0x02:
    if (idx >= 1 && idx <= 3) f->addr = (f->addr << 8 | in) & (FLASH_SIZE - 1);
    if (idx == 3) f->paddr = f->addr;
    if (idx >= 4) {
      uint8_t o = (uint8_t)((f->paddr + idx - 4) & 0xff);
      f->page[o] = in;
      f->pmask[o] = true;
    }
    break;
  case 0x20:
    if (idx >= 1 && idx <= 3) f->addr = (f->addr << 8 | in) & (FLASH_SIZE - 1);
    break;
  }
  f->outen = false;
  return 0xFF;
}

static void flash_step(peer *pp, board *b, uint8_t wire) {
  spi_flash *f = (spi_flash *)pp;
  uint64_t c = b->cycle;
  uint8_t cs = (wire >> f->cs) & 1, sck = (wire >> f->sck) & 1, mosi = (wire >> f->mosi) & 1;
  if (f->busy_until && c >= f->busy_until) f->busy_until = 0;

  if (f->pcs && !cs) { /* select */
    f->frames++;
    f->nbyte = f->bitc = 0;
    f->outen = false;
    f->mode = sck ? 3 : 0;
    if (f->cs_rise && (int)(c - f->cs_rise) < f->min_cs_high) f->min_cs_high = (int)(c - f->cs_rise);
    f->last_rise = f->last_fall = 0;
    f->cs_fall = c;
    memset(f->pmask, 0, sizeof f->pmask);
  }
  if (!f->pcs && cs) { /* deselect: execute write commands */
    if (f->bitc) board_error(b, "flash: CS rose after %d bits of a byte", f->bitc);
    bool busy = f->busy_until != 0;
    if (!busy && f->nbyte == 1 && f->cmd == 0x06) f->wel = true;
    if (!busy && f->nbyte == 1 && f->cmd == 0x04) f->wel = false;
    if (!busy && f->cmd == 0x02 && f->nbyte >= 5 && f->wel) {
      uint32_t base = f->paddr & ~0xffu;
      for (int i = 0; i < 256; i++)
        if (f->pmask[i]) f->mem[base + i] &= f->page[i];
      f->wel = false;
      f->busy_until = c + (uint64_t)f->tpp;
    }
    if (!busy && f->cmd == 0x20 && f->nbyte == 4 && f->wel) {
      memset(f->mem + (f->addr & ~0xfffu), 0xff, 4096);
      f->wel = false;
      f->busy_until = c + (uint64_t)f->tse;
    }
    f->outen = false;
    f->cs_rise = c;
  }
  if (!cs) {
    if (!f->psck && sck) {
      if (mosi != f->pmosi) board_error(b, "flash: MOSI changed on the SCK rising edge");
      if (f->last_rise && (int)(c - f->last_rise) < f->min_period) f->min_period = (int)(c - f->last_rise);
      if (f->last_rise && (int)(c - f->last_rise) > f->max_period) f->max_period = (int)(c - f->last_rise);
      if (f->last_fall && (int)(c - f->last_fall) < f->min_low) f->min_low = (int)(c - f->last_fall);
      f->last_rise = c;
      f->rises++;
      f->sin = (uint8_t)(f->sin << 1 | mosi);
      if (++f->bitc == 8) {
        f->bitc = 0;
        if (f->nbyte == 0) f->cmd = f->sin;
        bool busy = f->busy_until != 0;
        if (busy && f->cmd != 0x05) f->cmd = 0; /* ignored while busy */
        f->sout = flash_next_out(f, f->sin, f->nbyte);
        f->nbyte++;
        f->bytes++;
      }
    }
    if (f->psck && !sck) {
      if (f->last_rise && (int)(c - f->last_rise) < f->min_high) f->min_high = (int)(c - f->last_rise);
      f->last_fall = c;
      if (f->outen) {
        f->miso_v = (f->sout >> 7) & 1;
        f->sout = (uint8_t)(f->sout << 1);
      }
    }
  }
  /* MISO through an optional extra delay line (slower part / longer wires) */
  f->hist = (uint16_t)(f->hist << 2 | (!cs && f->outen) << 1 | f->miso_v);
  int d = (f->hist >> (2 * f->extra_lat)) & 3;
  f->base.oe = (uint8_t)((d >> 1) << f->miso);
  f->base.out = (uint8_t)((d & 1) << f->miso);
  f->pcs = cs;
  f->psck = sck;
  f->pmosi = mosi;
}

void spi_flash_init(spi_flash *f, int cs, int mosi, int miso, int sck) {
  memset(f, 0, sizeof *f);
  f->base.name = "w25q128";
  f->base.step = flash_step;
  f->cs = cs;
  f->mosi = mosi;
  f->miso = miso;
  f->sck = sck;
  f->mem = malloc(FLASH_SIZE);
  memset(f->mem, 0xff, FLASH_SIZE);
  f->pcs = 1;
  f->min_period = f->min_high = f->min_low = f->min_cs_high = 1 << 30;
  f->tpp = PE_NS(400000);    /* 0.4 ms typical page program */
  f->tse = PE_NS(45000000);  /* 45 ms typical 4 KB sector erase */
}

/* ======================================================================== */
/* I2C target: ADT7420 + Fast-mode timing checker                            */
/* ======================================================================== */

enum { I_IDLE, I_ADDR, I_WDATA, I_RDATA, I_ACKOUT, I_ACKIN, I_IGNORE };


static void tmin(int *m, uint64_t a, uint64_t b) {
  int d = (int)(a - b);
  if (d < *m) *m = d;
}

static void i2c_write_reg(i2c_adt7420 *d, uint8_t v) {
  uint8_t r = d->ptr;
  if (r == 0x2F) {
    d->regs[0x03] = 0;
  } else if (r < 0x30 && r != 0x00 && r != 0x01 && r != 0x02 && r != 0x0B) {
    d->regs[r] = v;
  }
  d->ptr++;
}

static void i2c_step(peer *pp, board *b, uint8_t wire) {
  i2c_adt7420 *d = (i2c_adt7420 *)pp;
  uint64_t c = b->cycle;
  uint8_t scl = (wire >> d->scl) & 1, sda = (wire >> d->sda) & 1;

  if (sda != d->psda) {
    if (scl && d->pscl) {
      if (!sda) { /* START */
        /* a repeated START follows one SCL rise; later ones are mid-byte */
        if (d->bit >= 2 && (d->st == I_ADDR || d->st == I_WDATA || d->st == I_RDATA))
          board_error(b, "i2c: START inside a byte");
        d->starts++;
        if (d->t_rise) tmin(&d->min_susta, c, d->t_rise);
        if (d->t_stop) tmin(&d->min_buf, c, d->t_stop);
        d->t_start = c;
        d->after_start = true;
        d->st = I_ADDR;
        d->bit = 0;
        d->sh = 0;
        d->sda_low = false;
      } else { /* STOP */
        if (d->bit >= 2 && (d->st == I_ADDR || d->st == I_WDATA || d->st == I_RDATA))
          board_error(b, "i2c: STOP inside a byte");
        d->stops++;
        if (d->t_rise) tmin(&d->min_susto, c, d->t_rise);
        d->t_stop = c;
        d->st = I_IDLE;
        d->sda_low = false;
      }
    } else {
      d->t_sda = c;
      d->sda_moved = true;
    }
  }

  if (!d->pscl && scl) { /* rising */
    if (d->t_fall) tmin(&d->min_low, c, d->t_fall);
    if (d->sda_moved && d->t_fall) tmin(&d->min_sudat, c, d->t_sda);
    if (d->last_rise_p && !d->stretched) tmin(&d->min_period, c, d->last_rise_p);
    d->last_rise_p = c;
    d->stretched = false;
    d->t_rise = c;
    switch (d->st) {
    case I_ADDR:
    case I_WDATA:
      d->sh = (uint8_t)(d->sh << 1 | sda);
      d->bit++;
      break;
    case I_RDATA: d->bit++; break;
    case I_ACKOUT: d->rose = true; break;
    case I_ACKIN: d->rose = true; d->mack = sda; break;
    }
  }

  if (d->pscl && !scl) { /* falling */
    if (d->t_rise) tmin(&d->min_high, c, d->t_rise);
    if (d->after_start) {
      tmin(&d->min_hdsta, c, d->t_start);
      d->after_start = false;
    }
    d->t_fall = c;
    d->sda_moved = false;
    switch (d->st) {
    case I_ADDR:
      if (d->bit == 8) {
        if ((d->sh >> 1) == d->addr) {
          d->rw = d->sh & 1;
          d->sda_low = true;
          d->st = I_ACKOUT;
          d->rose = false;
          d->nextst = d->rw ? I_RDATA : I_WDATA;
          d->nwrite = 0;
        } else {
          d->st = I_IGNORE;
        }
      }
      break;
    case I_WDATA:
      if (d->bit == 8) {
        if (d->nwrite++ == 0) d->ptr = d->sh;
        else i2c_write_reg(d, d->sh);
        d->bytes++;
        d->sda_low = true;
        d->st = I_ACKOUT;
        d->rose = false;
        d->nextst = I_WDATA;
      }
      break;
    case I_ACKOUT:
      if (d->rose) {
        d->sda_low = false;
        d->bit = 0;
        d->sh = 0;
        if (d->nextst == I_RDATA) {
          d->tx = d->regs[d->ptr++ % 0x30];
          d->sda_low = !(d->tx & 0x80);
          d->st = I_RDATA;
        } else {
          d->st = I_WDATA;
        }
        if (d->stretch) {
          d->stretch_until = c + (uint64_t)d->stretch;
          d->scl_low = true;
          d->nstretch++;
          d->stretched = true;
        }
      }
      break;
    case I_RDATA:
      if (d->bit < 8) {
        d->sda_low = !((d->tx >> (7 - d->bit)) & 1);
      } else {
        d->sda_low = false;
        d->bytes++;
        d->st = I_ACKIN;
        d->rose = false;
      }
      break;
    case I_ACKIN:
      if (d->rose) {
        if (!d->mack) {
          d->tx = d->regs[d->ptr++ % 0x30];
          d->bit = 0;
          d->sda_low = !(d->tx & 0x80);
          d->st = I_RDATA;
        } else {
          d->st = I_IGNORE;
        }
      }
      break;
    }
  }

  if (d->scl_low && c >= d->stretch_until) d->scl_low = false;
  d->base.oe = (uint8_t)((d->sda_low ? 1 << d->sda : 0) | (d->scl_low ? 1 << d->scl : 0));
  d->base.out = 0;
  d->pscl = scl;
  d->psda = sda;
}

void i2c_adt7420_init(i2c_adt7420 *d, int scl, int sda, uint8_t addr) {
  memset(d, 0, sizeof *d);
  d->base.name = "adt7420";
  d->base.step = i2c_step;
  d->scl = scl;
  d->sda = sda;
  d->addr = addr;
  d->pscl = d->psda = 1;
  /* power-on register values, 25.0 C */
  d->regs[0x00] = 0x0C;
  d->regs[0x01] = 0x80;
  d->regs[0x04] = 0x20;
  d->regs[0x06] = 0x05;
  d->regs[0x08] = 0x49;
  d->regs[0x09] = 0x80;
  d->regs[0x0A] = 0x05;
  d->regs[0x0B] = 0xCB;
  d->min_low = d->min_high = d->min_sudat = d->min_hdsta = d->min_susta = d->min_susto =
      d->min_buf = d->min_period = 1 << 30;
}
