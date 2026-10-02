/*
 * Board model: uio wires with external pull-ups, peers on the Pmod headers,
 * and the RP2040-side synchroniser on uo_out.
 *
 * Cycle t:  wire(t) = resolve(chip pads registered at t-1, peer drives from t-1)
 *           peers observe wire(t) and set their drive for t+1
 *           chip executes, reading uio as wire(t-2) through its synchroniser
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "pe.h"

void board_init(board *b) {
  memset(b, 0, sizeof *b);
  chip_reset(&b->chip);
  b->wire = 0xff;
}

void board_add_peer(board *b, peer *p) { b->peers[b->npeers++] = p; }

void board_error(board *b, const char *fmt, ...) {
  b->errors++;
  if (b->quiet || b->errors > 20) return;
  va_list ap;
  va_start(ap, fmt);
  fprintf(stderr, "  [cycle %llu] ERROR: ", (unsigned long long)b->cycle);
  vfprintf(stderr, fmt, ap);
  fputc('\n', stderr);
  va_end(ap);
}

void board_step(board *b) {
  uint8_t low = (uint8_t)(b->chip.uio_oe & ~b->chip.uio_out);
  uint8_t high = (uint8_t)(b->chip.uio_oe & b->chip.uio_out);
  for (int i = 0; i < b->npeers; i++) {
    peer *p = b->peers[i];
    uint8_t pl = (uint8_t)(p->oe & ~p->out), ph = (uint8_t)(p->oe & p->out);
    if ((pl & high) || (ph & low))
      board_error(b, "contention between chip/peers and %s on uio mask 0x%02x", p->name,
                  (pl & high) | (ph & low));
    low |= pl;
    high |= ph;
  }
  /* push-pull high against another push-pull high is fine; undriven pulls up */
  b->wire = (uint8_t)~low;

  for (int i = 0; i < b->npeers; i++) b->peers[i]->step(b->peers[i], b, b->wire);

  b->host_q2 = b->host_q1;
  b->host_q1 = b->chip.uo;
  chip_step(b);
  b->cycle++;
}

void board_run(board *b, uint64_t n) {
  while (n--) board_step(b);
}
