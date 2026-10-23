/*
 * fec.c – Energieverwürfelung, RS(204,188), Faltungsinterleaver, Faltungscoder + Punktierung
 * (EN 300 744 Kap. 4.3 / EN 300 429 Kap. 7). Alles ganzzahlig, tabellengetrieben.
 */
#include "dvbmod.h"
#include <string.h>
#include <stdlib.h>

/* ============================================================== Energieverwürfelung */
/* PRBS 1 + x^14 + x^15, Startwert 100101010000000, 8-Paket-Periode (1503 Bytes).
   Die Sequenz wird einmal berechnet; während der 7 nicht invertierten Sync-Bytes läuft der
   Generator weiter, sein Ausgang ist dort aber gesperrt (Sequenzbytes = 0). */
void dvb_scrambler_init(dvb_scrambler_t *s)
{
    uint16_t reg = 0x4A80; /* Bits r1..r15 = 1 0 0 1 0 1 0 1 0 0 0 0 0 0 0, r1 = MSB (Bit 14) */
    for (int p = 0; p < 8; p++) {
        for (int i = 0; i < TS_PKT; i++) {
            uint8_t b = 0;
            if (i == 0 && p == 0) { s->seq[0] = 0; continue; }  /* 0xB8 wird direkt gesetzt */
            for (int k = 0; k < 8; k++) {
                int out = ((reg >> 1) ^ reg) & 1;    /* r14 xor r15 (Bit 1, Bit 0) */
                reg = (uint16_t)((reg >> 1) | (out << 14));
                b = (uint8_t)((b << 1) | out);
            }
            s->seq[p * TS_PKT + i] = (i == 0) ? 0 : b;   /* Sync-Bytes unverändert */
        }
    }
    s->pkt = 0;
}

void dvb_scramble_packet(dvb_scrambler_t *s, uint8_t *pkt)
{
    const uint8_t *q = s->seq + s->pkt * TS_PKT;
    if (s->pkt == 0) pkt[0] = TS_NSYNC;
    for (int i = 1; i < TS_PKT; i++) pkt[i] ^= q[i];
    s->pkt = (s->pkt + 1) & 7;
}

/* ============================================================== RS(204,188) */
static uint8_t gf_exp[512], gf_log[256];
/* 16 Paritätsbytes als 4×uint32 (little endian: Byte 0 = p[0] = höchstes Polynom-Glied). */
static uint32_t rs_tab[4][256];
static int rs_ready = 0;

static uint8_t gf_mul(uint8_t a, uint8_t b)
{
    if (!a || !b) return 0;
    return gf_exp[gf_log[a] + gf_log[b]];
}

void dvb_rs_init(void)
{
    if (rs_ready) return;
    int x = 1;
    for (int i = 0; i < 255; i++) {
        gf_exp[i] = (uint8_t)x; gf_log[x] = (uint8_t)i;
        x <<= 1; if (x & 0x100) x ^= 0x11d;
    }
    for (int i = 255; i < 512; i++) gf_exp[i] = gf_exp[i - 255];
    /* Generatorpolynom g(x) = Π_{i=0}^{15} (x + α^i); g[j] = Koeffizient von x^j, g[16] = 1 */
    uint8_t g[17] = {1};
    int deg = 0;
    for (int i = 0; i < 16; i++) {
        uint8_t root = gf_exp[i];
        uint8_t ng[17] = {0};
        for (int j = 0; j <= deg; j++) {
            ng[j + 1] ^= g[j];                 /* x * g */
            ng[j]     ^= gf_mul(g[j], root);   /* root * g */
        }
        deg++;
        memcpy(g, ng, sizeof g);
    }
    /* Tabellen: für Rückkopplungsbyte b: p[i] ^= b * g[15-i]  (p[0] gehört zu x^15) */
    for (int b = 0; b < 256; b++) {
        uint8_t t[16];
        for (int i = 0; i < 16; i++) t[i] = gf_mul((uint8_t)b, g[15 - i]);
        for (int w = 0; w < 4; w++)
            rs_tab[w][b] = (uint32_t)t[4 * w] | (uint32_t)t[4 * w + 1] << 8 | (uint32_t)t[4 * w + 2] << 16 | (uint32_t)t[4 * w + 3] << 24;
    }
    rs_ready = 1;
}

