/*
 * Peers on the uio Pmod header, each modelled on a part that can be bought
 * and plugged in after the chip comes back:
 *   uart_peer   - 3.3 V USB-UART bridge (e.g. Digilent Pmod USBUART, FT232R)
 *   spi_flash   - Winbond W25Q128JV serial NOR flash (JEDEC ID EF 40 18)
 *   i2c_adt7420 - Analog Devices ADT7420 temperature sensor (Digilent Pmod TMP2)
 *   usb_kbd     - USB low-speed HID keyboard (any USB 1.1 keyboard)
 *   eth_rx      - 10BASE-T receiver (a PC NIC behind an RJ45 MagJack)
 *
 * Peers react one cycle after they observe a pin change (25 ns at 40 MHz,
 * i.e. clock-to-output plus pad delay) and also act as protocol checkers.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#ifndef PEERS_H
#define PEERS_H

#include "pe.h"

typedef struct {
  peer base;
  int rx_pin, tx_pin;   /* uio pins: peer listens on rx_pin, drives tx_pin */
  double rx_period;     /* expected bit period (cycles)                    */
  double tx_period;     /* bit period the peer transmits with              */
  bool echo;
  int gap;              /* idle cycles between transmitted frames          */
  /* receiver */
  int rst, rbit;
  uint64_t rstart;
  uint8_t rbyte, rprev;
  uint8_t rx[1024];
  uint64_t starts[1024]; /* start-bit edge of each received frame */
  int nrx, ferr;
  double max_dev;       /* worst edge deviation from the ideal bit grid    */
  /* transmitter: bit 8 of a queue entry forces a bad stop bit */
  uint16_t txq[1024];
  int txh, txt;
  bool tbusy;
  uint64_t tstart, tnext;
  uint16_t tcur;
} uart_peer;

void uart_peer_init(uart_peer *u, int rx_pin, int tx_pin, double rx_period, double tx_period);
void uart_peer_send(uart_peer *u, uint8_t v, bool bad_stop);

typedef struct {
  peer base;
  int sck, mosi, miso, cs;
  uint8_t *mem;
  uint8_t psck, pcs, pmosi;
  int bitc, nbyte;
  uint8_t sin, sout, cmd;
  bool outen;
  uint32_t addr;
  bool wel;
  uint64_t busy_until;
  uint8_t page[256];
  bool pmask[256];
  uint32_t paddr;
  int mode;
  uint64_t last_rise, last_fall, cs_fall, cs_rise;
  int min_period, max_period, min_high, min_low, min_cs_high, frames, rises;
  uint64_t bytes;
  int tpp, tse;  /* busy times in cycles */
  int extra_lat; /* extra MISO delay in cycles (0..7) */
  uint8_t miso_v;
  uint16_t hist;
} spi_flash;

void spi_flash_init(spi_flash *f, int cs, int mosi, int miso, int sck);

typedef struct {
  peer base;
  int scl, sda;
  uint8_t addr;
  uint8_t regs[0x30];
  uint8_t ptr;
  int st, nextst, bit, nwrite;
  uint8_t sh, tx;
  bool rw, rose, mack;
  bool sda_low, scl_low;
  uint8_t pscl, psda;
  int stretch;
  uint64_t stretch_until;
  /* checker */
  uint64_t t_rise, t_fall, t_sda, t_start, t_stop;
  bool after_start, sda_moved, stretched;
  int min_low, min_high, min_sudat, min_hdsta, min_susta, min_susto, min_buf, min_period;
  uint64_t last_rise_p;
  int starts, stops, bytes, nstretch;
} i2c_adt7420;

void i2c_adt7420_init(i2c_adt7420 *d, int scl, int sda, uint8_t addr);

typedef struct {
  peer base;
  int dp, dm;
  double tbit;          /* device transmit bit period (cycles)             */
  int resp_bits;        /* bus turnaround before replying (bit times)      */
  int st, pline;
  double next_s;
  uint64_t first_edge, se0_t, tx_at, eop_end, ack_deadline;
  double tx_t0;
  uint8_t raw[1024], tx[1024];
  int nraw, ntx;
  /* device state */
  uint8_t addr, new_addr;
  bool addr_pend, ep0_status, report_ready, expect_ack, want_ipd;
  int tok_pid, tok_ep, ep0_len, ep0_off, ep0_tog, ep1_tog, pend, pend_len;
  uint8_t ep0[64], report[8];
  /* checks */
  double max_rate_err, min_ipd, max_ipd;
  int min_eop, max_eop;
  int pkts, bad_crc, bad_coding, ignored, acks, naks, ack_timeouts, setups, reports;
} usb_kbd;

void usb_kbd_init(usb_kbd *k, int dp, int dm, double clock_err);
void usb_kbd_press(usb_kbd *k, const uint8_t report[8]);

#define ETH_MAXF 8
typedef struct {
  peer base;
  int tdp, tdm;
  int prev;
  bool in_frame, in_pulse;
  int8_t *lev;
  int nlev;
  uint64_t pstart, last_nlp, last_end;
  int nlps, nlp_wmin, nlp_wmax;
  uint64_t nlp_gmin, nlp_gmax;
  int ifg_min, tpidl_min, tpidl_max;
  uint8_t frame[ETH_MAXF][1600];
  int flen[ETH_MAXF], nframes;
  int bad_manch, bad_pre, bad_fcs, bad_len, odd_bits;
} eth_rx;

void eth_rx_init(eth_rx *e, int tdp, int tdm);
void eth_rx_free(eth_rx *e);

#endif
