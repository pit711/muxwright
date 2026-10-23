/*
 * dvbs2.c – DVB-S2-Kodierkette (EN 302 307-1 V1.4.1), siehe dvbs2.h.
 * Bitreihenfolge: gepackte Bytes, MSB zuerst = erstes Bit im Normsinn.
 */
#include "dvbs2.h"
#include "dvbs2_tables.h"
#include "dvbmod.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <stdio.h>

/* Tabelle 5a/5b: Kbch, Nbch (= k_ldpc), t */
static const int KBCH_N[S2_NRATE] = {16008,21408,25728,32208,38688,43040,48408,51648,53840,57472,58192};
static const int NBCH_N[S2_NRATE] = {16200,21600,25920,32400,38880,43200,48600,51840,54000,57600,58320};
static const int BCHT_N[S2_NRATE] = {12,12,12,12,12,10,12,12,10,8,8};
static const int KBCH_S[S2_NRATE] = {3072,5232,6312,7032,9552,10632,11712,12432,13152,14232,0};
static const int NBCH_S[S2_NRATE] = {3240,5400,6480,7200,9720,10800,11880,12600,13320,14400,0};
/* q = (n_ldpc − k_ldpc)/360 (Tabellen 7a/7b) */
static const int QN[S2_NRATE] = {135,120,108,90,72,60,45,36,30,20,18};
static const int QS[S2_NRATE] = {36,30,27,25,18,15,12,10,8,5,0};
const char *const s2_rate_name[S2_NRATE] = {"1/4","1/3","2/5","1/2","3/5","2/3","3/4","4/5","5/6","8/9","9/10"};
const char *const s2_mod_name[4] = {"QPSK","8PSK","16APSK","32APSK"};
static const int MODBITS[4] = {2,3,4,5};

#define LW 12   /* 32-Bit-Wörter je 360-Bit-Vektor */

struct dvbs2 {
    int mod, rate, shortf, pilots, rolloff, gold, modbits, A;
    int kbch, nbch, nldpc, kldpc, M, q, t, bch_deg;
    /* LDPC */
    const uint16_t *laddr, *lrow; int lrows;
    uint16_t *lc; uint16_t *ls;      /* je Adresse: Zeile c = x mod q, Fensteranfang s = (360 − x/q) mod 360 */
    uint32_t *par;                   /* q × LW */
    /* BCH */
    uint8_t gpoly[200];
    uint32_t (*bcht)[256][6];        /* [4][256][6], MSB-bündig */
    /* BB */
    uint8_t *bbscr;                  /* kbch/8 */
    uint8_t crctab[256];
    dvbs2_pkt_fn in_fn; void *in_ctx;
    uint8_t pkt[188]; int pkt_pos; uint8_t prev_crc;
    /* PL */
    int ns, nslots, npil, plsym;
    uint32_t hdr[90];
    uint32_t *pil;                   /* npil·36 verwürfelte Pilotsymbole */
    uint16_t *rnd;                   /* je Datensymbol: Rn << modbits */
    uint32_t *tab;                   /* 4 · 2^modbits: (I | Q<<16) */
    uint8_t *lab;                    /* ns Labels */
    uint8_t *cw;                     /* nldpc/8 */
    double peak;
    char name[48];
    int prof_on; dvbs2_prof_t prof;
};

/* ---------------------------------------------------------------- Hilfen */
static uint8_t bitrev8(uint8_t b) { b = (uint8_t)((b * 0x0202020202ULL & 0x010884422010ULL) % 1023); return b; }

