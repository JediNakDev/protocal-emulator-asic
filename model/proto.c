/*
 * Protocol coding helpers. See proto.h.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "proto.h"

#include <string.h>

uint8_t crc5_usb(const uint8_t *d, int nbits) {
  uint8_t crc = 0x1f;
  for (int i = 0; i < nbits; i++) {
    int bit = (d[i >> 3] >> (i & 7)) & 1;
    crc = ((crc ^ bit) & 1) ? (uint8_t)((crc >> 1) ^ 0x14) : (uint8_t)(crc >> 1);
  }
  return crc ^ 0x1f;
}

uint16_t crc16_usb(const uint8_t *d, int n) {
  uint16_t crc = 0xffff;
  for (int i = 0; i < n; i++) {
    crc ^= d[i];
    for (int k = 0; k < 8; k++) crc = (crc & 1) ? (uint16_t)((crc >> 1) ^ 0xa001) : (uint16_t)(crc >> 1);
  }
  return (uint16_t)~crc;
}

uint32_t crc32_eth(const uint8_t *d, int n) {
  uint32_t crc = 0xffffffffu;
  for (int i = 0; i < n; i++) {
    crc ^= d[i];
    for (int k = 0; k < 8; k++) crc = (crc & 1) ? (crc >> 1) ^ 0xedb88320u : crc >> 1;
  }
  return ~crc;
}

uint16_t ip_checksum(const uint8_t *d, int n) {
  uint32_t s = 0;
  for (int i = 0; i + 1 < n; i += 2) s += (uint32_t)(d[i] << 8 | d[i + 1]);
  if (n & 1) s += (uint32_t)(d[n - 1] << 8);
  while (s >> 16) s = (s & 0xffff) + (s >> 16);
  return (uint16_t)~s;
}

int usb_encode(const uint8_t *bytes, int n, uint8_t *line) {
  int nl = 0, ones = 0, lvl = 0; /* J */
  for (int i = 0; i < 8 * n; i++) {
    if ((bytes[i >> 3] >> (i & 7)) & 1) {
      ones++;
    } else {
      ones = 0;
      lvl ^= 1; /* NRZI: 0 is a transition */
    }
    line[nl++] = (uint8_t)lvl;
    if (ones == 6) { /* stuff a 0 */
      lvl ^= 1;
      line[nl++] = (uint8_t)lvl;
      ones = 0;
    }
  }
  return nl;
}

int usb_decode(const uint8_t *line, int n, uint8_t *bytes) {
  int prev = 0, ones = 0, nb = 0;
  for (int i = 0; i < n; i++) {
    int bit = line[i] == prev;
    prev = line[i];
    if (ones == 6) {
      if (bit) return -1; /* stuffing violation */
      ones = 0;
      continue;
    }
    ones = bit ? ones + 1 : 0;
    if ((nb & 7) == 0) bytes[nb >> 3] = 0;
    bytes[nb >> 3] |= (uint8_t)(bit << (nb & 7));
    nb++;
  }
  return (nb & 7) ? -1 : nb >> 3;
}

int usb_packet(uint8_t *out, int pid, const uint8_t *payload, int n) {
  int k = 0;
  out[k++] = 0x80;
  out[k++] = USB_PID(pid);
  if (pid == PID_DATA0 || pid == PID_DATA1) {
    memcpy(out + k, payload, (size_t)n);
    k += n;
    uint16_t crc = crc16_usb(payload, n);
    out[k++] = crc & 0xff;
    out[k++] = crc >> 8;
  }
  return k;
}

int usb_token(uint8_t *out, int pid, int addr, int ep) {
  uint8_t f[2] = {(uint8_t)((addr & 0x7f) | (ep & 1) << 7), (uint8_t)((ep >> 1) & 7)};
  uint8_t crc = crc5_usb(f, 11);
  out[0] = 0x80;
  out[1] = USB_PID(pid);
  out[2] = f[0];
  out[3] = (uint8_t)(f[1] | crc << 3);
  return 4;
}

int eth_udp_frame(uint8_t *out, const uint8_t *payload, int n) {
  int k = 0;
  for (int i = 0; i < 7; i++) out[k++] = 0x55;
  out[k++] = 0xd5;
  uint8_t *f = out + k;
  int m = 0;
  static const uint8_t hdr[14] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x02, 0x00,
                                  0x00, 0x00, 0x00, 0x01, 0x08, 0x00};
  memcpy(f, hdr, 14);
  m = 14;
  int iplen = 20 + 8 + n;
  uint8_t *ip = f + m;
  uint8_t iph[20] = {0x45, 0x00, (uint8_t)(iplen >> 8), (uint8_t)iplen, 0x00, 0x00, 0x40, 0x00,
                     0x40, 0x11, 0x00, 0x00, 192, 168, 0, 2, 192, 168, 0, 255};
  uint16_t cs = ip_checksum(iph, 20);
  iph[10] = (uint8_t)(cs >> 8);
  iph[11] = (uint8_t)cs;
  memcpy(ip, iph, 20);
  m += 20;
  int ulen = 8 + n;
  uint8_t udp[8] = {0x04, 0xd2, 0x13, 0x8d, (uint8_t)(ulen >> 8), (uint8_t)ulen, 0, 0}; /* 1234 -> 5005 */
  memcpy(f + m, udp, 8);
  m += 8;
  memcpy(f + m, payload, (size_t)n);
  m += n;
  while (m < 60) f[m++] = 0; /* minimum frame without FCS */
  uint32_t fcs = crc32_eth(f, m);
  for (int i = 0; i < 4; i++) f[m++] = (uint8_t)(fcs >> (8 * i));
  return k + m;
}
