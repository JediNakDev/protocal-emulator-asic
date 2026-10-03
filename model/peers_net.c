/*
 * USB low-speed keyboard and 10BASE-T receiver peers, with protocol checkers.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "peers.h"
#include "proto.h"

/* ======================================================================== */
/* USB low-speed HID keyboard                                                */
/* ======================================================================== */

#define USB_NOM (PE_CLK_HZ / 1.5e6) /* nominal bit period in cycles */
enum { LN_SE0 = 0, LN_K = 1, LN_J = 2 };
enum { U_IDLE, U_RX, U_WAIT, U_TX };
enum { P_NONE, P_EP0_DATA, P_EP0_STATUS, P_EP1 };

/* line state: D+ in bit 0, D- in bit 1 (LN_*, or 3 for SE1) */
static int line_state(uint8_t wire, int dp, int dm) {
  return ((wire >> dp) & 1) | ((wire >> dm) & 1) << 1;
}

static const uint8_t dev_desc[18] = {0x12, 0x01, 0x10, 0x01, 0x00, 0x00, 0x00, 0x08, 0x6d,
                                     0x04, 0x1c, 0xc3, 0x00, 0x01, 0x01, 0x02, 0x00, 0x01};

static void usb_send(usb_kbd *k, uint64_t c, int pid, const uint8_t *d, int n) {
  uint8_t pk[16];
  int len = usb_packet(pk, pid, d, n);
  k->ntx = usb_encode(pk, len, k->tx);
  k->tx_at = c + (uint64_t)(k->resp_bits * USB_NOM);
  k->st = U_WAIT;
  if (pid == PID_NAK) k->naks++;
}

static void usb_in(usb_kbd *k, uint64_t c) {
  if (k->tok_ep == 0 && k->ep0_status) {
    usb_send(k, c, PID_DATA1, NULL, 0);
    k->pend = P_EP0_STATUS;
  } else if (k->tok_ep == 0 && k->ep0_off < k->ep0_len) {
    int n = k->ep0_len - k->ep0_off > 8 ? 8 : k->ep0_len - k->ep0_off;
    usb_send(k, c, k->ep0_tog ? PID_DATA1 : PID_DATA0, k->ep0 + k->ep0_off, n);
    k->pend = P_EP0_DATA;
    k->pend_len = n;
  } else if (k->tok_ep == 1 && k->report_ready) {
    usb_send(k, c, k->ep1_tog ? PID_DATA1 : PID_DATA0, k->report, 8);
    k->pend = P_EP1;
  } else {
    usb_send(k, c, PID_NAK, NULL, 0);
    return;
  }
  k->expect_ack = true;
}

static void usb_setup(usb_kbd *k, const uint8_t *p) {
  int wvalue = p[2] | p[3] << 8, wlength = p[6] | p[7] << 8;
  k->setups++;
  k->ep0_len = k->ep0_off = 0;
  k->ep0_status = false;
  if (p[0] == 0x80 && p[1] == 0x06 && (wvalue >> 8) == 1) {
    k->ep0_len = wlength < 18 ? wlength : 18;
    memcpy(k->ep0, dev_desc, 18);
    k->ep0_tog = 1;
  } else if (p[0] == 0x00 && p[1] == 0x05) {
    k->new_addr = (uint8_t)(wvalue & 0x7f);
    k->addr_pend = true;
    k->ep0_status = true;
  } else {
    k->ep0_status = true;
  }
}

