/*
 * Two-pass assembler for the protocol engine (.pasm, PIO-like syntax).
 *
 *   .program name            .pin name idx [od]       .side_set pin [opt]
 *   .out pin  .in pin  .jmp_pin pin                    .shift out|in left|right
 *   .autopull  .autopush     .wrap_target  .wrap  .entry
 *
 *   jmp [!x|x--|!y|y--|pin|!pin|!osre] label
 *   wait 0|1 pin name [resync]      in src, n      out dst, n      xch [n]
 *   push [noblock]   pull [noblock]   mov dst, [~|::]src   nop
 *   set pin name, v | pins, v | pindirs, v | x, v | y, v | flag, v | timer, 0|1
 *   modifiers: side v   [n]   [tick]
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pe.h"

#define MAXTOK 16
#define MAXLINES 256

typedef struct {
  char tok[MAXTOK][32];
  int n, line;
} src_line;

typedef struct {
  pe_prog *p;
  char lab[64][32];
  int labaddr[64], nlab;
  src_line ins[PE_IMEM + 1];
  int nins;
  int smode, sidep, outp, inp, jmpp, out_r, in_r, apull, apush, wbot, wtop, entry;
  int have_wrap;
  char *err;
  int errlen;
} asm_ctx;

static int fail(asm_ctx *a, int line, const char *msg, const char *tok) {
  snprintf(a->err, (size_t)a->errlen, "line %d: %s%s%s", line, msg, tok ? " " : "", tok ? tok : "");
  return -1;
}

static int parse_num(const char *s, int *v) {
  char *end;
  long r;
  if (s[0] == '0' && (s[1] == 'b' || s[1] == 'B'))
    r = strtol(s + 2, &end, 2);
  else
    r = strtol(s, &end, 0);
  if (*s == 0 || *end) return -1;
  *v = (int)r;
  return 0;
}

static int pin_idx(asm_ctx *a, const char *s) {
  for (int i = 0; i < PE_LPINS; i++)
    if (a->p->pin[i][0] && !strcmp(a->p->pin[i], s)) return i;
  int v;
  if ((s[0] == 'p' || s[0] == 'P') && !parse_num(s + 1, &v) && v >= 0 && v < PE_LPINS) return v;
  if (!parse_num(s, &v) && v >= 0 && v < PE_LPINS) return v;
  return -1;
}

static int find_label(asm_ctx *a, const char *s) {
  for (int i = 0; i < a->nlab; i++)
    if (!strcmp(a->lab[i], s)) return a->labaddr[i];
  int v;
  if (!parse_num(s, &v)) return v;
  return -1;
}

static int tokenize(char *s, src_line *l) {
  l->n = 0;
  while (*s) {
    while (*s && (isspace((unsigned char)*s) || *s == ',')) s++;
    if (!*s) break;
    if (l->n == MAXTOK) return -1;
    int k = 0;
    while (*s && !isspace((unsigned char)*s) && *s != ',' && k < 31) l->tok[l->n][k++] = *s++;
    l->tok[l->n][k] = 0;
    l->n++;
  }
  return 0;
}

static int directive(asm_ctx *a, src_line *l) {
  const char *d = l->tok[0];
  int v;
  if (!strcmp(d, ".program") && l->n >= 2) {
    snprintf(a->p->name, sizeof a->p->name, "%s", l->tok[1]);
  } else if (!strcmp(d, ".pin") && l->n >= 3) {
    if (parse_num(l->tok[2], &v) || v < 0 || v >= PE_LPINS) return fail(a, l->line, "bad pin index", l->tok[2]);
    snprintf(a->p->pin[v], sizeof a->p->pin[v], "%s", l->tok[1]);
    if (l->n >= 4 && !strcmp(l->tok[3], "od")) a->p->cfg[CFG_PMAP0 + v] |= 8;
  } else if (!strcmp(d, ".side_set") && l->n >= 2) {
    if ((a->sidep = pin_idx(a, l->tok[1])) < 0) return fail(a, l->line, "unknown pin", l->tok[1]);
    a->smode = (l->n >= 3 && !strcmp(l->tok[2], "opt")) ? SIDE_OPT : SIDE_ON;
  } else if (!strcmp(d, ".out") && l->n >= 2) {
    if ((a->outp = pin_idx(a, l->tok[1])) < 0) return fail(a, l->line, "unknown pin", l->tok[1]);
  } else if (!strcmp(d, ".in") && l->n >= 2) {
    if ((a->inp = pin_idx(a, l->tok[1])) < 0) return fail(a, l->line, "unknown pin", l->tok[1]);
  } else if (!strcmp(d, ".jmp_pin") && l->n >= 2) {
    if ((a->jmpp = pin_idx(a, l->tok[1])) < 0) return fail(a, l->line, "unknown pin", l->tok[1]);
  } else if (!strcmp(d, ".shift") && l->n >= 3) {
    int r = !strcmp(l->tok[2], "right");
    if (!strcmp(l->tok[1], "out")) a->out_r = r;
    else a->in_r = r;
  } else if (!strcmp(d, ".autopull")) {
    a->apull = 1;
  } else if (!strcmp(d, ".autopush")) {
    a->apush = 1;
  } else if (!strcmp(d, ".wrap_target")) {
    a->wbot = a->nins;
  } else if (!strcmp(d, ".wrap")) {
    a->wtop = a->nins - 1;
    a->have_wrap = 1;
  } else if (!strcmp(d, ".entry")) {
    a->entry = a->nins;
  } else {
    return fail(a, l->line, "unknown directive", d);
  }
  return 0;
}

static int encode(asm_ctx *a, src_line *l, uint16_t *out) {
  int side = -1, dly = 0, tick = 0, n = 0;
  char t[MAXTOK][32];
  for (int i = 0; i < l->n; i++) {
    const char *s = l->tok[i];
    if (!strcmp(s, "side")) {
      if (i + 1 >= l->n || parse_num(l->tok[i + 1], &side)) return fail(a, l->line, "bad side", NULL);
      i++;
    } else if (s[0] == '[') {
      char buf[32];
      snprintf(buf, sizeof buf, "%s", s + 1);
      char *e = strchr(buf, ']');
      if (!e) return fail(a, l->line, "bad delay", s);
      *e = 0;
      if (!strcmp(buf, "tick")) tick = 1;
      else if (parse_num(buf, &dly)) return fail(a, l->line, "bad delay", s);
    } else {
      snprintf(t[n++], 32, "%s", s);
    }
  }

  /* delay / side-set field */
  int field;
  int maxd = a->smode == SIDE_NONE ? 30 : a->smode == SIDE_ON ? 14 : 6;
  if (dly > maxd) return fail(a, l->line, "delay too large for side-set mode", NULL);
  int d = tick ? maxd + 1 : dly;
  if (a->smode == SIDE_NONE) {
    if (side >= 0) return fail(a, l->line, "side without .side_set", NULL);
    field = d;
  } else if (a->smode == SIDE_ON) {
    if (side < 0) return fail(a, l->line, "side-set is mandatory in this program", NULL);
    field = (side & 1) << 4 | d;
  } else {
    field = (side >= 0 ? 16 | (side & 1) << 3 : 0) | d;
  }

  int op, arg = 0, v;
  const char *m = t[0];
  if (!strcmp(m, "nop")) {
    op = OP_MOV;
    arg = MV_Y << 5 | MV_Y;
  } else if (!strcmp(m, "jmp")) {
    op = OP_JMP;
    int cond = JC_ALWAYS;
    const char *tgt = t[1];
    if (n == 3) {
      static const char *cn[] = {"", "!x", "x--", "!y", "y--", "pin", "!pin", "!osre"};
      cond = -1;
      for (int i = 1; i < 8; i++)
        if (!strcmp(t[1], cn[i])) cond = i;
      if (cond < 0) return fail(a, l->line, "bad jmp condition", t[1]);
      tgt = t[2];
    } else if (n != 2) {
      return fail(a, l->line, "jmp syntax", NULL);
    }
    int ad = find_label(a, tgt);
    if (ad < 0 || ad >= PE_IMEM) return fail(a, l->line, "unknown label", tgt);
    arg = cond << 5 | ad;
  } else if (!strcmp(m, "wait")) {
    op = OP_WAIT;
    if (n < 4 || strcmp(t[2], "pin") || parse_num(t[1], &v)) return fail(a, l->line, "wait syntax", NULL);
    int p = pin_idx(a, t[3]);
    if (p < 0) return fail(a, l->line, "unknown pin", t[3]);
    arg = (v & 1) << 7 | p;
    if (n >= 5 && !strcmp(t[4], "resync")) arg |= 0x40;
  } else if (!strcmp(m, "in") || !strcmp(m, "out")) {
    int isin = m[0] == 'i';
    op = isin ? OP_IN : OP_OUT;
    if (n != 3 || parse_num(t[2], &v) || v < 1 || v > 8) return fail(a, l->line, "in/out syntax", NULL);
    static const char *sn[] = {"pins", "x", "y", "null", "pindirs", "pc", "isr", "osr"};
    int s = -1;
    for (int i = 0; i < 8; i++)
      if (!strcmp(t[1], sn[i])) s = i;
    if (s < 0 || (isin && (s == 4 || s == 5)) || (!isin && s == 7))
      return fail(a, l->line, "bad in/out operand", t[1]);
    arg = s << 5 | (v & 7);
  } else if (!strcmp(m, "xch")) {
    op = OP_XCH;
    v = 1;
    if (n >= 2 && (parse_num(t[1], &v) || v < 1 || v > 8)) return fail(a, l->line, "xch count", NULL);
    arg = v & 7;
  } else if (!strcmp(m, "push") || !strcmp(m, "pull")) {
    op = OP_PP;
    arg = (m[1] == 'u' && m[2] == 'l') ? 0x80 : 0;
    if (!(n >= 2 && !strcmp(t[1], "noblock"))) arg |= 0x20;
  } else if (!strcmp(m, "mov")) {
    op = OP_MOV;
    if (n != 3) return fail(a, l->line, "mov syntax", NULL);
    static const char *mn[] = {"pins", "x", "y", "null", "", "pc", "isr", "osr"};
    const char *s = t[2];
    int mop = MOP_NONE;
    if (s[0] == '~' || s[0] == '!') { mop = MOP_INV; s++; }
    else if (s[0] == ':' && s[1] == ':') { mop = MOP_REV; s += 2; }
    int ds = -1, ss = -1;
    for (int i = 0; i < 8; i++) {
      if (mn[i][0] && !strcmp(t[1], mn[i])) ds = i;
      if (mn[i][0] && !strcmp(s, mn[i])) ss = i;
    }
    if (ds < 0 || ss < 0 || ss == MV_PC) return fail(a, l->line, "bad mov operand", NULL);
    arg = ds << 5 | mop << 3 | ss;
  } else if (!strcmp(m, "set")) {
    op = OP_SET;
    if (n >= 4 && !strcmp(t[1], "pin")) {
      int p = pin_idx(a, t[2]);
      if (p < 0) return fail(a, l->line, "unknown pin", t[2]);
      if (parse_num(t[3], &v)) return fail(a, l->line, "bad value", t[3]);
      arg = SET_PIN << 5 | (v & 1) << 2 | p;
    } else {
      static const char *dn[] = {"", "pins", "pindirs", "x", "y", "flag", "timer"};
      int ds = -1;
      for (int i = 1; i < 7; i++)
        if (n == 3 && !strcmp(t[1], dn[i])) ds = i;
      if (ds < 0 || parse_num(t[2], &v) || v < 0 || v > 31) return fail(a, l->line, "set syntax", NULL);
      arg = ds << 5 | v;
    }
  } else {
    return fail(a, l->line, "unknown mnemonic", m);
  }
  *out = (uint16_t)(op << 13 | field << 8 | arg);
  return 0;
}

