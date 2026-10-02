/*
 * Protocol test suite. Every check is tied to a criterion in CRITERIA.md and
 * observes the uio pins through independent peer models, or the host bus.
 *
 * Board wiring (Tiny Tapeout demo board uio Pmod):
 *   uio[0] SPI CS / USB D+ / -   uio[1] SPI MOSI / USB D- / -
 *   uio[2] SPI MISO / - / ETH TD+  uio[3] SPI SCK / - / ETH TD-
 *   uio[4] UART TX   uio[5] UART RX   uio[6] I2C SCL   uio[7] I2C SDA
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host.h"
#include "peers.h"
#include "proto.h"

#define UART_T0 ((uint16_t)(PE_CLK_HZ / 115200 + 0.5))  /* 347 at 40 MHz */
#define UART_T1 ((uint16_t)(UART_T0 / 2 - 1))            /* first tick mid-bit */
#define I2C_T0 ((uint16_t)PE_NS(1500))                   /* SCL low 1.5 us */
#define I2C_T1 ((uint16_t)(PE_NS(1000) - 4))             /* SCL high 1.0 us incl. edge detect */
#define USB_T0 ((uint16_t)(PE_CLK_HZ / 1.5e6 + 0.5))    /* 27 at 40 MHz */
#define USB_T1 ((uint16_t)(USB_T0 / 2 - 1))
#define ETH_T0 ((uint16_t)PE_NS(10000))                  /* interframe-gap tick */
#define HOST_EDGE 2 /* chip cycles per RP2040 pin change */

static pe_prog P_UTX, P_URX, P_SPI, P_SPI6, P_I2C, P_USB, P_ETH;
static unsigned rng = 12345;
static unsigned rnd(void) { return rng = rng * 1103515245u + 12345u, rng >> 16; }

/* ---- criteria bookkeeping ---------------------------------------------- */
typedef struct {
  const char *id;
  int n, fail;
} crit_t;
#define C_(id) {id, 0, 0}
static crit_t crits[] = {C_("G1"), C_("G2"), C_("G3"), C_("G4"), C_("G5"), C_("G6"),
                         C_("U1"), C_("U2"), C_("U3"), C_("U4"), C_("U5"), C_("U6"),
                         C_("S1"), C_("S2"), C_("S3"), C_("S4"), C_("S5"), C_("S6"),
                         C_("I1"), C_("I2"), C_("I3"), C_("I4"), C_("I5"),
                         C_("K1"), C_("K2"), C_("K3"), C_("K4"), C_("K5"), C_("K6"), C_("K7"), C_("K8"),
                         C_("E1"), C_("E2"), C_("E3"), C_("E4"), C_("E5"), C_("E6")};
#define NCRIT ((int)(sizeof crits / sizeof crits[0]))

static crit_t *crit(const char *id) {
  for (int i = 0; i < NCRIT; i++)
    if (!strcmp(crits[i].id, id)) return &crits[i];
  fprintf(stderr, "unknown criterion %s\n", id);
  exit(2);
}

#define CRIT(id, cond, ...)                                                                   \
  do {                                                                                        \
    crit_t *c_ = crit(id);                                                                    \
    c_->n++;                                                                                  \
    if (!(cond)) {                                                                            \
      c_->fail++;                                                                             \
      printf("  FAIL %s (line %d): ", id, __LINE__);                                          \
      printf(__VA_ARGS__);                                                                    \
      printf("\n");                                                                           \
    }                                                                                         \
  } while (0)

static const int MAP_UTX[4] = {4, 0, 0, 0};
static const int MAP_URX[4] = {5, 0, 0, 0};
static const int MAP_SPI[4] = {3, 1, 2, 0}; /* sck mosi miso cs */
static const int MAP_I2C[4] = {7, 6, 0, 0}; /* sda scl */
static const int MAP_USB[4] = {0, 1, 0, 0}; /* d+ d- */
static const int MAP_ETH[4] = {2, 3, 0, 0}; /* td+ td- */

static void setup(board *b, host *h) {
  board_init(b);
  host_init(h, b, HOST_EDGE);
  board_run(b, 10);
}

static void no_errors(board *b) { CRIT("G2", b->errors == 0, "%d pin/bus errors", b->errors); }

/* ======================================================================== */
/* General                                                                   */

static void test_selfcheck(void) {
  printf("helpers: CRC check values and USB line coding\n");
  const uint8_t s[] = "123456789";
  CRIT("G6", crc5_usb(s, 72) == 0x19, "CRC-5/USB");
  CRIT("G6", crc16_usb(s, 9) == 0xb4c8, "CRC-16/USB");
  CRIT("G6", crc32_eth(s, 9) == 0xcbf43926, "CRC-32");
  uint8_t t[4];
  usb_token(t, PID_SETUP, 0, 0);
  CRIT("G6", t[1] == 0x2d && t[2] == 0x00 && t[3] == 0x10, "SETUP token %02x %02x %02x", t[1], t[2], t[3]);
  uint8_t ack[2] = {0x80, USB_PID(PID_ACK)}, line[32];
  int n = usb_encode(ack, 2, line);
  char str[33] = "";
  for (int i = 0; i < n; i++) str[i] = line[i] ? 'K' : 'J';
  CRIT("G6", !strcmp(str, "KJKJKJKKJJKJJKKK"), "SYNC+ACK encodes as %s", str);
  uint8_t ff[3] = {0x80, 0xff, 0xff}, back[3];
  n = usb_encode(ff, 3, line);
  CRIT("G6", n == 24 + 2 && usb_decode(line, n, back) == 3 && !memcmp(back, ff, 3), "bit stuffing");
}

