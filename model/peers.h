/*
 * Peers on the uio Pmod header, each modelled on a part that can be bought
 * and plugged in after the chip comes back:
 *   uart_peer   - 3.3 V USB-UART bridge (e.g. Digilent Pmod USBUART, FT232R)
 *   spi_flash   - Winbond W25Q128JV serial NOR flash (JEDEC ID EF 40 18)
 *   i2c_adt7420 - Analog Devices ADT7420 temperature sensor (Digilent Pmod TMP2)
 *
 * Peers react one cycle after they observe a pin change (20 ns at 50 MHz,
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

#endif
