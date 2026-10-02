/*
 * Protocol test suite: UART, SPI and I2C running as programs on the engine,
 * checked by independent peer models through the uio pins only.
 *
 * Board wiring (Tiny Tapeout demo board uio Pmod):
 *   uio[0] SPI CS    uio[1] SPI MOSI   uio[2] SPI MISO   uio[3] SPI SCK
 *   uio[4] UART TX   uio[5] UART RX    uio[6] I2C SCL    uio[7] I2C SDA
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host.h"
#include "peers.h"

#define UART_T0 ((uint16_t)(PE_CLK_HZ / 115200 + 0.5)) /* 434 at 50 MHz */
#define UART_T1 ((uint16_t)(UART_T0 / 2 - 1))           /* first tick mid start bit */
#define I2C_T0 ((uint16_t)PE_NS(1500))     /* SCL low 1.5 us */
#define I2C_T1 ((uint16_t)(PE_NS(1000) - 4)) /* SCL high 1.0 us incl. 4 cycles release + edge detect */
#define HOST_EDGE 2 /* chip cycles per RP2040 pin change */

static pe_prog P_UTX, P_URX, P_SPI, P_SPI6, P_I2C;
static int fails, checks;
static unsigned rng = 12345;
static unsigned rnd(void) { return rng = rng * 1103515245u + 12345u, rng >> 16; }

#define CHECK(cond, ...)                                                                      \
  do {                                                                                        \
    checks++;                                                                                 \
    if (!(cond)) {                                                                            \
      fails++;                                                                                \
      printf("  FAIL %s:%d: ", __FILE__, __LINE__);                                           \
      printf(__VA_ARGS__);                                                                    \
      printf("\n");                                                                           \
    }                                                                                         \
  } while (0)

static const int MAP_UTX[4] = {4, 4, 4, 4};
static const int MAP_URX[4] = {5, 5, 5, 5};
static const int MAP_SPI[4] = {3, 1, 2, 0}; /* sck mosi miso cs */
static const int MAP_I2C[4] = {7, 6, 7, 7}; /* sda scl */

static void setup(board *b, host *h) {
  board_init(b);
  host_init(h, b, HOST_EDGE);
  board_run(b, 10);
}

/* ------------------------------------------------------------------------ */
/* UART                                                                      */

static void test_reset_safe(void) {
  printf("reset: all uio pads are inputs\n");
  board b;
  host h;
  setup(&b, &h);
  CHECK(b.chip.uio_oe == 0, "uio_oe=0x%02x after reset", b.chip.uio_oe);
  CHECK(b.wire == 0xff, "wire=0x%02x", b.wire);
}

static void test_uart_tx(void) {
  printf("uart_tx: 115200 8N1 on uio[4]\n");
  board b;
  host h;
  uart_peer u;
  setup(&b, &h);
  uart_peer_init(&u, 4, -1, UART_T0, UART_T0);
  board_add_peer(&b, &u.base);
  host_load(&h, 0, &P_UTX, MAP_UTX, UART_T0, 0);
  uint8_t msg[48] = {0x00, 0xFF, 0x55, 0xAA, 'H', 'e', 'l', 'l', 'o'};
  int n = sizeof msg;
  for (int i = 9; i < n; i++) msg[i] = (uint8_t)rnd();
  uint64_t t0 = b.cycle;
  for (int i = 0; i < n; i++) host_put(&h, 0, msg[i]);
  while (u.nrx < n && b.cycle < t0 + (uint64_t)n * 10 * UART_T0 + 20000) board_step(&b);
  uint64_t el = b.cycle - t0;
  CHECK(u.nrx == n, "received %d of %d bytes", u.nrx, n);
  CHECK(!memcmp(u.rx, msg, (size_t)n), "data mismatch");
  CHECK(u.ferr == 0, "framing errors %d", u.ferr);
  CHECK(u.max_dev <= 1.0, "edge deviation %.2f cycles", u.max_dev);
  CHECK(el <= (uint64_t)(n + 2) * 10 * UART_T0, "back-to-back frames: %llu cycles", (unsigned long long)el);
  CHECK(b.errors == 0, "%d board errors", b.errors);
  printf("  %d bytes, baud %.0f, worst edge error %.2f cycles, %.1f frames/ms\n", u.nrx,
         PE_CLK_HZ / UART_T0, u.max_dev, n / (el / PE_CLK_HZ * 1e3));
}