static void usb_packet_in(usb_kbd *k, board *b, uint64_t c) {
  uint8_t by[64];
  int n = usb_decode(k->raw, k->nraw, by);
  if (n < 2 || by[0] != 0x80 || (by[1] & 15) != (~by[1] >> 4 & 15)) {
    k->bad_coding++;
    board_error(b, "usb device: undecodable packet (%d line bits)", k->nraw);
    return;
  }
  k->pkts++;
  int pid = by[1] & 15;
  switch (pid) {
  case PID_OUT:
  case PID_IN:
  case PID_SETUP: {
    uint8_t f[2] = {by[2], (uint8_t)(by[3] & 7)};
    if (n != 4 || crc5_usb(f, 11) != by[3] >> 3) {
      k->bad_crc++;
      return;
    }
    int addr = by[2] & 0x7f, ep = (by[2] >> 7) | (by[3] & 7) << 1;
    if (addr != k->addr) {
      k->ignored++;
      k->tok_pid = 0;
      return;
    }
    k->tok_pid = pid;
    k->tok_ep = ep;
    if (pid == PID_IN) usb_in(k, c);
    break;
  }
  case PID_DATA0:
  case PID_DATA1: {
    if (n < 4 || crc16_usb(by + 2, n - 4) != (by[n - 2] | by[n - 1] << 8)) {
      k->bad_crc++;
      return;
    }
    if (k->tok_pid == PID_SETUP && n == 12) usb_setup(k, by + 2);
    if (k->tok_pid == PID_SETUP || k->tok_pid == PID_OUT) usb_send(k, c, PID_ACK, NULL, 0);
    k->tok_pid = 0;
    break;
  }
  case PID_ACK:
    if (!k->expect_ack) break;
    k->expect_ack = false;
    k->acks++;
    if (k->pend == P_EP0_DATA) {
      k->ep0_off += k->pend_len;
      k->ep0_tog ^= 1;
    } else if (k->pend == P_EP0_STATUS) {
      k->ep0_status = false;
      if (k->addr_pend) k->addr = k->new_addr;
      k->addr_pend = false;
    } else if (k->pend == P_EP1) {
      k->report_ready = false;
      k->ep1_tog ^= 1;
      k->reports++;
    }
    k->pend = P_NONE;
    break;
  default:
    k->ignored++;
  }
}

static void usb_step(peer *pp, board *b, const wire_view *w) {
  usb_kbd *k = (usb_kbd *)pp;
  uint64_t c = b->cycle;
  int ln = line_state(w->now, k->dp, k->dm), pline = line_state(w->prev, k->dp, k->dm);

  if (k->st == U_WAIT && c >= k->tx_at) {
    k->st = U_TX;
    k->tx_t0 = (double)c + 1;
  }
  if (k->st == U_TX) { /* drive the line for the next cycle */
    bitgrid g = {k->tx_t0, k->tbit};
    int i = bg_bit(&g, c + 1), code;
    if (i < k->ntx) code = k->tx[i] ? LN_K : LN_J;
    else if (i < k->ntx + 2) code = LN_SE0;
    else if (i < k->ntx + 3) code = LN_J;
    else code = -1;
    bool drive = code >= 0;
    peer_drive(&k->base, k->dp, drive, drive && (code & 1));
    peer_drive(&k->base, k->dm, drive, drive && (code & 2));
    if (!drive) {
      k->st = U_IDLE;
      k->eop_end = (uint64_t)bg_start(&g, k->ntx + 2);
      if (k->expect_ack) {
        k->want_ipd = true;
        k->ack_deadline = c + (uint64_t)(16 * USB_NOM);
      }
    }
    return;
  }

  if (k->st == U_IDLE) {
    if (k->expect_ack && c > k->ack_deadline) {
      k->ack_timeouts++;
      k->expect_ack = false;
      k->pend = P_NONE;
    }
    if (pline == LN_J && ln == LN_K) {
      k->st = U_RX;
      k->first_edge = c;
      k->next_s = (double)c + USB_NOM / 2;
      k->nraw = 0;
      k->se0_t = 0;
      if (k->want_ipd) {
        span_add(&k->ipd, (double)(c - k->eop_end) / USB_NOM);
        k->want_ipd = false;
      }
    }
  } else if (k->st == U_RX) {
    if (ln == 3) board_error(b, "usb device: SE1 on the bus");
    if (ln != pline) {
      k->next_s = (double)c + USB_NOM / 2; /* re-centre on every edge */
      if (ln == LN_SE0 && !k->se0_t) k->se0_t = c;
    }
    if (pline == LN_SE0 && ln == LN_J) { /* end of packet */
      span_add(&k->eop, (double)(c - k->se0_t));
      span_add(&k->rate_err, fabs((double)(k->se0_t - k->first_edge) / k->nraw / USB_NOM - 1));
      k->st = U_IDLE;
      usb_packet_in(k, b, c);
    } else if (ln != LN_SE0 && !k->se0_t && (double)c >= k->next_s) {
      if (k->nraw < (int)sizeof k->raw) k->raw[k->nraw++] = ln == LN_K;
      k->next_s += USB_NOM;
    }
  }
}