static void test_reset(void) {
  printf("reset: pads are inputs after reset and after an engine restart\n");
  board b;
  host h;
  setup(&b, &h);
  CRIT("G1", b.chip.uio_oe == 0 && b.wire == 0xff, "uio_oe=0x%02x after reset", b.chip.uio_oe);
  host_load(&h, 0, &P_UTX, MAP_UTX, UART_T0, 0);
  host_wait(&h, 20);
  CRIT("G1", b.chip.uio_oe == 0x10, "UART TX drives uio[4]: oe=0x%02x", b.chip.uio_oe);
  host_ctrl(&h, 0, 1); /* restart engine 0 and leave it stopped */
  host_wait(&h, 10);
  CRIT("G1", b.chip.uio_oe == 0, "uio_oe=0x%02x after restart", b.chip.uio_oe);
  no_errors(&b);
}

/* ======================================================================== */
/* UART                                                                      */

static void test_uart_tx(void) {
  printf("uart_tx: 115200 8N1 on uio[4]\n");
  board b;
  host h;
  uart_peer u, g;
  setup(&b, &h);
  uart_peer_init(&u, 4, -1, PE_CLK_HZ / 115200, 0); /* receiver at the nominal baud rate */
  uart_peer_init(&g, 4, -1, UART_T0, 0);            /* edge checker on the T0 grid */
  board_add_peer(&b, &u.base);
  board_add_peer(&b, &g.base);
  host_load(&h, 0, &P_UTX, MAP_UTX, UART_T0, 0);
  uint8_t msg[48] = {0x00, 0xFF, 0x55, 0xAA, 'H', 'e', 'l', 'l', 'o'};
  int n = sizeof msg;
  for (int i = 9; i < n; i++) msg[i] = (uint8_t)rnd();
  for (int i = 0; i < 4; i++) host_put(&h, 0, msg[i]);
  uint64_t t0 = 0;
  int r0 = 0;
  for (int i = 4; i < n; i++) {
    host_put(&h, 0, msg[i]);
    if (i == 8) { /* the FIFO is kept full from here on: frames back to back */
      t0 = b.cycle;
      r0 = u.nrx;
    }
  }
  while (u.nrx < n && b.cycle < t0 + (uint64_t)n * 12 * UART_T0) board_step(&b);
  double per_frame = (double)(u.starts[n - 1] - u.starts[r0 + 1]) / (n - 2 - r0);
  CRIT("U1", u.nrx == n && !memcmp(u.rx, msg, (size_t)n) && u.ferr == 0, "received %d of %d", u.nrx, n);
  double baud_err = (double)UART_T0 / (PE_CLK_HZ / 115200) - 1;
  CRIT("U2", baud_err < 0.005 && baud_err > -0.005, "baud error %.3f%%", baud_err * 100);
  CRIT("U2", g.max_dev <= 1.0, "edge deviation %.2f cycles", g.max_dev);
  CRIT("U3", per_frame <= 10 * UART_T0 + 1 && per_frame >= 10 * UART_T0 - 1, "%.1f cycles per frame", per_frame);
  no_errors(&b);
  printf("  %d bytes, %.0f baud (%+.3f%%), %.1f cycles per back-to-back frame\n", u.nrx,
         PE_CLK_HZ / UART_T0, -baud_err * 100, per_frame);
}

static void test_uart_rx(double err) {
  printf("uart_rx: sender baud error %+.1f%%\n", err * 100);
  board b;
  host h;
  uart_peer u;
  setup(&b, &h);
  uart_peer_init(&u, -1, 5, 0, PE_CLK_HZ / 115200 / (1 + err));
  board_add_peer(&b, &u.base);
  host_load(&h, 1, &P_URX, MAP_URX, UART_T0, UART_T1);
  uint8_t msg[32];
  for (int i = 0; i < 32; i++) {
    msg[i] = i == 0 ? 0x00 : i == 1 ? 0xff : (uint8_t)rnd(); /* 0x00/0xFF: longest runs without edges */
    uart_peer_send(&u, msg[i], false);
  }
  uint8_t got[32];
  int n = 0;
  while (n < 32 && host_get(&h, 1, &got[n], 20 * UART_T0)) n++;
  CRIT("U4", n == 32 && !memcmp(got, msg, 32), "received %d of 32", n);
  CRIT("U4", !(host_est(&h, 1) & ST_FLAG), "unexpected flag");
  no_errors(&b);
}

static void test_uart_framing(void) {
  printf("uart_rx: framing error is flagged and the byte dropped\n");
  board b;
  host h;
  uart_peer u;
  setup(&b, &h);
  uart_peer_init(&u, -1, 5, 0, PE_CLK_HZ / 115200);
  u.gap = 3 * UART_T0;
  board_add_peer(&b, &u.base);
  host_load(&h, 1, &P_URX, MAP_URX, UART_T0, UART_T1);
  uart_peer_send(&u, 0x3C, true);
  uart_peer_send(&u, 0xA5, false);
  uint8_t v = 0;
  int ok = host_get(&h, 1, &v, 40 * UART_T0);
  CRIT("U5", ok && v == 0xA5, "got %d 0x%02x", ok, v);
  CRIT("U5", host_est(&h, 1) & ST_FLAG, "framing flag not set");
  host_clear_flags(&h, 2);
  CRIT("U5", !(host_est(&h, 1) & ST_FLAG), "flag not cleared");
  no_errors(&b);
}