static void test_uart_rx(double err) {
  printf("uart_rx: peer baud error %+.1f%%\n", err * 100);
  board b;
  host h;
  uart_peer u;
  setup(&b, &h);
  uart_peer_init(&u, -1, 5, UART_T0, UART_T0 * (1 + err));
  board_add_peer(&b, &u.base);
  host_load(&h, 1, &P_URX, MAP_URX, UART_T0, UART_T1);
  uint8_t msg[32];
  for (int i = 0; i < 32; i++) {
    msg[i] = (uint8_t)rnd();
    uart_peer_send(&u, msg[i], false);
  }
  uint8_t got[32];
  int n = 0;
  while (n < 32 && host_get(&h, 1, &got[n], 20 * UART_T0)) n++;
  CHECK(n == 32, "received %d of 32", n);
  CHECK(!memcmp(got, msg, (size_t)n), "data mismatch");
  CHECK(!((host_status(&h) >> 4) & ST_FLAG), "unexpected flag");
  CHECK(b.errors == 0, "%d board errors", b.errors);
}

static void test_uart_framing(void) {
  printf("uart_rx: framing error is flagged and the byte dropped\n");
  board b;
  host h;
  uart_peer u;
  setup(&b, &h);
  uart_peer_init(&u, -1, 5, UART_T0, UART_T0);
  u.gap = 3 * UART_T0;
  board_add_peer(&b, &u.base);
  host_load(&h, 1, &P_URX, MAP_URX, UART_T0, UART_T1);
  uart_peer_send(&u, 0x3C, true);
  uart_peer_send(&u, 0xA5, false);
  uint8_t v = 0;
  int ok = host_get(&h, 1, &v, 40 * UART_T0);
  CHECK(ok && v == 0xA5, "got %d 0x%02x", ok, v);
  CHECK((host_status(&h) >> 4) & ST_FLAG, "framing flag not set");
  host_clear_flags(&h, 2);
  CHECK(!((host_status(&h) >> 4) & ST_FLAG), "flag not cleared");
}

static void test_uart_echo(void) {
  printf("uart full duplex: core0 TX + core1 RX, USB-UART echoes\n");
  board b;
  host h;
  uart_peer u;
  setup(&b, &h);
  uart_peer_init(&u, 4, 5, UART_T0, UART_T0 * 1.01);
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
    uint8_t s = host_status(&h);
    if (sent < N && !(s & ST_TXFULL)) host_write_tx(&h, 0, tx[sent++]);
    if (s & (ST_RXNE << 4)) rx[got++] = host_pop(&h, 1);
  }
  CHECK(got == N, "echoed %d of %d", got, N);
  CHECK(!memcmp(tx, rx, N), "echo mismatch");
  CHECK(b.errors == 0, "%d board errors", b.errors);
}

/* ------------------------------------------------------------------------ */
/* SPI                                                                       */

