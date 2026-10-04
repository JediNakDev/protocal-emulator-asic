/*
 * RP2040 host model. See host.h and HC_* in pe.h for the bus.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "host.h"

#include <stdio.h>

/* cycles from the strobe toggle until uo_out is visible to the host:
 * chip synchroniser (2) + command (1) + host synchroniser (2) - steps done */
#define HOST_LAT 3

void host_init(host *h, board *b, int edge) {
  h->b = b;
  h->edge = edge;
  h->sel = -1;
  h->run = 0;
  h->bus_ops = 0;
}

void host_wait(host *h, uint64_t cycles) { board_run(h->b, cycles); }

static void nib(host *h, int cmd, int v) {
  board *b = h->b;
  b->ui_in = (uint8_t)((b->ui_in & 0x80) | (cmd & 7) << 4 | (v & 15));
  board_run(b, (uint64_t)h->edge);
  b->ui_in ^= 0x80;
  board_run(b, (uint64_t)h->edge);
  h->bus_ops++;
}

static void sel(host *h, int target) {
  if (h->sel == target) return;
  nib(h, HC_SEL, target);
  h->sel = target;
}

static void wbyte(host *h, uint8_t v) {
  nib(h, HC_LO, v & 15);
  nib(h, HC_HI, v >> 4);
}

uint8_t host_est(host *h, int eng) {
  nib(h, HC_STAT, eng >> 1);
  board_run(h->b, HOST_LAT);
  return (uint8_t)((h->b->host_q2 >> (4 * (eng & 1))) & 15);
}

void host_ctrl(host *h, uint8_t run, uint8_t restart) {
  h->run = run & 15;
  nib(h, HC_LO, restart & 15);
  nib(h, HC_CTRL, run & 15);
}

void host_restart(host *h, int eng) { host_ctrl(h, h->run, (uint8_t)(1 << eng)); }

void host_clear_flags(host *h, int mask) { nib(h, HC_CLRF, mask & 15); }

void host_load(host *h, int eng, const pe_prog *p, const int phys[PE_LPINS], uint16_t t0,
               uint16_t t1) {
  /* Release the old outputs before remapping pins one config byte at a time. */
  host_ctrl(h, (uint8_t)(h->run & ~(1 << eng)), (uint8_t)(1 << eng));
  nib(h, HC_SEL, SEL_IMEM | eng);
  for (int i = 0; i < PE_IMEM; i++) {
    uint16_t w = i < p->len ? p->code[i] : 0;
    wbyte(h, w & 0xff);
    wbyte(h, w >> 8);
  }
  uint8_t cfg[PE_CFG_BYTES];
  for (int i = 0; i < PE_CFG_BYTES; i++) cfg[i] = p->cfg[i];
  for (int i = 0; i < PE_LPINS; i++)
    cfg[CFG_PMAP0 + i] = (uint8_t)((p->cfg[CFG_PMAP0 + i] & (PMAP_OD | PMAP_EN)) | (phys[i] & 7));
  cfg[CFG_T0] = t0 & 0xff;
  cfg[CFG_T0 + 1] = t0 >> 8;
  cfg[CFG_T1] = t1 & 0xff;
  cfg[CFG_T1 + 1] = t1 >> 8;
  nib(h, HC_SEL, SEL_CFG | eng);
  for (int i = 0; i < PE_CFG_BYTES; i++) wbyte(h, cfg[i]);
  h->sel = -1;
  host_ctrl(h, (uint8_t)(h->run | 1 << eng), (uint8_t)(1 << eng));
}

void host_write_tx(host *h, int eng, uint8_t v) {
  sel(h, SEL_TX | eng);
  wbyte(h, v);
}

void host_put(host *h, int eng, uint8_t v) {
  while (host_est(h, eng) & ST_TXFULL) {
  }
  host_write_tx(h, eng, v);
}

uint8_t host_pop(host *h, int eng) {
  nib(h, HC_POP, eng);
  board_run(h->b, HOST_LAT);
  return h->b->host_q2;
}

int host_get(host *h, int eng, uint8_t *v, uint64_t timeout) {
  uint64_t end = h->b->cycle + timeout;
  while (h->b->cycle < end) {
    if (host_est(h, eng) & ST_RXNE) {
      *v = host_pop(h, eng);
      return 1;
    }
  }
  return 0;
}

/* send hdr[] then tx[] */
static int stream(host *h, int eng, const uint8_t *hdr, int nh, const uint8_t *tx, int ntx,
                  uint8_t *rx, int nrx, uint64_t timeout) {
  int sent = 0, got = 0, total = nh + ntx;
  uint64_t end = h->b->cycle + timeout;
  while ((sent < total || got < nrx) && h->b->cycle < end) {
    uint8_t s = host_est(h, eng);
    if (got < nrx && (s & ST_RXNE)) {
      uint8_t v = host_pop(h, eng);
      if (rx) rx[got] = v;
      got++;
    }
    if (sent < total && !(s & ST_TXFULL)) {
      host_write_tx(h, eng, sent < nh ? hdr[sent] : tx[sent - nh]);
      sent++;
    }
  }
  return got == nrx && sent == total;
}

int host_stream(host *h, int eng, const uint8_t *tx, int ntx, uint8_t *rx, int nrx,
                uint64_t timeout) {
  return stream(h, eng, NULL, 0, tx, ntx, rx, nrx, timeout);
}

int host_frame(host *h, int eng, uint16_t hdr, const uint8_t *tx, int ntx, uint8_t *rx, int nrx,
               uint64_t timeout) {
  const uint8_t hb[2] = {(uint8_t)(hdr >> 8), (uint8_t)hdr};
  return stream(h, eng, hb, 2, tx, ntx, rx, nrx, timeout);
}