static void test_uart_echo(void) {
  printf("uart full duplex: engine 0 TX + engine 1 RX, USB-UART echoes at +1%%\n");
  board b;
  host h;
  uart_peer u;
  setup(&b, &h);
  uart_peer_init(&u, 4, 5, PE_CLK_HZ / 115200, PE_CLK_HZ / 115200 / 1.01);
  u.echo = true;
  board_add_peer(&b, &u.base);
  host_load(&h, 0, &P_UTX, MAP_UTX, UART_T0, 0);
  host_load(&h, 1, &P_URX, MAP_URX, UART_T0, UART_T1);
  enum { N = 64 };
  uint8_t tx[N], rx[N];
  for (int i = 0; i < N; i++) tx[i] = (uint8_t)rnd();
  int sent = 0, got = 0;
  uint64_t end = b.cycle + (uint64_t)N * 12 * UART_T0 + 50000;
  while (got < N && b.cycle < end) {
    if (sent < N && !(host_est(&h, 0) & ST_TXFULL)) host_write_tx(&h, 0, tx[sent++]);
    if (host_est(&h, 1) & ST_RXNE) rx[got++] = host_pop(&h, 1);
  }
  CRIT("U6", got == N && !memcmp(tx, rx, N), "echoed %d of %d", got, N);
  no_errors(&b);
}

/* ======================================================================== */
/* SPI                                                                       */

static int spi_xfer(host *h, int eng, const uint8_t *tx, uint8_t *rx, int n) {
  uint8_t buf[260];
  buf[0] = (uint8_t)((n - 1) >> 8);
  buf[1] = (uint8_t)(n - 1);
  memcpy(buf + 2, tx, (size_t)n);
  return host_stream(h, eng, buf, n + 2, rx, n, 400000);
}

static void test_spi(void) {
  printf("spi: W25Q128JV, mode 0, SCK = clk/4\n");
  board b;
  host h;
  spi_flash f;
  setup(&b, &h);
  spi_flash_init(&f, 0, 1, 2, 3);
  for (int i = 0; i < 4096; i++) f.mem[0x1000 + i] = (uint8_t)(i * 7 + (i >> 8));
  board_add_peer(&b, &f.base);
  host_load(&h, 0, &P_SPI, MAP_SPI, 0, 0);

  uint8_t tx[256], rx[256];
  memset(tx, 0, sizeof tx);
  tx[0] = 0x9F;
  int ok = spi_xfer(&h, 0, tx, rx, 4);
  CRIT("S4", ok && rx[1] == 0xEF && rx[2] == 0x40 && rx[3] == 0x18, "JEDEC ID %02x %02x %02x", rx[1], rx[2], rx[3]);

  memset(tx, 0, sizeof tx);
  tx[0] = 0x03; tx[1] = 0x00; tx[2] = 0x10; tx[3] = 0x00;
  uint64_t c0 = b.cycle;
  f.max_period = 0;
  ok = spi_xfer(&h, 0, tx, rx, 256);
  uint64_t el = b.cycle - c0;
  CRIT("S4", ok && !memcmp(rx + 4, f.mem + 0x1000, 252), "read data");
  CRIT("S2", f.max_period == 4, "longest SCK period %d cycles in a 256-byte frame", f.max_period);
  printf("  252-byte read: %.2f MB/s incl. host traffic, longest SCK period %d cycles\n",
         256 / ((double)el / PE_CLK_HZ) / 1e6, f.max_period);

  memset(tx, 0, sizeof tx);
  tx[0] = 0x0B; tx[1] = 0x00; tx[2] = 0x13; tx[3] = 0x21;
  ok = spi_xfer(&h, 0, tx, rx, 5 + 40);
  CRIT("S4", ok && !memcmp(rx + 5, f.mem + 0x1321, 40), "fast read");

  tx[0] = 0x06;
  spi_xfer(&h, 0, tx, rx, 1);
  tx[0] = 0x05; tx[1] = 0;
  spi_xfer(&h, 0, tx, rx, 2);
  CRIT("S4", rx[1] & 2, "WEL not set: SR1=0x%02x", rx[1]);
  uint8_t data[32];
  tx[0] = 0x02; tx[1] = 0x00; tx[2] = 0x20; tx[3] = 0x00;
  for (int i = 0; i < 32; i++) tx[4 + i] = data[i] = (uint8_t)rnd();
  spi_xfer(&h, 0, tx, rx, 36);
  int polls = 0;
  do {
    tx[0] = 0x05; tx[1] = 0;
    spi_xfer(&h, 0, tx, rx, 2);
    polls++;
  } while ((rx[1] & 1) && polls < 10000);
  memset(tx, 0, sizeof tx);
  tx[0] = 0x03; tx[1] = 0x00; tx[2] = 0x20; tx[3] = 0x00;
  spi_xfer(&h, 0, tx, rx, 36);
  CRIT("S4", polls > 1 && !memcmp(rx + 4, data, 32), "page program read-back (%d polls)", polls);

  CRIT("S1", f.min_period == 4 && f.min_high >= 2 && f.min_low >= 2 && f.mode == 0,
       "period %d high %d low %d mode %d", f.min_period, f.min_high, f.min_low, f.mode);
  CRIT("S3", b.errors == 0, "MOSI changed on a rising edge");
  CRIT("S5", f.min_cs_high >= PE_NS(50), "CS high %d cycles", f.min_cs_high);
  no_errors(&b);
  printf("  SCK %.1f MHz, CS high >= %d ns, %d status polls during page program\n",
         PE_CLK_HZ / f.min_period / 1e6, (int)(f.min_cs_high * 1e9 / PE_CLK_HZ), polls);
  free(f.mem);
}