void dvb_rs_encode(const uint8_t *in, uint8_t *out)
{
    uint32_t p0 = 0, p1 = 0, p2 = 0, p3 = 0;   /* p0 Byte 0 = Paritätsregister p[0] (höchstes Glied) */
    const uint32_t *t0 = rs_tab[0], *t1 = rs_tab[1], *t2 = rs_tab[2], *t3 = rs_tab[3];
    for (int i = 0; i < TS_PKT; i++) {
        unsigned b = (in[i] ^ p0) & 0xFF;
        p0 = (p0 >> 8) | (p1 << 24); p1 = (p1 >> 8) | (p2 << 24); p2 = (p2 >> 8) | (p3 << 24); p3 >>= 8;
        p0 ^= t0[b]; p1 ^= t1[b]; p2 ^= t2[b]; p3 ^= t3[b];
    }
    if (out != in) memcpy(out, in, TS_PKT);
    uint32_t w[4] = {p0, p1, p2, p3};
    for (int i = 0; i < 16; i++) out[TS_PKT + i] = (uint8_t)(w[i >> 2] >> (8 * (i & 3)));
}

int dvb_rs_check(const uint8_t *pkt)
{
    /* Syndrome S_i = c(α^i), i = 0..15 */
    for (int i = 0; i < 16; i++) {
        uint8_t s = 0, a = gf_exp[i];
        for (int j = 0; j < RS_PKT; j++) s = (uint8_t)(gf_mul(s, a) ^ pkt[j]);
        if (s) return 0;
    }
    return 1;
}

/* ============================================================== Faltungsinterleaver */
/* I = 12 Zweige, Zweig j verzögert um 17·j Zellen à 12 Byte = j·204 Byte = j Pakete.
   Das Sync-Byte (Index 0) liegt immer in Zweig 0 (204 = 12·17). */
void dvb_convint_init(dvb_convint_t *ci) { memset(ci, 0, sizeof *ci); }

void dvb_convint_packet(dvb_convint_t *ci, const uint8_t *in, uint8_t *out)
{
    memcpy(ci->hist[ci->pos], in, RS_PKT);
    const uint8_t *src[12];
    for (int j = 0; j < 12; j++) src[j] = ci->hist[(ci->pos + 12 - j) % 12];
    for (int i = 0; i < RS_PKT; i += 12) {
        out[i + 0]  = src[0][i + 0];  out[i + 1]  = src[1][i + 1];  out[i + 2]  = src[2][i + 2];
        out[i + 3]  = src[3][i + 3];  out[i + 4]  = src[4][i + 4];  out[i + 5]  = src[5][i + 5];
        out[i + 6]  = src[6][i + 6];  out[i + 7]  = src[7][i + 7];  out[i + 8]  = src[8][i + 8];
        out[i + 9]  = src[9][i + 9];  out[i + 10] = src[10][i + 10]; out[i + 11] = src[11][i + 11];
    }
    ci->pos = (ci->pos + 1) % 12;
}

/* ============================================================== Faltungscoder + Punktierung */
const char *dvb_coderate_name[5] = {"1/2", "2/3", "3/4", "5/6", "7/8"};
const int dvb_coderate_num[5] = {1, 2, 3, 5, 7};
const int dvb_coderate_den[5] = {2, 3, 4, 6, 8};

/* Nibble-Tabelle: [Zustand 64][Nibble 16] → 8 Bits X0 Y0 X1 Y1 X2 Y2 X3 Y3 (MSB zuerst) */
static uint8_t cc_tab[64][16];
static uint8_t cc_next[64][16];   /* Folgezustand nach dem Nibble (jüngstes Bit = Bit 5) */
static int cc_ready = 0;

static int parity7(unsigned x) { x ^= x >> 4; x ^= x >> 2; x ^= x >> 1; return x & 1; }

static void cc_build(void)
{
    if (cc_ready) return;
    for (unsigned st = 0; st < 64; st++)
        for (unsigned nib = 0; nib < 16; nib++) {
            unsigned s = st, o = 0;
            for (int k = 3; k >= 0; k--) {
                unsigned u = (nib >> k) & 1;
                unsigned w = (u << 6) | s;             /* u_i, u_{i-1}..u_{i-6} */
                unsigned X = parity7(w & 0171);        /* G1 = 171 oktal */
                unsigned Y = parity7(w & 0133);        /* G2 = 133 oktal */
                o = (o << 2) | (X << 1) | Y;
                s = (w >> 1) & 63;
            }
            cc_tab[st][nib] = (uint8_t)o;
            cc_next[st][nib] = (uint8_t)s;
        }
    cc_ready = 1;
}