int pe_assemble(const char *src, pe_prog *p, char *err, int errlen) {
  asm_ctx *a = calloc(1, sizeof *a);
  memset(p, 0, sizeof *p);
  a->p = p;
  a->err = err;
  a->errlen = errlen;
  a->wtop = -1;
  int rc = 0, lineno = 0;
  const char *s = src;
  while (*s && rc == 0) {
    char line[256];
    int k = 0;
    lineno++;
    while (*s && *s != '\n') {
      if (k < 255) line[k++] = *s;
      s++;
    }
    if (*s) s++;
    line[k] = 0;
    char *c = strchr(line, ';');
    if (c) *c = 0;
    c = strstr(line, "//");
    if (c) *c = 0;
    src_line l;
    if (tokenize(line, &l)) { rc = fail(a, lineno, "too many tokens", NULL); break; }
    l.line = lineno;
    int first = 0;
    /* labels */
    while (first < l.n && l.tok[first][strlen(l.tok[first]) - 1] == ':') {
      l.tok[first][strlen(l.tok[first]) - 1] = 0;
      snprintf(a->lab[a->nlab], 32, "%s", l.tok[first]);
      a->labaddr[a->nlab++] = a->nins;
      first++;
    }
    if (first == l.n) continue;
    if (first) {
      for (int i = first; i < l.n; i++) memcpy(l.tok[i - first], l.tok[i], 32);
      l.n -= first;
    }
    if (l.tok[0][0] == '.') {
      rc = directive(a, &l);
    } else {
      if (a->nins >= PE_IMEM) { rc = fail(a, lineno, "program exceeds instruction memory", NULL); break; }
      a->ins[a->nins++] = l;
    }
  }
  for (int i = 0; i < a->nins && rc == 0; i++) rc = encode(a, &a->ins[i], &p->code[i]);
  if (rc == 0) {
    p->len = a->nins;
    if (!a->have_wrap) a->wtop = a->nins - 1;
    p->cfg[CFG_PINSEL] = (uint8_t)(a->outp | a->inp << 2 | a->sidep << 4 | a->jmpp << 6);
    p->cfg[CFG_MODE] = (uint8_t)(a->smode | a->out_r << 2 | a->in_r << 3 | a->apull << 4 | a->apush << 5);
    p->cfg[CFG_WRAP_BOT] = (uint8_t)a->wbot;
    p->cfg[CFG_WRAP_TOP] = (uint8_t)a->wtop;
    p->cfg[CFG_ENTRY] = (uint8_t)a->entry;
  }
  free(a);
  return rc;
}