/* ---------------------------------------------------------------- BCH */
static void polymul(uint8_t *a, int *da, const uint16_t *bt, int nb)
{
    int db = 0; uint8_t bc[20] = {0};
    for (int i = 0; i < nb; i++) { bc[bt[i]] = 1; if (bt[i] > db) db = bt[i]; }
    uint8_t r[256] = {0};
    for (int i = 0; i <= *da; i++) if (a[i]) for (int j = 0; j <= db; j++) r[i + j] ^= bc[j];
    *da += db; memcpy(a, r, (size_t)*da + 1);
}
static void build_bch(dvbs2_t *s)
{
    /* Tabelle 6a (normal, GF(2^16)) und 6b (short, GF(2^14)): g1 … g12 als Exponentenlisten */
    static const uint16_t gN[12][14] = {
        {0,2,3,5,16},{0,1,4,5,6,8,16},{0,2,3,4,5,7,8,9,10,11,16},{0,2,4,6,9,11,12,14,16},
        {0,1,2,3,5,8,9,10,11,12,16},{0,2,4,5,7,8,9,10,12,13,14,15,16},{0,2,5,6,8,9,10,11,13,15,16},
        {0,1,2,5,6,8,9,12,13,14,16},{0,5,7,9,10,11,16},{0,1,2,5,7,8,10,12,13,14,16},
        {0,2,3,5,9,11,12,13,16},{0,1,5,6,7,9,11,12,16}};
    static const int gNlen[12] = {5,7,11,9,11,13,11,11,7,11,9,9};
    static const uint16_t gS[12][14] = {
        {0,1,3,5,14},{0,6,8,11,14},{0,1,2,6,9,10,14},{0,4,7,8,10,12,14},{0,2,4,6,8,9,11,13,14},
        {0,3,7,8,9,13,14},{0,2,5,6,7,10,11,13,14},{0,5,8,9,10,11,14},{0,1,2,3,9,10,14},
        {0,3,6,9,11,12,14},{0,4,11,12,14},{0,1,2,3,5,6,7,8,10,13,14}};
    static const int gSlen[12] = {5,5,7,7,9,7,9,7,7,7,5,11};
    uint8_t g[256] = {0}; g[0] = 1; int dg = 0;
    for (int k = 0; k < s->t; k++)
        if (!s->shortf) polymul(g, &dg, gN[k], gNlen[k]); else polymul(g, &dg, gS[k], gSlen[k]);
    s->bch_deg = dg;
    memcpy(s->gpoly, g, (size_t)dg + 1);
    /* Byte-Tabellen: T_k[v] = v(x)·x^(D+8k) mod g, als MSB-bündiges 192-Bit-Register (Wort 0 = höchste Koeffizienten) */
    s->bcht = malloc(sizeof(uint32_t[4][256][6]));
    int D = dg;
    uint8_t reg[200];
    for (int k = 0; k < 4; k++)
        for (int v = 0; v < 256; v++) {
            memset(reg, 0, sizeof reg);
            for (int i = 0; i < 8 + 8 * k; i++) {
                int bit = i < 8 ? (v >> (7 - i)) & 1 : 0;
                int fb = bit ^ reg[D - 1];
                for (int j = D - 1; j > 0; j--) reg[j] = (uint8_t)(reg[j - 1] ^ (g[j] & fb));
                reg[0] = (uint8_t)(g[0] & fb);
            }
            uint32_t *w = s->bcht[k][v]; memset(w, 0, 24);
            for (int j = 0; j < D; j++) {          /* Koeffizient x^(D-1-j) → Bit j von oben */
                int b = reg[D - 1 - j];
                if (b) w[j >> 5] |= 0x80000000u >> (j & 31);
            }
        }
}
/* cw[0 .. kbch/8) → Parität nach cw[kbch/8 ..) */
static void bch_encode(const dvbs2_t *s, uint8_t *cw)
{
    uint32_t r0 = 0, r1 = 0, r2 = 0, r3 = 0, r4 = 0, r5 = 0;
    const uint32_t (*T0)[6] = s->bcht[0], (*T1)[6] = s->bcht[1], (*T2)[6] = s->bcht[2], (*T3)[6] = s->bcht[3];
    int n = s->kbch / 8, i = 0;
    for (; i + 4 <= n; i += 4) {
        const uint32_t *a = T3[(r0 >> 24) ^ cw[i]], *b = T2[((r0 >> 16) & 0xFF) ^ cw[i + 1]],
                       *c = T1[((r0 >> 8) & 0xFF) ^ cw[i + 2]], *d = T0[(r0 & 0xFF) ^ cw[i + 3]];
        r0 = r1 ^ a[0] ^ b[0] ^ c[0] ^ d[0];
        r1 = r2 ^ a[1] ^ b[1] ^ c[1] ^ d[1];
        r2 = r3 ^ a[2] ^ b[2] ^ c[2] ^ d[2];
        r3 = r4 ^ a[3] ^ b[3] ^ c[3] ^ d[3];
        r4 = r5 ^ a[4] ^ b[4] ^ c[4] ^ d[4];
        r5 =      a[5] ^ b[5] ^ c[5] ^ d[5];
    }
    for (; i < n; i++) {
        const uint32_t *a = T0[(r0 >> 24) ^ cw[i]];
        r0 = ((r0 << 8) | (r1 >> 24)) ^ a[0]; r1 = ((r1 << 8) | (r2 >> 24)) ^ a[1];
        r2 = ((r2 << 8) | (r3 >> 24)) ^ a[2]; r3 = ((r3 << 8) | (r4 >> 24)) ^ a[3];
        r4 = ((r4 << 8) | (r5 >> 24)) ^ a[4]; r5 = (r5 << 8) ^ a[5];
    }
    uint32_t r[6] = {r0, r1, r2, r3, r4, r5};
    uint8_t *p = cw + n;
    for (int k = 0; k < s->bch_deg / 8; k++) p[k] = (uint8_t)(r[k >> 2] >> (24 - 8 * (k & 3)));
}