static int spi_xfer(host *h, int core, const uint8_t *tx, uint8_t *rx, int n) {
  uint8_t buf[257];
  buf[0] = (uint8_t)(n - 1);
  memcpy(buf + 1, tx, (size_t)n);
  return host_stream(h, core, buf, n + 1, rx, n, 200000);
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
  host_wait(&h, 10);
  CHECK(b.chip.uio_oe == 0x0b, "spi pad directions 0x%02x", b.chip.uio_oe);

  uint8_t tx[256], rx[256];
  memset(tx, 0, sizeof tx);
  tx[0] = 0x9F;
  CHECK(spi_xfer(&h, 0, tx, rx, 4), "jedec xfer");
  CHECK(rx[1] == 0xEF && rx[2] == 0x40 && rx[3] == 0x18, "JEDEC ID %02x %02x %02x", rx[1], rx[2], rx[3]);
  printf("  JEDEC ID %02X %02X %02X\n", rx[1], rx[2], rx[3]);

  /* 0x03 read, 252 data bytes in one 256-byte frame */
  memset(tx, 0, sizeof tx);
  tx[0] = 0x03; tx[1] = 0x00; tx[2] = 0x10; tx[3] = 0x00;
  uint64_t c0 = b.cycle;
  uint64_t fr0 = (uint64_t)f.frames;
  f.max_period = 0;
  CHECK(spi_xfer(&h, 0, tx, rx, 256), "read xfer");
  int gap = f.max_period;
  uint64_t el = b.cycle - c0;
  CHECK(f.frames == (int)fr0 + 1, "frames");
  CHECK(!memcmp(rx + 4, f.mem + 0x1000, 252), "read data mismatch");
  printf("  252-byte read: %.2f MB/s incl. host traffic (%llu cycles), longest SCK period %d cycles\n",
         256 / ((double)el / PE_CLK_HZ) / 1e6, (unsigned long long)el, gap);

  /* 0x0B fast read with dummy byte, at an odd address */
  memset(tx, 0, sizeof tx);
  tx[0] = 0x0B; tx[1] = 0x00; tx[2] = 0x13; tx[3] = 0x21;
  CHECK(spi_xfer(&h, 0, tx, rx, 5 + 40), "fast read xfer");
  CHECK(!memcmp(rx + 5, f.mem + 0x1321, 40), "fast read mismatch");

  /* WREN, RDSR, page program, poll, read back */
  tx[0] = 0x06;
  spi_xfer(&h, 0, tx, rx, 1);
  tx[0] = 0x05; tx[1] = 0;
  spi_xfer(&h, 0, tx, rx, 2);
  CHECK(rx[1] & 2, "WEL not set: SR1=0x%02x", rx[1]);
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
  CHECK(!(rx[1] & 1), "flash stayed busy");
  memset(tx, 0, sizeof tx);
  tx[0] = 0x03; tx[1] = 0x00; tx[2] = 0x20; tx[3] = 0x00;
  spi_xfer(&h, 0, tx, rx, 36);
  CHECK(!memcmp(rx + 4, data, 32), "program/read-back mismatch");
  printf("  page program + %d status polls + read-back OK\n", polls);

  CHECK(f.min_period == 4, "min SCK period %d cycles", f.min_period);
  CHECK(f.min_high >= 2 && f.min_low >= 2, "SCK high %d low %d", f.min_high, f.min_low);
  CHECK(f.mode == 0, "mode %d", f.mode);
  CHECK(b.errors == 0, "%d board errors", b.errors);
  printf("  SCK %.1f MHz (period %d cycles, high>=%d low>=%d), CS high >= %d cycles\n",
         PE_CLK_HZ / f.min_period / 1e6, f.min_period, f.min_high, f.min_low, f.min_cs_high);
  free(f.mem);
}

/* Read through a flash whose MISO arrives extra_lat cycles late. */
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
  printf("spi: MISO timing margin vs. extra target delay (cycles)\n");
  const pe_prog *ps[2] = {&P_SPI, &P_SPI6};
  for (int k = 0; k < 2; k++) {
    int maxok = -1, period = 0;
    for (int lat = 0; lat <= 4; lat++) {
      int pr, ok = spi_read_ok(ps[k], lat, &pr);
      if (lat == 0) period = pr;
      if (ok && maxok == lat - 1) maxok = lat;
    }
    printf("  %-11s SCK %.2f MHz: reads correct with up to %d extra cycle(s) of MISO delay\n",
           ps[k]->name, PE_CLK_HZ / period / 1e6, maxok);
    CHECK(maxok == (k == 0 ? 0 : 2), "%s tolerates %d", ps[k]->name, maxok);
    CHECK(period == (k == 0 ? 4 : 6), "%s period %d", ps[k]->name, period);
  }
}