int pe_assemble_file(const char *path, pe_prog *p) {
  FILE *f = fopen(path, "rb");
  if (!f) {
    fprintf(stderr, "cannot open %s\n", path);
    return -1;
  }
  static char buf[16384];
  size_t n = fread(buf, 1, sizeof buf - 1, f);
  fclose(f);
  buf[n] = 0;
  char err[256];
  if (pe_assemble(buf, p, err, sizeof err)) {
    fprintf(stderr, "%s: %s\n", path, err);
    return -1;
  }
  return 0;
}

void pe_disasm(const pe_prog *p, uint16_t w, char *buf, int len) {
  static const char *opn[] = {"jmp", "wait", "in", "out", "xch", "push/pull", "mov", "set"};
  int smode = p->cfg[CFG_MODE] & 3, f = (w >> 8) & 31;
  char sd[32] = "";
  if (smode == SIDE_ON) snprintf(sd, sizeof sd, " side %d [%s%d]", f >> 4, (f & 15) == 15 ? "tick:" : "", f & 15);
  else if (smode == SIDE_OPT && (f & 16)) snprintf(sd, sizeof sd, " side %d [%d]", (f >> 3) & 1, f & 7);
  else snprintf(sd, sizeof sd, " [%d]", smode == SIDE_OPT ? f & 7 : f);
  snprintf(buf, (size_t)len, "%-9s arg=0x%02x%s", opn[w >> 13], w & 0xff, sd);
}