void dvbs2_ref_bch(const dvbs2_t *s, const uint8_t *in, uint8_t *out)
{
    int K = s->kbch, D = s->bch_deg;
    uint8_t reg[200] = {0};
    memmove(out, in, (size_t)K);
    for (int i = 0; i < K; i++) {
        int fb = in[i] ^ reg[D - 1];
        for (int j = D - 1; j > 0; j--) reg[j] = (uint8_t)(reg[j - 1] ^ (s->gpoly[j] & fb));
        reg[0] = (uint8_t)(s->gpoly[0] & fb);
    }
    for (int j = 0; j < D; j++) out[K + j] = reg[D - 1 - j];
}

/* ---------------------------------------------------------------- LDPC */
static int ldpc_table(int rate, int shortf, const uint16_t **addr, const uint16_t **row, int *rows)
{
#define SET(a) do { *addr = a##_addr; *row = a##_row; *rows = (int)(sizeof(a##_row) / sizeof(uint16_t)) - 1; } while (0)
    if (!shortf) switch (rate) {
        case R1_4: SET(ldpc_n_1_4); break; case R1_3: SET(ldpc_n_1_3); break; case R2_5: SET(ldpc_n_2_5); break;
        case R1_2: SET(ldpc_n_1_2); break; case R3_5: SET(ldpc_n_3_5); break; case R2_3: SET(ldpc_n_2_3); break;
        case R3_4: SET(ldpc_n_3_4); break; case R4_5: SET(ldpc_n_4_5); break; case R5_6: SET(ldpc_n_5_6); break;
        case R8_9: SET(ldpc_n_8_9); break; case R9_10: SET(ldpc_n_9_10); break; default: return -1;
    } else switch (rate) {
        case R1_4: SET(ldpc_s_1_4); break; case R1_3: SET(ldpc_s_1_3); break; case R2_5: SET(ldpc_s_2_5); break;
        case R1_2: SET(ldpc_s_1_2); break; case R3_5: SET(ldpc_s_3_5); break; case R2_3: SET(ldpc_s_2_3); break;
        case R3_4: SET(ldpc_s_3_4); break; case R4_5: SET(ldpc_s_4_5); break; case R5_6: SET(ldpc_s_5_6); break;
        case R8_9: SET(ldpc_s_8_9); break; default: return -1;
    }
#undef SET
    return 0;
}

void dvbs2_ref_ldpc(const dvbs2_t *s, const uint8_t *in, uint8_t *out)
{
    int kl = s->kldpc, M = s->M, q = s->q;
    memmove(out, in, (size_t)kl);
    uint8_t *p = out + kl; memset(p, 0, (size_t)M);
    for (int i = 0; i < kl; i++) {
        if (!in[i]) continue;
        int g = i / 360, off = i % 360;
        for (int c = s->lrow[g]; c < s->lrow[g + 1]; c++) p[(s->laddr[c] + off * q) % M] ^= 1;
    }
    for (int i = 1; i < M; i++) p[i] ^= p[i - 1];
}

/*
 * Quasi-zyklisch: Paritätsindex x + m·q (mod M) mit x = c + q·r ⇒ Zeile c, Spalte (r + m) mod 360 der
 * transponierten Anordnung P'[c][j] = p[c + q·j]. Jede Adresse x einer 360er-Gruppe u (Infobits) bewirkt
 * P'[c] ^= rot(u, r). Danach Akkumulator p_i ^= p_{i−1} in natürlicher Reihenfolge:
 * p[c + q·j] = R_c[j] ^ F_{j−1}, R_c = XOR_{c'≤c} P'[c'], F = inklusive Präfix-XOR über die Bits von R_{q−1}.
 */
