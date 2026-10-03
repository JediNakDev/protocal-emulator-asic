/*
 * RP2040 host model: drives ui_in, reads uo_out through its own
 * synchroniser, and advances simulated time while it does so.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#ifndef HOST_H
#define HOST_H

#include "pe.h"

typedef struct {
  board *b;
  int edge;          /* chip cycles per host pin change (RP2040 SIO loop)  */
  int sel;           /* cached SEL target, -1 unknown                      */
  uint8_t run;
  uint64_t bus_ops;
} host;

void host_init(host *h, board *b, int edge);
void host_wait(host *h, uint64_t cycles);
uint8_t host_est(host *h, int eng);                /* status nibble of one engine */
void host_ctrl(host *h, uint8_t run, uint8_t restart);
void host_restart(host *h, int eng);               /* PC, registers, FIFOs, pins */
void host_load(host *h, int eng, const pe_prog *p, const int phys[PE_LPINS], uint16_t t0,
               uint16_t t1);
void host_write_tx(host *h, int eng, uint8_t v);   /* no flow control       */
void host_put(host *h, int eng, uint8_t v);        /* waits for space       */
uint8_t host_pop(host *h, int eng);                /* caller saw RXNE       */
int host_get(host *h, int eng, uint8_t *v, uint64_t timeout);
void host_clear_flags(host *h, int mask);

/* stream tx[] into an engine while collecting nrx bytes back */
int host_stream(host *h, int eng, const uint8_t *tx, int ntx, uint8_t *rx, int nrx,
                uint64_t timeout);
/* the same, preceded by a 16-bit header, high byte first: the framing of the
 * programs that load a count with two "out x, 8" (spi, usb, eth) */
int host_frame(host *h, int eng, uint16_t hdr, const uint8_t *tx, int ntx, uint8_t *rx, int nrx,
               uint64_t timeout);

#endif