/* Punktiermuster (EN 300 744 Tabelle 3): X-, Y-Maske je Periode, Bit 0 = erste Position */
/* Bit k der Maske = Position k+1 der Tabelle (LSB = X1/Y1).
   1/2: X 1 / Y 1; 2/3: X 10 / Y 11; 3/4: X 101 / Y 110; 5/6: X 10101 / Y 11010; 7/8: X 1000101 / Y 1111010
   Sendefolgen: X1Y1 | X1Y1Y2 | X1Y1Y2X3 | X1Y1Y2X3Y4X5 | X1Y1Y2Y3Y4X5Y6X7 (Tabelle 2, EN 300 744 V1.6.1) */
static const uint8_t punct_x[5] = {0x01, 0x01, 0x05, 0x15, 0x51};
static const uint8_t punct_y[5] = {0x01, 0x03, 0x03, 0x0B, 0x2F};

void dvb_convcode_init(dvb_convcode_t *c, int coderate)
{
    cc_build();
    memset(c, 0, sizeof *c);
    c->cr = coderate;
    static const int per[5] = {1, 2, 3, 5, 7}, nout[5] = {2, 3, 4, 6, 8};
    c->period = per[coderate]; c->nout = nout[coderate];
    /* 16-Bit-Masken für 8 aufeinanderfolgende Eingangsbits ab Phase ph (MSB = X0) */
    uint8_t distinct[16]; int nd = 0;
    for (int ph = 0; ph < c->period; ph++) {
        uint16_t m = 0;
        for (int i = 0; i < 8; i++) {
            int pos = (ph + i) % c->period;
            int kx = (punct_x[coderate] >> pos) & 1, ky = (punct_y[coderate] >> pos) & 1;
            m |= (uint16_t)(kx << (15 - 2 * i)); m |= (uint16_t)(ky << (14 - 2 * i));
        }
        c->mask16[ph] = m;
        uint8_t mb[2] = {(uint8_t)(m >> 8), (uint8_t)m};
        for (int h = 0; h < 2; h++) {
            int idx = -1;
            for (int d = 0; d < nd; d++) if (distinct[d] == mb[h]) idx = d;
            if (idx < 0) { idx = nd; distinct[nd++] = mb[h]; }
            if (h == 0) c->mask_idx_hi[ph] = (uint8_t)idx; else c->mask_idx_lo[ph] = (uint8_t)idx;
        }
    }
    for (int ph = 0; ph < c->period; ph++) c->next_phase[ph] = (uint8_t)((ph + 8) % c->period);
    for (int d = 0; d < nd; d++) {
        uint8_t mask = distinct[d]; int cnt = 0;
        for (int b = 0; b < 8; b++) cnt += (mask >> b) & 1;
        c->cmp_cnt[d] = (uint8_t)cnt;
        for (int b = 0; b < 256; b++) {
            unsigned v = 0;
            for (int k = 7; k >= 0; k--) if ((mask >> k) & 1) v = (v << 1) | ((b >> k) & 1);
            c->cmp_val[d][b] = (uint8_t)v;
        }
    }
}

size_t dvb_convcode_bytes(dvb_convcode_t *c, const uint8_t *in, size_t n, uint8_t *out)
{
    size_t no = 0;
    uint32_t acc = (uint32_t)c->acc; int accn = c->accn;
    unsigned st = c->state; int ph = c->phase;
    const uint8_t *tab = &cc_tab[0][0], *nxt = &cc_next[0][0];
    for (size_t i = 0; i < n; i++) {
        unsigned b = in[i];
        unsigned hi = tab[st * 16 + (b >> 4)];  st = nxt[st * 16 + (b >> 4)];
        unsigned lo = tab[st * 16 + (b & 15)];  st = nxt[st * 16 + (b & 15)];
        unsigned ih = c->mask_idx_hi[ph], il = c->mask_idx_lo[ph];
        unsigned ch = c->cmp_cnt[ih], cl = c->cmp_cnt[il];
        acc = (acc << ch) | c->cmp_val[ih][hi];
        acc = (acc << cl) | c->cmp_val[il][lo];
        accn += (int)(ch + cl);
        ph = c->next_phase[ph];
        if (accn >= 8) { accn -= 8; out[no++] = (uint8_t)(acc >> accn); }
        if (accn >= 8) { accn -= 8; out[no++] = (uint8_t)(acc >> accn); }
        acc &= (1u << accn) - 1;
    }
    c->acc = acc; c->accn = accn; c->state = st; c->phase = ph;
    return no;
}