static void ldpc_encode(dvbs2_t *s, uint8_t *cw)
{
    static const uint8_t *rev = NULL; static uint8_t revt[256];
    if (!rev) { for (int i = 0; i < 256; i++) revt[i] = bitrev8((uint8_t)i); rev = revt; }
    const int q = s->q;
    uint32_t *P = s->par;
    memset(P, 0, (size_t)q * LW * 4);
    uint32_t D[2 * LW + 2];
    for (int g = 0; g < s->lrows; g++) {
        const uint8_t *b = cw + 45 * g;
        uint32_t u[LW];
        for (int w = 0; w < 11; w++)
            u[w] = (uint32_t)rev[b[4*w]] | (uint32_t)rev[b[4*w+1]] << 8 | (uint32_t)rev[b[4*w+2]] << 16 | (uint32_t)rev[b[4*w+3]] << 24;
        u[11] = rev[b[44]];
        /* D = u | u << 360 (720 Bit) */
        for (int w = 0; w < 11; w++) D[w] = u[w];
        D[11] = u[11] | (u[0] << 8);
        for (int w = 1; w < 12; w++) D[11 + w] = (u[w - 1] >> 24) | (u[w] << 8);
        D[23] = D[24] = D[25] = 0;          /* u[11] hat nur 8 Bit */
        for (int a = s->lrow[g]; a < s->lrow[g + 1]; a++) {
            uint32_t *dst = P + (size_t)s->lc[a] * LW;
            int st = s->ls[a], wi = st >> 5, sh = st & 31;
            const uint32_t *src = D + wi;
            if (sh == 0) { for (int w = 0; w < LW; w++) dst[w] ^= src[w]; }
            else { for (int w = 0; w < LW; w++) dst[w] ^= (src[w] >> sh) | (src[w + 1] << (32 - sh)); }
        }
    }
    /* R_c (in place) */
    for (int c = 1; c < q; c++) { uint32_t *a = P + (size_t)c * LW, *pr = a - LW; for (int w = 0; w < LW; w++) a[w] ^= pr[w]; }
    /* F = Präfix-XOR der Bits von T = R_{q-1}, E_j = F_{j-1} */
    const uint32_t *T = P + (size_t)(q - 1) * LW;
    uint32_t E[LW]; uint32_t carry = 0;
    for (int w = 0; w < LW; w++) {
        uint32_t x = T[w];
        if (w == 11) x &= 0xFF;
        x ^= x << 1; x ^= x << 2; x ^= x << 4; x ^= x << 8; x ^= x << 16;
        if (carry) x = ~x;
        E[w] = (x << 1) | (carry & 1);
        carry = x >> 31;
    }
    for (int c = 0; c < q; c++) { uint32_t *a = P + (size_t)c * LW; for (int w = 0; w < LW; w++) a[w] ^= E[w]; }
    /* natürliche Reihenfolge i = c + q·j, MSB zuerst ab Bit k_ldpc */
    uint8_t *o = cw + s->kldpc / 8;
    unsigned acc = 0; int nb = 0;
    for (int j = 0; j < 360; j++) {
        int wj = j >> 5, bj = j & 31;
        const uint32_t *col = P + wj;
        for (int c = 0; c < q; c++) {
            acc = (acc << 1) | ((col[(size_t)c * LW] >> bj) & 1);
            if (++nb == 8) { *o++ = (uint8_t)acc; acc = 0; nb = 0; }
        }
    }
}