static int spi_read_ok(const pe_prog *p, int extra_lat, int *period) {
  board b;
  host h;
  spi_flash f;
  setup(&b, &h);
  spi_flash_init(&f, 0, 1, 2, 3);
  f.extra_lat = extra_lat;
  for (int i = 0; i < 64; i++) f.mem[0x400 + i] = (uint8_t)rnd();
  board_add_peer(&b, &f.base);
  host_load(&h, 0, p, MAP_SPI, 0, 0);
  uint8_t tx[68] = {0x03, 0x00, 0x04, 0x00}, rx[68];
  int ok = spi_xfer(&h, 0, tx, rx, 68) && !memcmp(rx + 4, f.mem + 0x400, 64) && b.errors == 0;
  *period = f.min_period;
  free(f.mem);
  return ok;
}

static void test_spi_margin(void) {
  printf("spi: MISO timing margin vs. extra target delay\n");
  const pe_prog *ps[2] = {&P_SPI, &P_SPI6};
  for (int k = 0; k < 2; k++) {
    int maxok = -1, period = 0;
    for (int lat = 0; lat <= 4; lat++) {
      int pr, ok = spi_read_ok(ps[k], lat, &pr);
      if (lat == 0) period = pr;
      if (ok && maxok == lat - 1) maxok = lat;
    }
    printf("  %-11s SCK %.2f MHz: correct with up to %d extra cycle(s) of MISO delay\n", ps[k]->name,
           PE_CLK_HZ / period / 1e6, maxok);
    CRIT("S6", maxok == (k == 0 ? 0 : 2), "%s tolerates %d", ps[k]->name, maxok);
  }
}

/* ======================================================================== */
/* I2C                                                                       */

#define I2C_START 0x00
#define I2C_STOP 0x20
#define I2C_WR 0xC0      /* XFER, release SDA for the target's ACK */
#define I2C_RD_ACK 0x40  /* XFER 0xFF, controller ACKs */
#define I2C_RD_NACK 0xC0 /* XFER 0xFF, controller NACKs (last byte) */

static int i2c_read(host *h, int eng, uint8_t addr, uint8_t reg, uint8_t *out, int n, uint8_t *acks) {
  uint8_t c[64], r[64];
  int k = 0;
  c[k++] = I2C_START;
  c[k++] = I2C_WR; c[k++] = (uint8_t)(addr << 1);
  c[k++] = I2C_WR; c[k++] = reg;
  c[k++] = I2C_START;
  c[k++] = I2C_WR; c[k++] = (uint8_t)(addr << 1 | 1);
  for (int i = 0; i < n; i++) {
    c[k++] = i < n - 1 ? I2C_RD_ACK : I2C_RD_NACK;
    c[k++] = 0xFF;
  }
  c[k++] = I2C_STOP;
  if (!host_stream(h, eng, c, k, r, 2 * (3 + n), 2000000)) return 0;
  for (int i = 0; i < 3; i++) acks[i] = r[2 * i + 1];
  for (int i = 0; i < n; i++) out[i] = r[2 * (3 + i)];
  return 1;
}

static int i2c_write(host *h, int eng, uint8_t addr, uint8_t reg, const uint8_t *d, int n, uint8_t *acks) {
  uint8_t c[64], r[64];
  int k = 0;
  c[k++] = I2C_START;
  c[k++] = I2C_WR; c[k++] = (uint8_t)(addr << 1);
  c[k++] = I2C_WR; c[k++] = reg;
  for (int i = 0; i < n; i++) { c[k++] = I2C_WR; c[k++] = d[i]; }
  c[k++] = I2C_STOP;
  if (!host_stream(h, eng, c, k, r, 2 * (2 + n), 2000000)) return 0;
  for (int i = 0; i < 2 + n; i++) acks[i] = r[2 * i + 1];
  return 1;
}

static void test_i2c(int stretch) {
  const char *t = stretch ? "I4" : "I1";
  printf("i2c: ADT7420 @0x48, 400 kHz%s\n", stretch ? ", target stretches SCL 10 us after every ACK" : "");
  board b;
  host h;
  i2c_adt7420 d;
  setup(&b, &h);
  i2c_adt7420_init(&d, 6, 7, 0x48);
  d.stretch = stretch;
  board_add_peer(&b, &d.base);
  host_load(&h, 0, &P_I2C, MAP_I2C, I2C_T0, I2C_T1);
  host_wait(&h, 20);
  CRIT("I5", b.chip.uio_oe == 0 && b.wire == 0xff, "lines not released at idle");

  uint8_t v[8], a[8];
  int ok = i2c_read(&h, 0, 0x48, 0x0B, v, 1, a);
  CRIT("I2", ok && !a[0] && !a[1] && !a[2] && v[0] == 0xCB, "ID 0x%02x", v[0]);
  ok = i2c_read(&h, 0, 0x48, 0x00, v, 2, a);
  CRIT("I2", ok && v[0] == 0x0C && v[1] == 0x80, "temperature %02x %02x", v[0], v[1]);
  uint8_t th[2] = {0x12, 0x34};
  ok = i2c_write(&h, 0, 0x48, 0x04, th, 2, a);
  CRIT("I2", ok && !a[0] && !a[1] && !a[2] && !a[3], "write acks");
  ok = i2c_read(&h, 0, 0x48, 0x04, v, 2, a);
  CRIT("I2", ok && v[0] == 0x12 && v[1] == 0x34, "T_HIGH %02x %02x", v[0], v[1]);

  uint8_t c[4] = {I2C_START, I2C_WR, 0x50 << 1, I2C_STOP}, r[2];
  ok = host_stream(&h, 0, c, 4, r, 2, 200000);
  CRIT("I3", ok && r[1] == 1, "absent device acked");

  host_wait(&h, 2000);
  CRIT("I5", b.wire == 0xff && b.chip.uio_oe == 0, "bus not released after STOP");
  CRIT(t, d.min_period >= PE_NS(2500), "SCL period %d", d.min_period);
  CRIT(t, d.min_low >= PE_NS(1300), "tLOW %d", d.min_low);
  CRIT(t, d.min_high >= PE_NS(600), "tHIGH %d", d.min_high);
  CRIT(t, d.min_sudat >= PE_NS(100), "tSU;DAT %d", d.min_sudat);
  CRIT(t, d.min_hdsta >= PE_NS(600), "tHD;STA %d", d.min_hdsta);
  CRIT(t, d.min_susta >= PE_NS(600), "tSU;STA %d", d.min_susta);
  CRIT(t, d.min_susto >= PE_NS(600), "tSU;STO %d", d.min_susto);
  CRIT(t, d.min_buf >= PE_NS(1300), "tBUF %d", d.min_buf);
  if (stretch) CRIT("I4", d.nstretch > 0 && ok, "target never stretched");
  no_errors(&b);
  double us = 1e6 / PE_CLK_HZ;
  printf("  SCL %.1f kHz; tLOW %.2f us, tHIGH %.2f us, tSU;DAT %.0f ns, tHD;STA %.2f us, "
         "tSU;STA %.2f us, tSU;STO %.2f us, tBUF %.2f us\n",
         PE_CLK_HZ / d.min_period / 1e3, d.min_low * us, d.min_high * us, d.min_sudat * us * 1e3,
         d.min_hdsta * us, d.min_susta * us, d.min_susto * us, d.min_buf * us);
}