void usb_kbd_init(usb_kbd *k, int dp, int dm, double clock_err) {
  memset(k, 0, sizeof *k);
  k->base.name = "usb_kbd";
  k->base.step = usb_step;
  k->dp = dp;
  k->dm = dm;
  k->tbit = USB_NOM * (1 - clock_err); /* faster clock = shorter bits */
  k->resp_bits = 3;
}

void usb_kbd_press(usb_kbd *k, const uint8_t report[8]) {
  memcpy(k->report, report, 8);
  k->report_ready = true;
}

/* ======================================================================== */
/* 10BASE-T receiver                                                         */
/* ======================================================================== */

#define ETH_LEVBUF 65536

static void eth_decode(eth_rx *e, board *b) {
  int nb = 0;
  while (4 * nb + 3 < e->nlev) {
    int8_t *l = e->lev + 4 * nb;
    if (!(l[0] && l[0] == l[1] && l[2] == l[3] && l[2] == -l[0])) break;
    nb++;
  }
  int tp = 0;
  for (int i = 4 * nb; i < e->nlev - 1; i++) {
    if (e->lev[i] != 1) {
      e->bad_manch++;
      board_error(b, "eth: Manchester violation at bit %d", i / 4);
      return;
    }
    tp++;
  }
  span_add(&e->tpidl, tp);
  if (nb % 8) {
    e->odd_bits++;
    board_error(b, "eth: %d bits is not whole bytes", nb);
    return;
  }
  uint8_t by[1600];
  int n = nb / 8 > 1600 ? 1600 : nb / 8;
  for (int i = 0; i < n; i++) {
    by[i] = 0;
    for (int k = 0; k < 8; k++) by[i] |= (uint8_t)((e->lev[4 * (8 * i + k) + 2] > 0) << k);
  }
  for (int i = 0; i < 8; i++)
    if (n < 8 || by[i] != (i < 7 ? 0x55 : 0xd5)) {
      e->bad_pre++;
      return;
    }
  int fl = n - 8;
  uint8_t *f = by + 8;
  if (fl < 64) {
    e->bad_len++;
    return;
  }
  uint32_t fcs = (uint32_t)(f[fl - 4] | f[fl - 3] << 8 | f[fl - 2] << 16 | (uint32_t)f[fl - 1] << 24);
  if (crc32_eth(f, fl - 4) != fcs) {
    e->bad_fcs++;
    return;
  }
  if (e->nframes < ETH_MAXF) {
    memcpy(e->frame[e->nframes], f, (size_t)fl);
    e->flen[e->nframes] = fl;
  }
  e->nframes++;
}

/* differential level: +1, -1, or 0 when both wires are equal (idle) */
static int eth_level(uint8_t wire, int tdp, int tdm) {
  int p = (wire >> tdp) & 1, m = (wire >> tdm) & 1;
  return p == m ? 0 : p ? 1 : -1;
}

static void eth_step(peer *pp, board *b, const wire_view *w) {
  eth_rx *e = (eth_rx *)pp;
  uint64_t c = b->cycle;
  int lv = eth_level(w->now, e->tdp, e->tdm), prev = eth_level(w->prev, e->tdp, e->tdm);
  if (e->in_frame) {
    if (e->nlev < ETH_LEVBUF) e->lev[e->nlev++] = (int8_t)lv;
    if (lv == 0) {
      eth_decode(e, b);
      e->in_frame = false;
      e->last_end = c;
    }
  } else if (e->in_pulse) {
    if (lv != 1) {
      span_add(&e->nlp_width, (double)(c - e->pstart));
      span_since(&e->nlp_gap, e->pstart, e->last_nlp);
      e->last_nlp = e->pstart;
      e->nlps++;
      e->in_pulse = false;
    }
  } else if (prev == 0 && lv == 1) {
    e->in_pulse = true;
    e->pstart = c;
  } else if (prev == 0 && lv == -1) {
    e->in_frame = true;
    e->nlev = 0;
    e->lev[e->nlev++] = -1;
    span_since(&e->ifg, c, e->last_end);
    e->last_nlp = 0; /* link pulses restart after traffic */
  }
}

void eth_rx_init(eth_rx *e, int tdp, int tdm) {
  memset(e, 0, sizeof *e);
  e->base.name = "eth_rx";
  e->base.step = eth_step;
  e->tdp = tdp;
  e->tdm = tdm;
  e->lev = malloc(ETH_LEVBUF);
}

void eth_rx_free(eth_rx *e) { free(e->lev); }