/* ---------------------------------------------------------------- BB (Mode-/Stream-Adaptation) */
static void build_bb(dvbs2_t *s)
{
    for (int v = 0; v < 256; v++) {     /* CRC-8 g = x^8+x^7+x^6+x^4+x^2+1 */
        uint8_t r = (uint8_t)v;
        for (int b = 0; b < 8; b++) r = (uint8_t)((r & 0x80) ? (r << 1) ^ 0xD5 : (r << 1));
        s->crctab[v] = r;
    }
    /* BB-Scrambler 1 + x^14 + x^15, Startwert 100101010000000 */
    s->bbscr = calloc((size_t)s->kbch / 8, 1);
    unsigned reg = 0x4A80;
    for (int i = 0; i < s->kbch; i++) {
        unsigned out = ((reg >> 1) ^ reg) & 1;
        if (out) s->bbscr[i >> 3] |= (uint8_t)(0x80 >> (i & 7));
        reg = (reg >> 1) | (out << 14);
    }
    s->pkt_pos = 188; s->prev_crc = 0;
}
static uint8_t crc8(const dvbs2_t *s, const uint8_t *d, int n, uint8_t r)
{
    for (int i = 0; i < n; i++) r = s->crctab[r ^ d[i]];
    return r;
}
static void next_packet(dvbs2_t *s)
{
    if (!s->in_fn || !s->in_fn(s->in_ctx, s->pkt)) {     /* Nullpaket */
        memcpy(s->pkt, "\x47\x1f\xff\x10", 4); memset(s->pkt + 4, 0xff, 184);
    }
    uint8_t c = crc8(s, s->pkt + 1, 187, 0);
    s->pkt[0] = s->prev_crc;                            /* Sync-Byte → CRC-8 des vorigen UP */
    s->prev_crc = c;
    s->pkt_pos = 0;
}
static void bbframe(dvbs2_t *s, uint8_t *cw)
{
    int dflb = s->kbch / 8 - 10, fill = 0, syncd = -1;
    uint8_t *df = cw + 10;
    while (fill < dflb) {
        if (s->pkt_pos == 188) { next_packet(s); if (syncd < 0) syncd = fill * 8; }
        int take = 188 - s->pkt_pos; if (take > dflb - fill) take = dflb - fill;
        memcpy(df + fill, s->pkt + s->pkt_pos, (size_t)take);
        fill += take; s->pkt_pos += take;
    }
    if (syncd < 0) syncd = 0xFFFF;
    int dfl = s->kbch - 80;
    cw[0] = (uint8_t)(0xF0 | s->rolloff);   /* TS, SIS, CCM, ISSYI=0, NPD=0, RO */
    cw[1] = 0;                               /* MATYPE-2 (SIS) */
    cw[2] = (188 * 8) >> 8; cw[3] = (188 * 8) & 0xFF;
    cw[4] = (uint8_t)(dfl >> 8); cw[5] = (uint8_t)dfl;
    cw[6] = 0x47;
    cw[7] = (uint8_t)(syncd >> 8); cw[8] = (uint8_t)syncd;
    cw[9] = crc8(s, cw, 9, 0);
    int n = s->kbch / 8;
    for (int i = 0; i < n; i++) cw[i] ^= s->bbscr[i];
}

/* ---------------------------------------------------------------- Konstellationen */
static void constellation(const dvbs2_t *s, double *re, double *im)
{
    int n = 1 << s->modbits;
    if (s->mod == S2_QPSK) {
        for (int w = 0; w < 4; w++) { re[w] = (1 - 2 * ((w >> 1) & 1)) / sqrt(2.0); im[w] = (1 - 2 * (w & 1)) / sqrt(2.0); }
        return;
    }
    if (s->mod == S2_8PSK) {   /* Bild 10 */
        static const double deg[8] = {45, 0, 180, 225, 90, 315, 135, 270};
        for (int w = 0; w < 8; w++) { re[w] = cos(deg[w] * M_PI / 180); im[w] = sin(deg[w] * M_PI / 180); }
        return;
    }
    if (s->mod == S2_16APSK) { /* Bild 11, γ = R2/R1 (Tabelle 9) */
        static const double gam[S2_NRATE] = {0,0,0,0,0,3.15,2.85,2.75,2.70,2.60,2.57};
        static const double ph[16] = {  /* in π/12 */
            3, -3, 9, -9, 1, -1, 11, -11, 5, -5, 7, -7, 3, -3, 9, -9};
        double g = gam[s->rate], r1 = sqrt(4.0 / (1.0 + 3.0 * g * g)), r2 = g * r1;
        for (int w = 0; w < 16; w++) { double r = w >= 12 ? r1 : r2; re[w] = r * cos(ph[w] * M_PI / 12); im[w] = r * sin(ph[w] * M_PI / 12); }
        return;
    }
    {   /* 32APSK, Bild 12, γ1 = R2/R1, γ2 = R3/R1 (Tabelle 10) */
        static const double g1[S2_NRATE] = {0,0,0,0,0,0,2.84,2.72,2.64,2.54,2.53};
        static const double g2[S2_NRATE] = {0,0,0,0,0,0,5.27,4.87,4.64,4.33,4.30};
        /* Ring (1,2,3) und Phase in π/24 */
        static const int ring[32] = {2,2,2,2,2,2,2,2, 3,3,3,3,3,3,3,3, 2,1,2,1,2,1,2,1, 3,3,3,3,3,3,3,3};
        static const int ph[32]   = {6,10,-6,-10,18,14,-18,-14, 3,9,-6,-12,18,12,-21,-15,
                                     2,6,-2,-6,22,18,-22,-18, 0,6,-3,-9,21,15,24,-18};
        double a = g1[s->rate], b = g2[s->rate], r1 = sqrt(8.0 / (1.0 + 3.0 * a * a + 4.0 * b * b));
        for (int w = 0; w < 32; w++) {
            double r = ring[w] == 1 ? r1 : ring[w] == 2 ? a * r1 : b * r1;
            re[w] = r * cos(ph[w] * M_PI / 24); im[w] = r * sin(ph[w] * M_PI / 24);
        }
    }
    (void)n;
}
static uint32_t iq32(double i, double q) { return (uint16_t)(int16_t)lrint(i) | ((uint32_t)(uint16_t)(int16_t)lrint(q) << 16); }