/* ======================================================================== */
/* USB low-speed host                                                        */

/* Send one packet; with listen, collect and decode the reply.
 * Returns the reply length in bytes (SYNC, PID, ...), 0 when not listening,
 * -1 on timeout (engine restarted) or a reply that does not decode. */
static int usb_xfer(host *h, int eng, const uint8_t *pk, int n, int listen, uint8_t *reply) {
  uint8_t line[512], buf[80];
  int nl = usb_encode(pk, n, line), pad = (8 - nl % 8) % 8, total = nl + pad;
  buf[0] = (uint8_t)(((total - 1) >> 8) << 1 | listen);
  buf[1] = (uint8_t)(total - 1);
  memset(buf + 2, 0, (size_t)(total / 8));
  for (int i = 0; i < nl; i++) buf[2 + (pad + i) / 8] |= (uint8_t)(line[i] << ((pad + i) % 8));
  if (!host_stream(h, eng, buf, 2 + total / 8, NULL, 0, 100000)) return -1;
  if (!listen) return 0;

  uint8_t rx[64];
  int nr = 0;
  uint64_t end = h->b->cycle + (uint64_t)(total + 160) * USB_T0; /* own packet + longest reply */
  for (;;) {
    uint8_t s = host_est(h, eng);
    if (s & ST_RXNE) {
      if (nr < 64) rx[nr++] = host_pop(h, eng);
    } else if (s & ST_IDLE) {
      break;
    } else if (h->b->cycle > end) {
      host_restart(h, eng);
      return -1;
    }
  }
  host_wait(h, (uint64_t)PE_NS(1000)); /* RP2040 decode and CRC time */
  int last = -1;
  for (int i = 0; i < 8 * nr; i++)
    if ((rx[i >> 3] >> (i & 7)) & 1) last = i;
  if (last < 1) return -1;
  for (int i = 0; i < last - 1; i++) line[i] = (rx[i >> 3] >> (i & 7)) & 1;
  int nb = usb_decode(line, last - 1, reply);
  return nb >= 2 && reply[0] == 0x80 ? nb : -1;
}

/* handshake, then wait until it has left the engine */
static int usb_handshake(host *h, int eng, int pid) {
  uint8_t pk[2];
  int r = usb_xfer(h, eng, pk, usb_packet(pk, pid, NULL, 0), 0, NULL);
  while (!(host_est(h, eng) & ST_IDLE)) {
  }
  return r;
}

static int crc_ok(const uint8_t *r, int n) {
  return n >= 4 && crc16_usb(r + 2, n - 4) == (r[n - 2] | r[n - 1] << 8);
}

/* SETUP stage; returns 1 when the device ACKs */
static int usb_setup(host *h, int eng, int addr, const uint8_t req[8]) {
  uint8_t pk[16], r[64];
  usb_xfer(h, eng, pk, usb_token(pk, PID_SETUP, addr, 0), 0, NULL);
  int n = usb_xfer(h, eng, pk, usb_packet(pk, PID_DATA0, req, 8), 1, r);
  return n == 2 && r[1] == USB_PID(PID_ACK);
}

/* IN transaction; returns the payload length, -2 on NAK, -1 on no reply */
static int usb_in(host *h, int eng, int addr, int ep, int *pid, uint8_t *data) {
  uint8_t pk[4], r[64];
  int n = usb_xfer(h, eng, pk, usb_token(pk, PID_IN, addr, ep), 1, r);
  if (n < 0) return -1;
  *pid = r[1] & 15;
  if (*pid == PID_NAK) return -2;
  if (!crc_ok(r, n)) return -1;
  memcpy(data, r + 2, (size_t)(n - 4));
  usb_handshake(h, eng, PID_ACK);
  return n - 4;
}