/* ------------------------------------------------------------------------ */
/* I2C                                                                       */

#define I2C_START 0x00
#define I2C_STOP 0x20
#define I2C_WR 0xC0      /* XFER, release SDA for the target's ACK */
#define I2C_RD_ACK 0x40  /* XFER 0xFF, controller ACKs */
#define I2C_RD_NACK 0xC0 /* XFER 0xFF, controller NACKs (last byte) */

static int i2c_read(host *h, int core, uint8_t addr, uint8_t reg, uint8_t *out, int n, uint8_t *acks) {
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
  int nr = 2 * (3 + n);
  if (!host_stream(h, core, c, k, r, nr, 2000000)) return 0;
  for (int i = 0; i < 3; i++) acks[i] = r[2 * i + 1];
  for (int i = 0; i < n; i++) out[i] = r[2 * (3 + i)];
  return 1;
}

static int i2c_write(host *h, int core, uint8_t addr, uint8_t reg, const uint8_t *d, int n, uint8_t *acks) {
  uint8_t c[64], r[64];
  int k = 0;
  c[k++] = I2C_START;
  c[k++] = I2C_WR; c[k++] = (uint8_t)(addr << 1);
  c[k++] = I2C_WR; c[k++] = reg;
  for (int i = 0; i < n; i++) { c[k++] = I2C_WR; c[k++] = d[i]; }
  c[k++] = I2C_STOP;
  int nr = 2 * (2 + n);
  if (!host_stream(h, core, c, k, r, nr, 2000000)) return 0;
  for (int i = 0; i < 2 + n; i++) acks[i] = r[2 * i + 1];
  return 1;
}

static void i2c_report(i2c_adt7420 *d) {
  double us = 1e6 / PE_CLK_HZ;
  printf("  SCL %.1f kHz; tLOW %.2f us, tHIGH %.2f us, tSU;DAT %.0f ns, tHD;STA %.2f us,\n"
         "  tSU;STA %.2f us, tSU;STO %.2f us, tBUF %.2f us (minimums)\n",
         PE_CLK_HZ / d->min_period / 1e3, d->min_low * us, d->min_high * us, d->min_sudat * us * 1e3,
         d->min_hdsta * us, d->min_susta * us, d->min_susto * us, d->min_buf * us);
}

static void i2c_check_timing(i2c_adt7420 *d) {
  /* I2C Fast-mode limits (UM10204 table 10) */
  CHECK(d->min_period >= PE_NS(2500), "SCL period %d < 2.5 us", d->min_period);
  CHECK(d->min_low >= PE_NS(1300), "tLOW %d", d->min_low);
  CHECK(d->min_high >= PE_NS(600), "tHIGH %d", d->min_high);
  CHECK(d->min_sudat >= PE_NS(100), "tSU;DAT %d", d->min_sudat);
  CHECK(d->min_hdsta >= PE_NS(600), "tHD;STA %d", d->min_hdsta);
  CHECK(d->min_susta >= PE_NS(600), "tSU;STA %d", d->min_susta);
  CHECK(d->min_susto >= PE_NS(600), "tSU;STO %d", d->min_susto);
  CHECK(d->min_buf >= PE_NS(1300), "tBUF %d", d->min_buf);
  CHECK(d->stretch == 0 || d->nstretch > 0, "target never stretched SCL");
}