/* ---------------------------------------------------------------- PL-Framing */
static int modcod_value(const dvbs2_t *s)
{
    static const int base[4] = {1, 12 - R3_5, 18 - R2_3, 24 - R3_4};
    /* 8PSK hat kein 4/5: MODCOD 12…17 = 3/5, 2/3, 3/4, 5/6, 8/9, 9/10 */
    return base[s->mod] + s->rate - (s->mod == S2_8PSK && s->rate > R4_5);
}
static void build_pl(dvbs2_t *s)
{
    /* PLHEADER: SOF (26 Bit) + PLS (64,7) */
    static const uint32_t G[6] = {0x55555555u, 0x33333333u, 0x0F0F0F0Fu, 0x00FF00FFu, 0x0000FFFFu, 0xFFFFFFFFu};
    static const uint64_t PLSCR = 0x719D83C953422DFAULL;
    unsigned code7 = (unsigned)(modcod_value(s) << 2) | (unsigned)(s->shortf ? 2 : 0) | (unsigned)(s->pilots ? 1 : 0);
    unsigned in6 = code7 >> 1, b7 = code7 & 1;
    uint32_t y = 0;
    for (int r = 0; r < 6; r++) if ((in6 >> (5 - r)) & 1) y ^= G[r];
    uint8_t bits[90];
    for (int i = 0; i < 26; i++) bits[i] = (uint8_t)((0x18D2E82u >> (25 - i)) & 1);
    for (int i = 0; i < 32; i++) { int yi = (y >> (31 - i)) & 1; bits[26 + 2*i] = (uint8_t)yi; bits[27 + 2*i] = (uint8_t)(yi ^ b7); }
    for (int i = 0; i < 64; i++) bits[26 + i] ^= (uint8_t)((PLSCR >> (63 - i)) & 1);
    double a = s->A / sqrt(2.0);
    for (int i = 0; i < 90; i += 2) {      /* π/2-BPSK */
        double u = a * (1 - 2 * bits[i]), v = a * (1 - 2 * bits[i + 1]);
        s->hdr[i] = iq32(u, u); s->hdr[i + 1] = iq32(-v, v);
    }
    /* Konstellation × Rn-Drehung */
    int n = 1 << s->modbits;
    double re[32], im[32]; constellation(s, re, im);
    s->peak = 0; for (int w = 0; w < n; w++) { double m = hypot(re[w], im[w]); if (m > s->peak) s->peak = m; }
    s->tab = malloc((size_t)4 * n * 4);
    for (int rn = 0; rn < 4; rn++)
        for (int w = 0; w < n; w++) {
            double i = re[w] * s->A, q = im[w] * s->A, oi, oq;
            switch (rn) { case 0: oi = i; oq = q; break; case 1: oi = -q; oq = i; break; case 2: oi = -i; oq = -q; break; default: oi = q; oq = -i; }
            s->tab[rn * n + w] = iq32(oi, oq);
        }
    /* PL-Scrambling: Gold-Folge, zn(i) = x((i+n) mod (2^18−1)) ⊕ y(i), Rn(i) = 2·zn(i+131072) + zn(i) */
    const int P = (1 << 18) - 1;
    uint8_t *x = malloc((size_t)P), *yv = malloc((size_t)P);
    unsigned xs = 1, ys = 0x3FFFF;   /* Bit k = x(i+k) */
    for (int i = 0; i < P; i++) {
        x[i] = (uint8_t)(xs & 1); yv[i] = (uint8_t)(ys & 1);
        unsigned xb = (xs ^ (xs >> 7)) & 1, yb = (ys ^ (ys >> 5) ^ (ys >> 7) ^ (ys >> 10)) & 1;
        xs = (xs >> 1) | (xb << 17); ys = (ys >> 1) | (yb << 17);
    }
    int L = s->plsym - 90;
    uint8_t *rn = malloc((size_t)L);
    for (int i = 0; i < L; i++) {
        int j = (i + 131072) % P;
        int z0 = x[(i + s->gold) % P] ^ yv[i], z1 = x[(j + s->gold) % P] ^ yv[j];
        rn[i] = (uint8_t)(2 * z1 + z0);
    }
    free(x); free(yv);
    /* Positionen: je 16 Slots ein Pilotblock (36 Symbole), nicht nach dem letzten Slot */
    s->rnd = malloc((size_t)s->ns * 2);
    s->pil = malloc((size_t)(s->npil ? s->npil : 1) * 36 * 4);
    int p = 0, k = 0, pb = 0;
    for (int slot = 0; slot < s->nslots; slot++) {
        for (int m = 0; m < 90; m++) s->rnd[k++] = (uint16_t)(rn[p++] << s->modbits);
        if (s->pilots && (slot + 1) % 16 == 0 && slot + 1 < s->nslots) {
            for (int m = 0; m < 36; m++) {
                double pi = a, pq = a, oi, oq;
                switch (rn[p++]) { case 0: oi = pi; oq = pq; break; case 1: oi = -pq; oq = pi; break; case 2: oi = -pi; oq = -pq; break; default: oi = pq; oq = -pi; }
                s->pil[pb * 36 + m] = iq32(oi, oq);
            }
            pb++;
        }
    }
    free(rn);
}