static void test_usb(double clock_err) {
  printf("usb: low-speed host, keyboard clock %+.1f%%\n", clock_err * 100);
  board b;
  host h;
  usb_kbd k;
  setup(&b, &h);
  b.pulldown = 1 << 0; /* host 15k on D+; the keyboard's 1.5k pulls D- up */
  usb_kbd_init(&k, 0, 1, clock_err);
  board_add_peer(&b, &k.base);
  host_load(&h, 0, &P_USB, MAP_USB, USB_T0, USB_T1);
  const char *kk = clock_err != 0 ? "K6" : "K4";

  /* GET_DESCRIPTOR(device) at address 0 */
  static const uint8_t get_dev[8] = {0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x12, 0x00};
  CRIT(kk, usb_setup(&h, 0, 0, get_dev), "GET_DESCRIPTOR setup not ACKed");
  uint8_t desc[32], d[8];
  int got = 0, pid = 0, tog_ok = 1, tries = 0;
  while (got < 18 && tries++ < 10) {
    int n = usb_in(&h, 0, 0, 0, &pid, d);
    if (n < 0) continue;
    tog_ok &= pid == ((got / 8) % 2 ? PID_DATA0 : PID_DATA1);
    memcpy(desc + got, d, (size_t)n);
    got += n;
  }
  uint8_t pk[16], r[64];
  usb_xfer(&h, 0, pk, usb_token(pk, PID_OUT, 0, 0), 0, NULL);
  int n = usb_xfer(&h, 0, pk, usb_packet(pk, PID_DATA1, NULL, 0), 1, r);
  CRIT(kk, got == 18 && desc[0] == 0x12 && desc[1] == 0x01 && desc[7] == 8 && tog_ok,
       "device descriptor: %d bytes, toggles %s", got, tog_ok ? "ok" : "wrong");
  CRIT(kk, n == 2 && r[1] == USB_PID(PID_ACK), "status stage not ACKed");

  /* SET_ADDRESS(5), then only address 5 answers */
  static const uint8_t set_addr[8] = {0x00, 0x05, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00};
  CRIT(kk, usb_setup(&h, 0, 0, set_addr), "SET_ADDRESS not ACKed");
  n = usb_in(&h, 0, 0, 0, &pid, d);
  CRIT(kk, n == 0 && pid == PID_DATA1, "SET_ADDRESS status stage: %d", n);
  CRIT(kk, k.addr == 5, "device address %d", k.addr);
  n = usb_in(&h, 0, 0, 1, &pid, d);
  CRIT(kk, n == -1, "old address still answers");

  /* interrupt IN on EP1 */
  const char *kr = clock_err != 0 ? "K6" : "K5";
  n = usb_in(&h, 0, 5, 1, &pid, d);
  CRIT(kr, n == -2, "no key: expected NAK, got %d", n);
  static const uint8_t key_a[8] = {0, 0, 0x04, 0, 0, 0, 0, 0}, key_b[8] = {0x02, 0, 0x05, 0, 0, 0, 0, 0};
  usb_kbd_press(&k, key_a);
  n = usb_in(&h, 0, 5, 1, &pid, d);
  CRIT(kr, n == 8 && pid == PID_DATA0 && !memcmp(d, key_a, 8), "first report: n=%d pid=%x", n, pid);
  usb_kbd_press(&k, key_b);
  n = usb_in(&h, 0, 5, 1, &pid, d);
  CRIT(kr, n == 8 && pid == PID_DATA1 && !memcmp(d, key_b, 8), "second report: n=%d pid=%x", n, pid);
  host_wait(&h, 200);
  CRIT(kr, k.reports == 2 && k.ack_timeouts == 0, "reports ACKed %d, ACK timeouts %d", k.reports, k.ack_timeouts);

  /* corrupted data packet: no reply, host recovers */
  if (clock_err == 0) {
    static const uint8_t zero[8] = {0};
    usb_xfer(&h, 0, pk, usb_token(pk, PID_OUT, 5, 0), 0, NULL);
    int len = usb_packet(pk, PID_DATA0, zero, 8);
    pk[len - 1] ^= 0x55;
    n = usb_xfer(&h, 0, pk, len, 1, r);
    CRIT("K8", n == -1 && k.bad_crc == 1, "bad-CRC packet: reply %d, device bad CRCs %d", n, k.bad_crc);
    n = usb_in(&h, 0, 5, 1, &pid, d);
    CRIT("K8", n == -2, "after recovery: expected NAK, got %d", n);
  }

  double rate = PE_CLK_HZ / USB_T0;
  CRIT("K1", k.max_rate_err <= 0.015, "bit rate error %.2f%%", k.max_rate_err * 100);
  CRIT("K2", k.bad_coding == 0 && k.bad_crc == (clock_err == 0 ? 1 : 0) && k.pkts >= 19,
       "%d packets, %d undecodable, %d bad CRC", k.pkts, k.bad_coding, k.bad_crc);
  CRIT("K3", k.min_eop >= PE_NS(1250) - 1 && k.max_eop <= (int)(1.5e-6 * PE_CLK_HZ),
       "EOP SE0 %d..%d cycles", k.min_eop, k.max_eop);
  CRIT("K7", k.min_ipd >= 2 && k.max_ipd <= 7.5, "host handshake %.2f..%.2f bit times", k.min_ipd, k.max_ipd);
  no_errors(&b);
  printf("  %d packets; host %.0f b/s (%+.2f%%), EOP %.2f us, handshake %.1f-%.1f bit times after device EOP\n",
         k.pkts, rate, (rate / 1.5e6 - 1) * 100, k.min_eop * 1e6 / PE_CLK_HZ, k.min_ipd, k.max_ipd);
}

/* ======================================================================== */
/* 10BASE-T transmitter                                                      */

