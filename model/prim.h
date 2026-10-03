/*
 * Measurement primitives shared by the peer models and their protocol
 * checkers. Every peer watches the same wire and times the same kinds of
 * things, so the common pieces live here:
 *
 *   span       smallest and largest value of a measured quantity
 *   wire_view  uio levels this cycle and last cycle: levels and edges
 *   bitgrid    bit k of a serial stream occupies a fixed slice of time
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#ifndef PRIM_H
#define PRIM_H

#include <math.h>
#include <stdbool.h>
#include <stdint.h>

/* ---- span --------------------------------------------------------------
 * A zeroed span is empty (n == 0) and reads lo = hi = 0, so a limit that is
 * never measured fails a "lo >= limit" check instead of passing silently.
 */
typedef struct {
  double lo, hi;
  int n;
} span;

static inline void span_add(span *s, double v) {
  if (s->n == 0 || v < s->lo) s->lo = v;
  if (s->n == 0 || v > s->hi) s->hi = v;
  s->n++;
}

/* cycles from an earlier event to now; a zero timestamp means "not yet" */
static inline void span_since(span *s, uint64_t now, uint64_t mark) {
  if (mark) span_add(s, (double)(now - mark));
}

/* ---- wire_view ---------------------------------------------------------- */
typedef struct {
  uint8_t now, prev;
} wire_view;

static inline int wv_lvl(const wire_view *w, int pin) { return (w->now >> pin) & 1; }
static inline int wv_was(const wire_view *w, int pin) { return (w->prev >> pin) & 1; }
static inline bool wv_rose(const wire_view *w, int pin) { return !wv_was(w, pin) && wv_lvl(w, pin); }
static inline bool wv_fell(const wire_view *w, int pin) { return wv_was(w, pin) && !wv_lvl(w, pin); }
static inline bool wv_moved(const wire_view *w, int pin) { return wv_was(w, pin) != wv_lvl(w, pin); }

/* ---- bitgrid -------------------------------------------------------------
 * Bit k occupies cycles [t0 + k * period, t0 + (k + 1) * period).
 */
typedef struct {
  double t0, period;
} bitgrid;

static inline double bg_start(const bitgrid *g, int k) { return g->t0 + k * g->period; }

/* the bit that cycle t falls in */
static inline int bg_bit(const bitgrid *g, uint64_t t) {
  return (int)floor(((double)t - g->t0) / g->period);
}

/* the cycle in which bit k is sampled: its middle */
static inline uint64_t bg_mid(const bitgrid *g, int k) {
  return (uint64_t)(g->t0 + (k + 0.5) * g->period);
}

/* distance in cycles from t to the nearest bit boundary */
static inline double bg_dev(const bitgrid *g, uint64_t t) {
  double e = (double)t - g->t0, k = floor(e / g->period + 0.5);
  return fabs(e - k * g->period);
}

#endif