/* ---------------------------------------------------------------- Interleaver + Mapping */
static void labels(const dvbs2_t *s, const uint8_t *cw, uint8_t *lab)
{
    int nb = s->nldpc / 8;
    if (s->mod == S2_QPSK) {
        for (int i = 0; i < nb; i++) { uint8_t b = cw[i]; lab[0] = b >> 6; lab[1] = (b >> 4) & 3; lab[2] = (b >> 2) & 3; lab[3] = b & 3; lab += 4; }
        return;
    }
    /* Bit-Interleaver: spaltenweise schreiben (Spalte = rows Bits), zeilenweise lesen; 8PSK 3/5: Spalten 2,1,0 */
    int cols = s->modbits, rows = s->nldpc / cols;
    int order[5]; for (int c = 0; c < cols; c++) order[c] = c;
    if (s->mod == S2_8PSK && s->rate == R3_5) { order[0] = 2; order[1] = 1; order[2] = 0; }
    if ((rows & 7) == 0) {
        static uint64_t spread[256]; static int init = 0;
        if (!init) { for (int b = 0; b < 256; b++) { uint64_t v = 0; for (int k = 0; k < 8; k++) if ((b >> (7 - k)) & 1) v |= 1ULL << (8 * k); spread[b] = v; } init = 1; }
        const uint8_t *col[5]; for (int c = 0; c < cols; c++) col[c] = cw + order[c] * (rows / 8);
        for (int t = 0; t < rows / 8; t++) {
            uint64_t v = 0;
            for (int c = 0; c < cols; c++) v = (v << 1) | spread[col[c][t]];
            for (int k = 0; k < 8; k++) lab[8 * t + k] = (uint8_t)(v >> (8 * k));
        }
        return;
    }
    for (int r = 0; r < rows; r++) {     /* allgemeiner Fall (16APSK short: rows = 4050) */
        unsigned w = 0;
        for (int c = 0; c < cols; c++) { int i = order[c] * rows + r; w = (w << 1) | ((cw[i >> 3] >> (7 - (i & 7))) & 1); }
        lab[r] = (uint8_t)w;
    }
}
void dvbs2_map_frame(dvbs2_t *s, const uint8_t *cw, int16_t *out_iq)
{
    labels(s, cw, s->lab);
    uint32_t *o = (uint32_t *)out_iq;
    memcpy(o, s->hdr, sizeof s->hdr); o += 90;
    const uint8_t *lab = s->lab; const uint16_t *rnd = s->rnd; const uint32_t *tab = s->tab;
    int pb = 0;
    for (int slot = 0; slot < s->nslots; slot++) {
        for (int m = 0; m < 90; m++) o[m] = tab[rnd[m] | lab[m]];
        o += 90; lab += 90; rnd += 90;
        if (s->pilots && (slot + 1) % 16 == 0 && slot + 1 < s->nslots) { memcpy(o, s->pil + 36 * pb, 36 * 4); o += 36; pb++; }
    }
}