static int eth_send(host *h, int eng, const uint8_t *payload, int n, uint8_t *wire, int *wl) {
  uint8_t buf[1600];
  int len = eth_udp_frame(buf + 2, payload, n);
  int bits = 8 * len - 1;
  buf[0] = (uint8_t)(bits >> 8);
  buf[1] = (uint8_t)bits;
  if (wire) {
    memcpy(wire, buf + 2, (size_t)len);
    *wl = len;
  }
  return host_stream(h, eng, buf, len + 2, NULL, 0, 4000000);
}

static int eth_frame_ok(const uint8_t *f, int fl, const uint8_t *payload, int n) {
  return fl >= 64 && f[12] == 0x08 && f[13] == 0x00 && ip_checksum(f + 14, 20) == 0 &&
         (f[38] << 8 | f[39]) == 8 + n && !memcmp(f + 42, payload, (size_t)n);
}

static void test_eth(void) {
  printf("eth: 10BASE-T transmitter, UDP broadcast frames\n");
  board b;
  host h;
  eth_rx e;
  setup(&b, &h);
  eth_rx_init(&e, 2, 3);
  board_add_peer(&b, &e.base);
  host_load(&h, 1, &P_ETH, MAP_ETH, ETH_T0, 0);

  board_run(&b, (uint64_t)(0.050 * PE_CLK_HZ)); /* 50 ms idle */
  CRIT("E5", e.nlps >= 3, "%d link pulses in 50 ms", e.nlps);
  CRIT("E5", e.nlp_wmin == PE_NS(100) && e.nlp_wmax == PE_NS(100), "pulse width %d..%d cycles", e.nlp_wmin, e.nlp_wmax);
  CRIT("E5", e.nlp_gmin >= (uint64_t)(0.008 * PE_CLK_HZ) && e.nlp_gmax <= (uint64_t)(0.024 * PE_CLK_HZ),
       "pulse spacing %.2f..%.2f ms", e.nlp_gmin * 1e3 / PE_CLK_HZ, e.nlp_gmax * 1e3 / PE_CLK_HZ);
  double nlp_ms = e.nlp_gmin * 1e3 / PE_CLK_HZ;

  /* three frames queued back to back */
  const char *msgs[3] = {"hello from tiny tapeout", "frame two", "frame three, a bit longer"};
  for (int i = 0; i < 3; i++) eth_send(&h, 1, (const uint8_t *)msgs[i], (int)strlen(msgs[i]), NULL, NULL);
  host_wait(&h, 4000);
  int ok = e.nframes == 3;
  for (int i = 0; i < 3 && ok; i++)
    ok = eth_frame_ok(e.frame[i], e.flen[i], (const uint8_t *)msgs[i], (int)strlen(msgs[i]));
  CRIT("E2", ok && e.bad_pre == 0 && e.bad_fcs == 0 && e.bad_len == 0, "%d frames, %d bad FCS", e.nframes, e.bad_fcs);
  CRIT("E1", e.bad_manch == 0 && e.odd_bits == 0 && e.nframes == 3, "Manchester violations %d", e.bad_manch);
  CRIT("E4", e.ifg_min >= PE_NS(9600), "interframe gap %d cycles", e.ifg_min);

  /* maximum-size frame: 1500-byte IP packet */
  static uint8_t big[1472];
  for (int i = 0; i < 1472; i++) big[i] = (uint8_t)rnd();
  ok = eth_send(&h, 1, big, 1472, NULL, NULL);
  host_wait(&h, 2000);
  CRIT("E6", ok && e.nframes == 4 && e.flen[3] == 1518 && eth_frame_ok(e.frame[3], e.flen[3], big, 1472),
       "max frame: %d frames, length %d", e.nframes, e.nframes > 3 ? e.flen[3] : 0);
  CRIT("E3", e.tpidl_min >= PE_NS(250), "TP_IDL %d cycles", e.tpidl_min);
  no_errors(&b);
  printf("  %d frames, half-bit 50 ns, TP_IDL %d ns, gap >= %.1f us, link pulses %d ns every %.1f ms\n",
         e.nframes, (int)(e.tpidl_min * 1e9 / PE_CLK_HZ), e.ifg_min * 1e6 / PE_CLK_HZ,
         (int)(e.nlp_wmin * 1e9 / PE_CLK_HZ), nlp_ms);
  eth_rx_free(&e);
}

/* ======================================================================== */
/* All engines at once, reprogramming, pin remapping, checker sanity         */

static void test_four_engines(void) {
  printf("four engines at once: UART TX + UART RX + SPI + I2C\n");
  board b;
  host h;
  uart_peer u;
  spi_flash f;
  i2c_adt7420 d;
  setup(&b, &h);
  uart_peer_init(&u, 4, 5, PE_CLK_HZ / 115200, PE_CLK_HZ / 115200);
  u.echo = true;
  spi_flash_init(&f, 0, 1, 2, 3);
  i2c_adt7420_init(&d, 6, 7, 0x48);
  board_add_peer(&b, &u.base);
  board_add_peer(&b, &f.base);
  board_add_peer(&b, &d.base);
  host_load(&h, 0, &P_UTX, MAP_UTX, UART_T0, 0);
  host_load(&h, 1, &P_URX, MAP_URX, UART_T0, UART_T1);
  host_load(&h, 2, &P_SPI, MAP_SPI, 0, 0);
  host_load(&h, 3, &P_I2C, MAP_I2C, I2C_T0, I2C_T1);
  const uint8_t msg[4] = {'T', 'T', '0', '!'};
  for (int i = 0; i < 4; i++) host_put(&h, 0, msg[i]);
  uint8_t tx[4] = {0x9F, 0, 0, 0}, rx[4], v[2], a[3];
  int spi_ok = spi_xfer(&h, 2, tx, rx, 4) && rx[1] == 0xEF && rx[3] == 0x18;
  int i2c_ok = i2c_read(&h, 3, 0x48, 0x0B, v, 1, a) && v[0] == 0xCB;
  int uart_busy = u.nrx < 4; /* UART still in flight while SPI and I2C ran */
  uint8_t echo[4];
  int n = 0;
  while (n < 4 && host_get(&h, 1, &echo[n], 20 * UART_T0)) n++;
  CRIT("G5", spi_ok && i2c_ok && uart_busy && n == 4 && !memcmp(echo, msg, 4),
       "spi %d i2c %d overlap %d uart %d", spi_ok, i2c_ok, uart_busy, n);

  /* the same engine, new firmware: UART TX -> SPI on other pads is G3's job */
  no_errors(&b);
  free(f.mem);
}

