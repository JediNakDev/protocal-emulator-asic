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
uint8_t host_status(host *h);
void host_ctrl(host *h, uint8_t run, uint8_t restart);
void host_load(host *h, int core, const pe_prog *p, const int phys[PE_LPINS], uint16_t t0,
               uint16_t t1);
void host_write_tx(host *h, int core, uint8_t v);  /* no flow control       */
void host_put(host *h, int core, uint8_t v);       /* waits for space       */
uint8_t host_pop(host *h, int core);               /* caller saw RXNE       */
int host_get(host *h, int core, uint8_t *v, uint64_t timeout);
void host_clear_flags(host *h, int mask);

/* stream tx[] into a core while collecting nrx bytes back */
int host_stream(host *h, int core, const uint8_t *tx, int ntx, uint8_t *rx, int nrx,
                uint64_t timeout);

#endif