/* ---------------------------------------------------------------- öffentliche Funktionen */
dvbs2_t *dvbs2_create(int mod, int rate, int shortf, int pilots, int rolloff, int gold, int A)
{
    if (mod < 0 || mod > 3 || rate < 0 || rate >= S2_NRATE || rolloff < 0 || rolloff > 2 || gold < 0 || gold > 262141) return NULL;
    if (shortf && rate == R9_10) return NULL;
    if ((mod == S2_8PSK && (rate < R3_5 || rate == R4_5)) || (mod == S2_16APSK && rate < R2_3) || (mod == S2_32APSK && rate < R3_4)) return NULL;
    dvbs2_t *s = calloc(1, sizeof *s);
    s->mod = mod; s->rate = rate; s->shortf = !!shortf; s->pilots = !!pilots; s->rolloff = rolloff; s->gold = gold;
    s->modbits = MODBITS[mod]; s->A = A > 0 ? A : 8000;
    s->kbch = shortf ? KBCH_S[rate] : KBCH_N[rate];
    s->nbch = shortf ? NBCH_S[rate] : NBCH_N[rate];
    s->t = shortf ? 12 : BCHT_N[rate];
    s->nldpc = shortf ? 16200 : 64800;
    s->kldpc = s->nbch; s->M = s->nldpc - s->kldpc;
    s->q = shortf ? QS[rate] : QN[rate];
    if (ldpc_table(rate, s->shortf, &s->laddr, &s->lrow, &s->lrows) < 0 || s->lrows * 360 != s->kldpc || s->M != 360 * s->q) {
        fprintf(stderr, "dvbs2: LDPC-Tabelle passt nicht\n"); free(s); return NULL;
    }
    int na = s->lrow[s->lrows];
    s->lc = malloc((size_t)na * 2); s->ls = malloc((size_t)na * 2);
    for (int a = 0; a < na; a++) {
        int x = s->laddr[a];
        if (x >= s->M) { fprintf(stderr, "dvbs2: LDPC-Adresse %d ≥ M\n", x); free(s); return NULL; }
        s->lc[a] = (uint16_t)(x % s->q); s->ls[a] = (uint16_t)((360 - x / s->q) % 360);
    }
    s->par = malloc((size_t)s->q * LW * 4);
    build_bch(s);
    build_bb(s);
    s->ns = s->nldpc / s->modbits;
    s->nslots = s->ns / 90;
    s->npil = s->pilots ? (s->nslots - 1) / 16 : 0;
    s->plsym = 90 + s->ns + 36 * s->npil;
    build_pl(s);
    s->lab = malloc((size_t)s->ns + 8);
    s->cw = malloc((size_t)s->nldpc / 8);
    snprintf(s->name, sizeof s->name, "%s %s%s%s", s2_mod_name[mod], s2_rate_name[rate], shortf ? " short" : "", pilots ? " pilots" : "");
    return s;
}

void dvbs2_free(dvbs2_t *s)
{
    if (!s) return;
    free(s->lc); free(s->ls); free(s->par); free(s->bcht); free(s->bbscr); free(s->pil); free(s->rnd);
    free(s->tab); free(s->lab); free(s->cw); free(s);
}

void dvbs2_set_input(dvbs2_t *s, dvbs2_pkt_fn fn, void *ctx) { s->in_fn = fn; s->in_ctx = ctx; }
int dvbs2_kbch(const dvbs2_t *s) { return s->kbch; }
int dvbs2_nbch(const dvbs2_t *s) { return s->nbch; }
int dvbs2_nldpc(const dvbs2_t *s) { return s->nldpc; }
int dvbs2_dfl_bits(const dvbs2_t *s) { return s->kbch - 80; }
int dvbs2_plframe_symbols(const dvbs2_t *s) { return s->plsym; }
double dvbs2_ts_bitrate(const dvbs2_t *s, double sr) { return sr * (s->kbch - 80) / s->plsym; }
const char *dvbs2_name(const dvbs2_t *s) { return s->name; }
int dvbs2_modcod(const dvbs2_t *s) { return modcod_value(s); }
double dvbs2_peak(const dvbs2_t *s) { return s->peak; }

void dvbs2_fecframe(dvbs2_t *s, uint8_t *cw)
{
    double t0 = s->prof_on ? dvb_now() : 0, t1, t2, t3;
    bbframe(s, cw);
    if (s->prof_on) { t1 = dvb_now(); s->prof.bb += t1 - t0; t0 = t1; }
    bch_encode(s, cw);
    if (s->prof_on) { t2 = dvb_now(); s->prof.bch += t2 - t0; t0 = t2; }
    ldpc_encode(s, cw);
    if (s->prof_on) { t3 = dvb_now(); s->prof.ldpc += t3 - t0; }
}

void dvbs2_frame(dvbs2_t *s, int16_t *out_iq)
{
    dvbs2_fecframe(s, s->cw);
    double t0 = s->prof_on ? dvb_now() : 0;
    dvbs2_map_frame(s, s->cw, out_iq);
    if (s->prof_on) { s->prof.map += dvb_now() - t0; s->prof.frames++; }
}

void dvbs2_profile(dvbs2_t *s, int on, dvbs2_prof_t *out)
{
    s->prof_on = on;
    if (out) *out = s->prof;
}