static void test_i2c(int stretch) {
  printf("i2c: ADT7420 @0x48, 400 kHz%s\n", stretch ? ", target stretches SCL 10 us after every ACK" : "");
  board b;
  host h;
  i2c_adt7420 d;
  setup(&b, &h);
  i2c_adt7420_init(&d, 6, 7, 0x48);
  d.stretch = stretch;
  board_add_peer(&b, &d.base);
  host_load(&h, 0, &P_I2C, MAP_I2C, I2C_T0, I2C_T1);
  CHECK(b.chip.uio_oe == 0 && b.wire == 0xff, "I2C lines not released at idle");

  uint8_t v[8], a[8];
  CHECK(i2c_read(&h, 0, 0x48, 0x0B, v, 1, a), "id read");
  CHECK(a[0] == 0 && a[1] == 0 && a[2] == 0, "acks %d %d %d", a[0], a[1], a[2]);
  CHECK(v[0] == 0xCB, "ID 0x%02x", v[0]);
  printf("  ID register 0x%02X\n", v[0]);

  CHECK(i2c_read(&h, 0, 0x48, 0x00, v, 2, a), "temp read");
  CHECK(v[0] == 0x0C && v[1] == 0x80, "temp %02x %02x", v[0], v[1]);
  printf("  temperature %.4f C\n", ((v[0] << 8 | v[1]) >> 3) * 0.0625);

  uint8_t th[2] = {0x12, 0x34};
  CHECK(i2c_write(&h, 0, 0x48, 0x04, th, 2, a), "write");
  CHECK(a[0] == 0 && a[1] == 0 && a[2] == 0 && a[3] == 0, "write acks");
  CHECK(i2c_read(&h, 0, 0x48, 0x04, v, 2, a), "read back");
  CHECK(v[0] == 0x12 && v[1] == 0x34, "T_HIGH %02x %02x", v[0], v[1]);

  /* nobody at 0x50: address NACK */
  uint8_t c[4] = {I2C_START, I2C_WR, 0x50 << 1, I2C_STOP}, r[2];
  CHECK(host_stream(&h, 0, c, 4, r, 2, 200000), "nack xfer");
  CHECK(r[1] == 1, "absent device acked");

  host_wait(&h, 2000);
  CHECK(b.wire == 0xff, "bus not released after STOP");
  CHECK(d.starts == d.stops + 3, "starts %d stops %d", d.starts, d.stops);
  i2c_check_timing(&d);
  CHECK(b.errors == 0, "%d board errors", b.errors);
  i2c_report(&d);
}

/* ------------------------------------------------------------------------ */
/* Reprogramming, concurrency, pin remapping                                 */

static void test_reprogram_and_concurrency(void) {
  printf("one board, all peers: reload core0 UART->SPI, core1 I2C runs alongside\n");
  board b;
  host h;
  uart_peer u;
  spi_flash f;
  i2c_adt7420 d;
  setup(&b, &h);
  uart_peer_init(&u, 4, 5, UART_T0, UART_T0);
  spi_flash_init(&f, 0, 1, 2, 3);
  i2c_adt7420_init(&d, 6, 7, 0x48);
  board_add_peer(&b, &u.base);
  board_add_peer(&b, &f.base);
  board_add_peer(&b, &d.base);

  /* core0 = UART TX, core1 = I2C, both active at once */
  host_load(&h, 0, &P_UTX, MAP_UTX, UART_T0, 0);
  host_load(&h, 1, &P_I2C, MAP_I2C, I2C_T0, I2C_T1);
  const char *msg = "TT!";
  for (int i = 0; i < 3; i++) host_put(&h, 0, (uint8_t)msg[i]);
  uint8_t v[2], a[3];
  CHECK(i2c_read(&h, 1, 0x48, 0x0B, v, 1, a) && v[0] == 0xCB, "I2C during UART");
  host_wait(&h, 6 * 10 * UART_T0);
  CHECK(u.nrx == 3 && !memcmp(u.rx, msg, 3), "UART during I2C: %d bytes", u.nrx);

  /* same core, new firmware: SPI */
  host_load(&h, 0, &P_SPI, MAP_SPI, 0, 0);
  uint8_t tx[4] = {0x9F, 0, 0, 0}, rx[4];
  CHECK(spi_xfer(&h, 0, tx, rx, 4) && rx[1] == 0xEF && rx[3] == 0x18, "SPI after reload");
  CHECK(i2c_read(&h, 1, 0x48, 0x00, v, 2, a) && v[0] == 0x0C, "I2C still fine");
  CHECK(b.errors == 0, "%d board errors", b.errors);
  free(f.mem);
}