static void test_reprogram_remap(void) {
  printf("reprogram + remap: engine 0 goes UART TX -> SPI, UART TX moves to uio[7]\n");
  board b;
  host h;
  uart_peer u;
  spi_flash f;
  setup(&b, &h);
  uart_peer_init(&u, 7, -1, PE_CLK_HZ / 115200, 0);
  spi_flash_init(&f, 6, 2, 1, 5); /* cs mosi miso sck */
  board_add_peer(&b, &u.base);
  board_add_peer(&b, &f.base);
  const int m_utx[4] = {7, 0, 0, 0}, m_spi[4] = {5, 2, 1, 6};
  host_load(&h, 0, &P_UTX, m_utx, UART_T0, 0);
  host_put(&h, 0, 0x42);
  host_wait(&h, 12 * UART_T0);
  CRIT("G3", u.nrx == 1 && u.rx[0] == 0x42, "UART TX on uio[7]");
  host_load(&h, 0, &P_SPI, m_spi, 0, 0);
  uint8_t tx[4] = {0x9F, 0, 0, 0}, rx[4];
  CRIT("G3", spi_xfer(&h, 0, tx, rx, 4) && rx[1] == 0xEF, "SPI after reload on remapped pads");
  no_errors(&b);
  free(f.mem);
}

static void test_conflict_detected(void) {
  printf("checker sanity: two engines driving one pad is reported\n");
  board b;
  host h;
  setup(&b, &h);
  b.quiet = true;
  host_load(&h, 0, &P_UTX, MAP_UTX, UART_T0, 0);
  host_load(&h, 1, &P_UTX, MAP_UTX, UART_T0, 0);
  host_wait(&h, 10);
  CRIT("G2", b.errors > 0, "conflict not detected");
}

/* ------------------------------------------------------------------------ */

int main(int argc, char **argv) {
  const char *dir = argc > 1 ? argv[1] : "programs";
  char path[512];
  struct {
    pe_prog *p;
    const char *f;
  } progs[] = {{&P_UTX, "uart_tx.pasm"},   {&P_URX, "uart_rx.pasm"},   {&P_SPI, "spi_master.pasm"},
               {&P_SPI6, "spi_div6.pasm"}, {&P_I2C, "i2c_master.pasm"}, {&P_USB, "usb_ls_host.pasm"},
               {&P_ETH, "eth10_tx.pasm"}};
  int np = (int)(sizeof progs / sizeof progs[0]);
  static const char *opn[] = {"jmp", "wait", "in", "out", "xch", "pp", "mov", "set"};
  printf("clock %.0f MHz, %d engines x %d words; program sizes and opcode use:\n  %-12s %5s", PE_CLK_HZ / 1e6,
         PE_CORES, PE_IMEM, "", "words");
  for (int o = 0; o < 8; o++) printf(" %4s", opn[o]);
  printf("\n");
  for (int i = 0; i < np; i++) {
    snprintf(path, sizeof path, "%s/%s", dir, progs[i].f);
    if (pe_assemble_file(path, progs[i].p)) return 2;
    int hist[8] = {0};
    for (int k = 0; k < progs[i].p->len; k++) hist[progs[i].p->code[k] >> 13]++;
    printf("  %-12s %5d", progs[i].p->name, progs[i].p->len);
    for (int o = 0; o < 8; o++) printf(" %4d", hist[o]);
    printf("\n");
    CRIT("G4", progs[i].p->len <= PE_IMEM, "%s is %d words", progs[i].p->name, progs[i].p->len);
  }

  test_selfcheck();
  test_reset();
  test_uart_tx();
  test_uart_rx(0.0);
  test_uart_rx(-0.03);
  test_uart_rx(+0.03);
  test_uart_framing();
  test_uart_echo();
  test_spi();
  test_spi_margin();
  test_i2c(0);
  test_i2c(PE_NS(10000));
  test_usb(0.0);
  test_usb(-0.015);
  test_usb(+0.015);
  test_eth();
  test_four_engines();
  test_reprogram_remap();
  test_conflict_detected();

  int bad = 0;
  printf("\ncriteria (see CRITERIA.md):\n");
  for (int i = 0; i < NCRIT; i++) {
    const char *r = crits[i].n == 0 ? "NOT RUN" : crits[i].fail ? "FAIL" : "PASS";
    if (crits[i].n == 0 || crits[i].fail) bad++;
    printf("  %-3s %-7s (%d checks%s)\n", crits[i].id, r, crits[i].n, crits[i].fail ? ", failures" : "");
  }
  printf("\n%d of %d criteria pass\n", NCRIT - bad, NCRIT);
  return bad ? 1 : 0;
}
