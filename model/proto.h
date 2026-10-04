/*
 * Protocol coding helpers shared by the RP2040 host model and the peers:
 * CRCs, USB NRZI/bit stuffing, Ethernet/IPv4/UDP framing.
 * The test suite checks them against published check values (criterion G6).
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#ifndef PROTO_H
#define PROTO_H

#include <stdint.h>

uint8_t crc5_usb(const uint8_t *d, int nbits);   /* CRC-5/USB, LSB first     */
uint16_t crc16_usb(const uint8_t *d, int n);     /* CRC-16/USB               */
uint32_t crc32_eth(const uint8_t *d, int n);     /* CRC-32 (IEEE 802.3)      */
uint16_t ip_checksum(const uint8_t *d, int n);

/* USB PIDs (4-bit) */
enum { PID_OUT = 0x1, PID_IN = 0x9, PID_SETUP = 0xD, PID_DATA0 = 0x3, PID_DATA1 = 0xB,
       PID_ACK = 0x2, PID_NAK = 0xA, PID_STALL = 0xE };
#define USB_PID(p) ((uint8_t)((p) | ((~(p) & 15) << 4)))

/* Packet bytes (SYNC, PID, ...) -> line states (1 = K, 0 = J) starting from J,
 * with bit stuffing. Returns the number of line states. */
int usb_encode(const uint8_t *bytes, int n, uint8_t *line);
/* Line states (first one after J idle) -> bytes; returns byte count or -1
 * on a stuffing error or a partial byte. */
int usb_decode(const uint8_t *line, int n, uint8_t *bytes);
/* SYNC + PID [+ payload + CRC16] packet bytes; returns length */
int usb_packet(uint8_t *out, int pid, const uint8_t *payload, int n);
int usb_token(uint8_t *out, int pid, int addr, int ep);

/* Preamble, SFD, broadcast Ethernet/IPv4/UDP frame with padding and FCS.
 * Returns the number of bytes on the wire. */
int eth_udp_frame(uint8_t *out, const uint8_t *payload, int n);

#endif