static void test_pin_remap(void) {
  printf("pin remap: UART TX on uio[7], SPI with swapped MOSI/MISO pads\n");
  board b;
  host h;
  uart_peer u;
  spi_flash f;
  setup(&b, &h);
  uart_peer_init(&u, 7, -1, UART_T0, UART_T0);
  spi_flash_init(&f, 6, 2, 1, 5); /* cs mosi miso sck */
  board_add_peer(&b, &u.base);
  board_add_peer(&b, &f.base);
  const int m_utx[4] = {7, 7, 7, 7}, m_spi[4] = {5, 2, 1, 6};
  host_load(&h, 0, &P_UTX, m_utx, UART_T0, 0);
  host_load(&h, 1, &P_SPI, m_spi, 0, 0);
  host_put(&h, 0, 0x42);
  uint8_t tx[4] = {0x9F, 0, 0, 0}, rx[4];
  CHECK(spi_xfer(&h, 1, tx, rx, 4) && rx[1] == 0xEF, "remapped SPI");
  host_wait(&h, 12 * UART_T0);
  CHECK(u.nrx == 1 && u.rx[0] == 0x42, "remapped UART");
  CHECK(b.errors == 0, "%d board errors", b.errors);
  free(f.mem);
}

static void test_conflict_detected(void) {
  printf("env check: two engines driving one pad is reported\n");
  board b;
  host h;
  setup(&b, &h);
  b.quiet = true;
  host_load(&h, 0, &P_UTX, MAP_UTX, UART_T0, 0);
  host_load(&h, 1, &P_UTX, MAP_UTX, UART_T0, 0);
  host_wait(&h, 10);
  CHECK(b.errors > 0, "conflict not detected");
}

/* ------------------------------------------------------------------------ */

static void report_programs(void) {
  pe_prog *ps[] = {&P_UTX, &P_URX, &P_SPI, &P_SPI6, &P_I2C};
  static const char *opn[] = {"jmp", "wait", "in", "out", "xch", "push/pull", "mov", "set"};
  printf("programs (limit %d words of 16 bits per engine), opcode use:\n  %-12s %5s", PE_IMEM, "", "words");
  for (int o = 0; o < 8; o++) printf(" %5s", opn[o]);
  printf("\n");
  for (int i = 0; i < 5; i++) {
    int hist[8] = {0};
    for (int k = 0; k < ps[i]->len; k++) hist[ps[i]->code[k] >> 13]++;
    printf("  %-12s %5d", ps[i]->name, ps[i]->len);
    for (int o = 0; o < 8; o++) printf(" %5d", hist[o]);
    printf("\n");
  }
}

int main(int argc, char **argv) {
  const char *dir = argc > 1 ? argv[1] : "programs";
  char path[512];
  struct { pe_prog *p; const char *f; } progs[] = {
      {&P_UTX, "uart_tx.pasm"}, {&P_URX, "uart_rx.pasm"}, {&P_SPI, "spi_master.pasm"},
      {&P_SPI6, "spi_div6.pasm"}, {&P_I2C, "i2c_master.pasm"}};
  for (int i = 0; i < 5; i++) {
    snprintf(path, sizeof path, "%s/%s", dir, progs[i].f);
    if (pe_assemble_file(path, progs[i].p)) return 2;
  }
  report_programs();

  test_reset_safe();
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
  test_reprogram_and_concurrency();
  test_pin_remap();
  test_conflict_detected();

  printf("\n%d checks, %d failed\n", checks, fails);
  return fails ? 1 : 0;
}
