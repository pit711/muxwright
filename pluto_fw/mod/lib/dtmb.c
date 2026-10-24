/*
 * dtmb.c – DTMB-Sender (GB 20600-2006): FEC, Mapping, Verschachtelung, Rahmenbildung, IDFT, SRRC.
 * Copyright (c) 2026 DAB-Lab. Lizenz: MIT (siehe LICENSE). Herkunft der Normkonstanten: siehe dtmb.h.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "dtmb.h"
#include "dtmb_ldpc_tables.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>

#if defined(__ARM_NEON) || defined(__ARM_NEON__)
#include <arm_neon.h>
#define DTMB_NEON 1
#endif

static double now_s(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return ts.tv_sec + ts.tv_nsec * 1e-9; }

/* ================================================================ Parameter */
const char *const dtmb_rate_name[3] = { "0.4", "0.6", "0.8" };
static const char *const qam_names[5] = { "4QAM-NR", "4QAM", "16QAM", "32QAM", "64QAM" };
const char *dtmb_qam_name(int q) { return (q >= 0 && q < 5) ? qam_names[q] : "?"; }

static int coded_bits(int q) { static const int b[5] = { 1, 2, 4, 5, 6 }; return b[q]; }
static int map_bits(int q)   { static const int b[5] = { 2, 2, 4, 5, 6 }; return b[q]; }
static int nbch_per_cw(int rate) { return 4 + 2 * rate; }          /* 4/6/8 BCH-Blöcke je Codewort */
static int pk_per_cw(int rate) { return nbch_per_cw(rate) / 2; }   /* 2/3/4 TS-Pakete je Codewort */

int dtmb_cfg_check(const dtmb_cfg_t *c, char *err, size_t n)
{
    if (c->carriers != 1 && c->carriers != 3780) { snprintf(err, n, "--carriers 3780 oder 1"); return -1; }
    if (c->qam < 0 || c->qam > 4) { snprintf(err, n, "--const 4qamnr|4qam|16qam|32qam|64qam"); return -1; }
    if (c->rate < 0 || c->rate > 2) { snprintf(err, n, "--rate 0.4|0.6|0.8"); return -1; }
    if ((c->qam == DTMB_4QAMNR || c->qam == DTMB_32QAM) && c->rate != 2) {
        snprintf(err, n, "%s ist nur mit Coderate 0,8 erlaubt (GB 20600-2006)", qam_names[c->qam]); return -1;
    }
    if (c->pn != 420 && c->pn != 595 && c->pn != 945) { snprintf(err, n, "--pn 420|595|945"); return -1; }
    if (c->ti != 240 && c->ti != 720) { snprintf(err, n, "--ti 240|720"); return -1; }
    return 0;
}
int dtmb_si_index(const dtmb_cfg_t *c)
{
    int base = c->qam == DTMB_4QAMNR ? 3 : c->qam == DTMB_4QAM ? 5 + 2 * c->rate : c->qam == DTMB_16QAM ? 11 + 2 * c->rate
             : c->qam == DTMB_32QAM ? 17 : 19 + 2 * c->rate;
    return base + (c->ti == 720);
}
int dtmb_frame_len(int pn) { return pn + DTMB_BODY; }
int dtmb_frames_per_superframe(int pn) { return pn == 420 ? 225 : pn == 595 ? 216 : 200; }
int dtmb_frames_per_group(const dtmb_cfg_t *c) { return (c->qam == DTMB_4QAMNR || c->qam == DTMB_32QAM) ? 2 : 1; }
int dtmb_cw_per_group(const dtmb_cfg_t *c) { return coded_bits(c->qam) * dtmb_frames_per_group(c) / 2; }
int dtmb_packets_per_frame2(const dtmb_cfg_t *c) { return dtmb_cw_per_group(c) * pk_per_cw(c->rate) * 2 / dtmb_frames_per_group(c); }
double dtmb_ts_bps(const dtmb_cfg_t *c)
{
    /* Pakete je 2 Rahmen · 1504 bit / (2 · Rahmendauer) */
    return dtmb_packets_per_frame2(c) * 1504.0 * DTMB_SYMBOL_RATE / (2.0 * dtmb_frame_len(c->pn));
}

/* ================================================================ Bitstrom-Hilfen (LSB zuerst) */
typedef struct { uint32_t *w; uint64_t acc; int n; size_t idx; } bw_t;
static inline void bw_put(bw_t *b, uint32_t v, int nb)
{
    b->acc |= (uint64_t)v << b->n; b->n += nb;
    if (b->n >= 32) { b->w[b->idx++] = (uint32_t)b->acc; b->acc >>= 32; b->n -= 32; }
}
static inline void bw_flush(bw_t *b) { if (b->n > 0) { b->w[b->idx++] = (uint32_t)b->acc; b->acc = 0; b->n = 0; } }
static inline uint32_t get32(const uint32_t *s, size_t pos)
{
    size_t i = pos >> 5; unsigned sh = pos & 31;
    uint64_t v = s[i] | ((uint64_t)s[i + 1] << 32);
    return (uint32_t)(v >> sh);
}
static uint8_t rev8_tab[256];
static void rev8_init(void)
{
    for (int i = 0; i < 256; i++) { int r = 0; for (int b = 0; b < 8; b++) if (i >> b & 1) r |= 1 << (7 - b); rev8_tab[i] = (uint8_t)r; }
}

/* ================================================================ LDPC: QC-Code, Codierung über GF(2^7)
 * Ring R = GF(2)[x]/(x^127+1) ≅ GF(2) × GF(128)^18 (x^127+1 = (x+1)·18 irreduzible Polynome vom Grad 7).
 * Paritätsblock p_j = Σ_i u_i·g_ij in R → im Bildbereich punktweise: P_j(β) = Σ_i U_i(β)·G_ij(β) für β = α^r
 * (r = Vertreter der 18 Kreisteilungsklassen, je 7 Elemente) und β = 1 (Paritätsbit). Hin-/Rücktransformation
 * über Nibble-Tabellen, Produkte über Log/Exp (NEON: vtbl). Gleiches Ergebnis wie die direkte Rotation (Selbsttest). */
struct dtmb_ldpc {
    int rate, kb, cb, jvn;
    const uint32_t *g;                 /* [kb][cb][4] */
    int rep[18];
    uint8_t  gexp[128], glog[128];     /* α = x mod (x^7+x+1) */
    uint8_t  fw[32][16][24];           /* Nibble q, Wert v → 18 GF-Werte + Parität (Byte 18) */
    uint32_t inv_lo[18][16][4], inv_hi[18][8][4], ones[4];
    uint8_t *lg;                       /* [18][jvn][kb][8] log G (0xFF = 0), C-Pfad */
    uint8_t *gv;                       /* [18][jvn][kb][8] G-Werte (0 = Auffüllung), NEON-Pfad (vmull.p8) */
    uint64_t gpar[40];                 /* je j: Bitmaske über i von G_ij(1) */
    /* Arbeitsbereich */
    uint8_t lu[18][48];
    uint8_t pt[18][40];
};

static uint8_t gf_mul(const dtmb_ldpc_t *l, uint8_t a, uint8_t b)
{
    if (!a || !b) return 0;
    return l->gexp[(l->glog[a] + l->glog[b]) % 127];
}

/* φ(x^k): Bytes 0..17 = α^(rep·k), Byte 18 = 1 */
static void phi_mono(const dtmb_ldpc_t *l, int k, uint8_t out[19])
{
    for (int r = 0; r < 18; r++) out[r] = l->gexp[(l->rep[r] * k) % 127];
    out[18] = 1;
}

static void fwd(const dtmb_ldpc_t *l, const uint32_t b[4], uint8_t out[24])
{
#ifdef DTMB_NEON
    uint8x8_t a0 = vdup_n_u8(0), a1 = a0, a2 = a0;
    for (int w = 0; w < 4; w++) {
        uint32_t v = b[w];
        for (int q = 0; q < 8; q++, v >>= 4) {
            const uint8_t *e = l->fw[w * 8 + q][v & 15];
            a0 = veor_u8(a0, vld1_u8(e)); a1 = veor_u8(a1, vld1_u8(e + 8)); a2 = veor_u8(a2, vld1_u8(e + 16));
        }
    }
    vst1_u8(out, a0); vst1_u8(out + 8, a1); vst1_u8(out + 16, a2);
#else
    uint64_t a0 = 0, a1 = 0, a2 = 0;
    for (int w = 0; w < 4; w++) {
        uint32_t v = b[w];
        for (int q = 0; q < 8; q++, v >>= 4) {
            const uint8_t *e = l->fw[w * 8 + q][v & 15];
            uint64_t x0, x1, x2; memcpy(&x0, e, 8); memcpy(&x1, e + 8, 8); memcpy(&x2, e + 16, 8);
            a0 ^= x0; a1 ^= x1; a2 ^= x2;
        }
    }
    memcpy(out, &a0, 8); memcpy(out + 8, &a1, 8); memcpy(out + 16, &a2, 8);
#endif
}

dtmb_ldpc_t *dtmb_ldpc_create(int rate)
{
    dtmb_ldpc_t *l = (dtmb_ldpc_t *)calloc(1, sizeof *l);
    if (!l) return NULL;
    l->rate = rate;
    l->kb = rate == 0 ? 24 : rate == 1 ? 36 : 48;
    l->cb = 59 - l->kb;
    l->jvn = (l->cb + 7) / 8;
    l->g = rate == 0 ? &DTMB_G_1[0][0][0] : rate == 1 ? &DTMB_G_2[0][0][0] : &DTMB_G_3[0][0][0];
    /* GF(128) */
    unsigned x = 1;
    for (int k = 0; k < 127; k++) { l->gexp[k] = (uint8_t)x; l->glog[x] = (uint8_t)k; x <<= 1; if (x & 0x80) x ^= 0x83; }
    l->gexp[127] = 0;
    /* Kreisteilungsklassen mod 127 unter ×2 */
    int vis[127] = { 0 }, nr = 0;
    for (int r = 1; r < 127; r++) {
        if (vis[r]) continue;
        l->rep[nr++] = r;
        int y = r; do { vis[y] = 1; y = (2 * y) % 127; } while (y != r);
    }
    /* Vorwärtstabelle */
    for (int q = 0; q < 32; q++)
        for (int v = 0; v < 16; v++) {
            uint8_t acc[24] = { 0 };
            for (int t = 0; t < 4; t++) {
                int k = 4 * q + t;
                if (!(v >> t & 1) || k >= 127) continue;
                uint8_t m[19]; phi_mono(l, k, m);
                for (int i = 0; i < 19; i++) acc[i] ^= m[i];
            }
            memcpy(l->fw[q][v], acc, 24);
        }
    /* Rücktransformation: A (Zeile = φ-Bit b, Spalte = Koeffizient k) invertieren */
    uint32_t A[127][4], Iv[127][4];
    memset(A, 0, sizeof A); memset(Iv, 0, sizeof Iv);
    for (int k = 0; k < 127; k++) {
        uint8_t m[19]; phi_mono(l, k, m);
        for (int r = 0; r < 18; r++) for (int t = 0; t < 7; t++) if (m[r] >> t & 1) A[7 * r + t][k >> 5] |= 1u << (k & 31);
        A[126][k >> 5] |= 1u << (k & 31);
    }
    for (int b = 0; b < 127; b++) Iv[b][b >> 5] |= 1u << (b & 31);
    for (int col = 0; col < 127; col++) {
        int piv = -1;
        for (int r = col; r < 127; r++) if (A[r][col >> 5] >> (col & 31) & 1) { piv = r; break; }
        if (piv < 0) { free(l); return NULL; }
        if (piv != col) for (int w = 0; w < 4; w++) { uint32_t t = A[piv][w]; A[piv][w] = A[col][w]; A[col][w] = t; t = Iv[piv][w]; Iv[piv][w] = Iv[col][w]; Iv[col][w] = t; }
        for (int r = 0; r < 127; r++)
            if (r != col && (A[r][col >> 5] >> (col & 31) & 1))
                for (int w = 0; w < 4; w++) { A[r][w] ^= A[col][w]; Iv[r][w] ^= Iv[col][w]; }
    }
    /* A^-1·e_b: Koeffizient k = Iv[k] Bit b */
    uint32_t col_b[127][4];
    memset(col_b, 0, sizeof col_b);
    for (int k = 0; k < 127; k++)
        for (int b = 0; b < 127; b++)
            if (Iv[k][b >> 5] >> (b & 31) & 1) col_b[b][k >> 5] |= 1u << (k & 31);
    for (int r = 0; r < 18; r++) {
        for (int v = 0; v < 16; v++) for (int w = 0; w < 4; w++) {
            uint32_t a = 0; for (int t = 0; t < 4; t++) if (v >> t & 1) a ^= col_b[7 * r + t][w];
            l->inv_lo[r][v][w] = a;
        }
        for (int v = 0; v < 8; v++) for (int w = 0; w < 4; w++) {
            uint32_t a = 0; for (int t = 0; t < 3; t++) if (v >> t & 1) a ^= col_b[7 * r + 4 + t][w];
            l->inv_hi[r][v][w] = a;
        }
    }
    memcpy(l->ones, col_b[126], sizeof l->ones);
    /* G im Bildbereich */
    l->lg = (uint8_t *)malloc((size_t)18 * l->jvn * l->kb * 8);
    memset(l->lg, 0xFF, (size_t)18 * l->jvn * l->kb * 8);
    l->gv = (uint8_t *)calloc((size_t)18 * l->jvn * l->kb * 8, 1);
    for (int i = 0; i < l->kb; i++)
        for (int j = 0; j < l->cb; j++) {
            const uint32_t *gc = l->g + ((size_t)i * l->cb + j) * 4;
            uint8_t t[24]; fwd(l, gc, t);
            for (int r = 0; r < 18; r++)
                l->lg[(((size_t)r * l->jvn + j / 8) * l->kb + i) * 8 + (j & 7)] = t[r] ? l->glog[t[r]] : 0xFF;
            for (int r = 0; r < 18; r++)
                l->gv[(((size_t)r * l->jvn + j / 8) * l->kb + i) * 8 + (j & 7)] = t[r];
            if (t[18] & 1) l->gpar[j] |= 1ull << i;
        }
    return l;
}
void dtmb_ldpc_free(dtmb_ldpc_t *l) { if (l) { free(l->lg); free(l->gv); free(l); } }
int dtmb_ldpc_kb(const dtmb_ldpc_t *l) { return l->kb; }
int dtmb_ldpc_cb(const dtmb_ldpc_t *l) { return l->cb; }

static void cw_assemble(const dtmb_ldpc_t *l, uint32_t par[][4], const uint32_t *info, uint32_t *cw)
{
    bw_t b = { cw, 0, 0, 0 };
    bw_put(&b, par[0][0] >> 5, 27); bw_put(&b, par[0][1], 32); bw_put(&b, par[0][2], 32); bw_put(&b, par[0][3] & 0x7fffffff, 31);
    for (int j = 1; j < l->cb; j++) { bw_put(&b, par[j][0], 32); bw_put(&b, par[j][1], 32); bw_put(&b, par[j][2], 32); bw_put(&b, par[j][3] & 0x7fffffff, 31); }
    int K = l->kb * 127, full = K >> 5;
    for (int w = 0; w < full; w++) bw_put(&b, info[w], 32);
    if (K & 31) bw_put(&b, info[full] & ((1u << (K & 31)) - 1), K & 31);
    bw_flush(&b);
}

static inline void info_block(const uint32_t *info, int i, uint32_t u[4])
{
    size_t p = (size_t)i * 127;
    u[0] = get32(info, p); u[1] = get32(info, p + 32); u[2] = get32(info, p + 64); u[3] = get32(info, p + 96) & 0x7fffffff;
}

void dtmb_ldpc_encode_ref(const dtmb_ldpc_t *l, const uint32_t *info, uint32_t *cw)
{
    uint32_t par[40][4]; memset(par, 0, sizeof par);
    uint8_t gb[127];
    for (int i = 0; i < l->kb; i++) {
        uint32_t u[4]; info_block(info, i, u);
        for (int j = 0; j < l->cb; j++) {
            const uint32_t *gc = l->g + ((size_t)i * l->cb + j) * 4;
            for (int r = 0; r < 127; r++) gb[r] = gc[r >> 5] >> (r & 31) & 1;
            for (int s = 0; s < 127; s++) {
                if (!(u[s >> 5] >> (s & 31) & 1)) continue;
                for (int r = 0; r < 127; r++) if (gb[r]) { int o = (r + s) % 127; par[j][o >> 5] ^= 1u << (o & 31); }
            }
        }
    }
    cw_assemble(l, par, info, cw);
}

#ifdef DTMB_NEON
/* Punktweise Produkte über GF(128) mit vmull.p8: je 8 Paritätsblöcke (Lanes) und Klasse r werden die ungekürzten
   Polynomprodukte U_i·G_ij (7×7 → 13 Bit) per XOR über i aufsummiert und erst am Ende einmal reduziert
   (x^7 = x + 1: Bits 7..12 = h → h·(x+1), Grad ≤ 6). lu enthält hier die Werte selbst, nicht Logarithmen. */
#define LU_CONV(l, v) (v)
static void pointwise(dtmb_ldpc_t *l)
{
    const int kb = l->kb;
    const uint16x8_t m7f = vdupq_n_u16(0x7F);
    for (int r = 0; r < 18; r++) {
        const poly8_t *lu = (const poly8_t *)l->lu[r];
        for (int jv = 0; jv < l->jvn; jv++) {
            const poly8_t *g = (const poly8_t *)(l->gv + ((size_t)r * l->jvn + jv) * kb * 8);
            uint16x8_t a0 = vdupq_n_u16(0), a1 = a0;
            for (int i = 0; i < kb; i += 2) {
                a0 = veorq_u16(a0, vreinterpretq_u16_p16(vmull_p8(vld1_dup_p8(lu + i), vld1_p8(g + 8 * i))));
                a1 = veorq_u16(a1, vreinterpretq_u16_p16(vmull_p8(vld1_dup_p8(lu + i + 1), vld1_p8(g + 8 * i + 8))));
            }
            uint16x8_t a = veorq_u16(a0, a1), hh = vshrq_n_u16(a, 7);
            a = veorq_u16(vandq_u16(a, m7f), veorq_u16(hh, vshlq_n_u16(hh, 1)));
            vst1_u8(&l->pt[r][jv * 8], vmovn_u16(a));
        }
    }
}
const char *dtmb_ldpc_backend(void) { return "neon-vmull-gf128"; }
#else
#define LU_CONV(l, v) ((v) ? (l)->glog[(v)] : 0xFF)
static void pointwise(dtmb_ldpc_t *l)
{
    const int kb = l->kb;
    for (int r = 0; r < 18; r++) {
        const uint8_t *lu = l->lu[r];
        for (int jv = 0; jv < l->jvn; jv++) {
            const uint8_t *g = l->lg + ((size_t)r * l->jvn + jv) * kb * 8;
            uint8_t acc[8] = { 0 };
            for (int i = 0; i < kb; i++) {
                unsigned u = lu[i];
                if (u == 0xFF) continue;
                for (int k = 0; k < 8; k++) {
                    unsigned s = g[8 * i + k];
                    if (s == 0xFF) continue;
                    s += u; if (s >= 127) s -= 127;
                    acc[k] ^= l->gexp[s];
                }
            }
            memcpy(&l->pt[r][jv * 8], acc, 8);
        }
    }
}
const char *dtmb_ldpc_backend(void) { return "c-gf128"; }
#endif

void dtmb_ldpc_encode(dtmb_ldpc_t *l, const uint32_t *info, uint32_t *cw)
{
    uint64_t upar = 0;
    for (int i = 0; i < l->kb; i++) {
        uint32_t u[4]; uint8_t t[24];
        info_block(info, i, u);
        fwd(l, u, t);
        for (int r = 0; r < 18; r++) l->lu[r][i] = (uint8_t)LU_CONV(l, t[r]);
        if (t[18] & 1) upar |= 1ull << i;
    }
    pointwise(l);
    uint32_t par[40][4];
    for (int j = 0; j < l->cb; j++) {
        uint32_t a0, a1, a2, a3;
        if (__builtin_popcountll(upar & l->gpar[j]) & 1) { a0 = l->ones[0]; a1 = l->ones[1]; a2 = l->ones[2]; a3 = l->ones[3]; }
        else a0 = a1 = a2 = a3 = 0;
        for (int r = 0; r < 18; r++) {
            unsigned v = l->pt[r][j];
            const uint32_t *lo = l->inv_lo[r][v & 15], *hi = l->inv_hi[r][v >> 4];
            a0 ^= lo[0] ^ hi[0]; a1 ^= lo[1] ^ hi[1]; a2 ^= lo[2] ^ hi[2]; a3 ^= lo[3] ^ hi[3];
        }
        par[j][0] = a0; par[j][1] = a1; par[j][2] = a2; par[j][3] = a3;
    }
    cw_assemble(l, par, info, cw);
}

/* ================================================================ NR-Code (Nordstrom-Robinson (16,8), Gl. 4-1…4-8) */
static uint16_t nr_tab[256];
static void nr_init(void)
{
    for (unsigned in = 0; in < 256; in++) {
        unsigned x[8];
        for (int i = 0; i < 8; i++) x[i] = in >> (7 - i) & 1;
        unsigned par = 0, last = __builtin_popcount(in) & 1;
        for (int i = 0; i < 7; i++) {
#define X(j) x[(i + (j)) % 7]
            unsigned y = x[7] ^ X(6) ^ X(0) ^ X(1) ^ X(3) ^ ((X(0) ^ X(4)) & (X(1) ^ X(2) ^ X(3) ^ X(5))) ^ ((X(1) ^ X(2)) & (X(3) ^ X(5)));
#undef X
            par |= y << (7 - i); last ^= y;
        }
        nr_tab[in] = (uint16_t)((in << 8) | par | last);
    }
}

/* ================================================================ Konstellationen (Einheiten: doppelte Normwerte) */
/* GB 20600-2006 Bild 4: 32QAM-Kreuz, Index b0 + 2 b1 + … + 16 b4 (Normwerte ±1,5/±4,5/±7,5 → ×2) */
static const int8_t qam32_pts[32][2] = {
    {-3,-3},{-15,-9},{3,-3},{15,-9},{-9,-3},{-15,-3},{9,-3},{15,-3},
    {-3,3},{-15,9},{3,3},{15,9},{-9,3},{-15,3},{9,3},{15,3},
    {-3,-9},{-3,-15},{3,-9},{3,-15},{-9,-9},{-9,-15},{9,-9},{9,-15},
    {-3,9},{-3,15},{3,9},{3,15},{-9,9},{-9,15},{9,9},{9,15},
};
/* Label (b0 zuerst = Bit 0) → Punkt; quadratisch: I = Bits 0..m/2−1, Q = Rest, je Achse reflektierter Gray-Code */
static void qam_point(int q, int label, int *re, int *im)
{
    if (q == DTMB_32QAM) { *re = qam32_pts[label][0]; *im = qam32_pts[label][1]; return; }
    static const int lv4[2] = { -9, 9 }, lv16[4] = { -12, -4, 4, 12 }, lv64[8] = { -14, -10, -6, -2, 2, 6, 10, 14 };
    int ab = map_bits(q) / 2, mask = (1 << ab) - 1;
    int li = label & mask, lq = (label >> ab) & mask;
    /* Gray-Inverse */
    int ii = li, iq = lq;
    for (int s = 1; s < ab; s <<= 1) { ii ^= ii >> s; iq ^= iq >> s; }
    const int *lv = ab == 1 ? lv4 : ab == 2 ? lv16 : lv64;
    *re = lv[ii]; *im = lv[iq];
}
static double qam_power(int q)   /* mittlere Leistung in doppelten Normeinheiten */
{
    double p = 0; int n = 1 << map_bits(q);
    for (int l = 0; l < n; l++) { int a, b; qam_point(q, l, &a, &b); p += a * a + b * b; }
    return p / n;
}

/* ================================================================ Systeminformation (Anhang G) */
static const char *const si_odd[32] = {
    "00011110101011100100100010110011", "01111000110010000010111011010101", "01110111110001110010000111011010",
    "00100010100100100111010010001111", "01001011111110110001110111100110", "00010001101000010100011110111100",
    "01111000001101110010111000101010", "00101101100111010111101110000000", "01110111001110000010000100100101",
    "00100010011011010111010001110000", "01000100000010110001001000010110", "00010001010111100100011101000011",
    "00101101011000100111101101111111", "01000100111101000001001011101001", "01001011000001000001110100011001",
    "00011110010100010100100001001100", "01111000110010001101000100101010", "00101101100111011000010001111111",
    "01001011111110111110001000011001", "00011110101011101011011101001100", "01110111110001111101111000100101",
    "00100010100100101000101101110000", "01000100111101001110110100010110", "00010001101000011011100001000011",
    "01111000001101111101000111010101", "00101101011000101000010010000000", "01001011000001001110001011100110",
    "00011110010100011011011110110011", "01110111001110001101111011011010", "00100010011011011000101110001111",
    "01000100000010111110110111101001", "00010001010111101011100010111100",
};
/* 36 Bits: 4 Bits Trägerkennung (C = 3780: 1111, C = 1: 0000) + 32-Bit-Vektor (gerader Index = Komplement) */
static void si_bits(const dtmb_cfg_t *c, uint8_t b[36])
{
    int idx = dtmb_si_index(c);
    const char *v = si_odd[(idx - 1) / 2];
    for (int i = 0; i < 4; i++) b[i] = c->carriers == 3780;
    for (int i = 0; i < 32; i++) b[4 + i] = (uint8_t)((v[i] - '0') ^ !(idx & 1));
}

/* ================================================================ PN-Rahmenkopf (4.6.2) */
typedef struct { int hdr, core, fps; const char *seed; int taps[4], ntaps; } pn_def_t;
static const pn_def_t pn_defs[3] = {
    { 420, 255, 225, "10110000", { 0, 2, 3, 7 }, 4 },
    { 595, 595, 216, "0000000001", { 0, 7 }, 2 },
    { 945, 511, 200, "111110111", { 0, 1, 2, 7 }, 4 },
};
static const pn_def_t *pn_def(int pn) { return pn == 420 ? &pn_defs[0] : pn == 595 ? &pn_defs[1] : &pn_defs[2]; }
/* Folge b[0..len−1] (0/1) aus Startwert und Rekursion b[n] = Σ b[n−L+tap] */
static void pn_bits(const pn_def_t *d, int len, uint8_t *b)
{
    int L = (int)strlen(d->seed);
    for (int n = 0; n < L; n++) b[n] = (uint8_t)(d->seed[n] - '0');
    for (int n = L; n < len; n++) { uint8_t v = 0; for (int t = 0; t < d->ntaps; t++) v ^= b[n - L + d->taps[t]]; b[n] = v; }
}
static int pn_phase(const pn_def_t *d, int rotate, int frame)
{
    if (d->core == d->hdr || !rotate) return 0;
    int idx = frame % d->fps, turn = d->fps / 2;
    if (idx > turn) idx = 2 * turn - idx;
    return idx == 0 ? 0 : (idx & 1) ? (idx + 1) / 2 : d->core - idx / 2;
}

/* ================================================================ 3780-Punkt-IDFT int16 (Stockham, gemischte Basis)
 * Festkomma Q15, Re/Im getrennt, Stufen 4·3·3·3·5·7, ohne Skalierung (die Eingangspegel sind so gewählt, dass der
 * Effektivwert durch √3780 auf den Zielpegel wächst), sättigende Addition, Konstanten/Drehfaktoren mit vqrdmulh.
 * Stufe 1 (Schrittweite 1) wird über pp vektorisiert (Ausgabe mit vst4), alle weiteren über q (Schrittweite ≥ 4).
 * Die letzte Stufe (m = 1, keine Drehfaktoren) schreibt direkt I/Q verschränkt nach int16, optional nachskaliert.
 * NEON (D-Register, 4 Lanes) auf ARM, gleichwertige C-Nachbildung sonst – bitgleich. Arbeitssatz 4 × 7,6 KB (L1). */
#define FFT_N 3780
#define FFT_PAD 16
#ifdef DTMB_NEON
typedef int16x4_t s4;
#define Q_LD(p)          vld1_s16(p)
#define Q_ST(p, v)       vst1_s16(p, v)
#define Q_DUPM(p)        vld1_dup_s16(p)
#define Q_ADD(a, b)      vqadd_s16(a, b)
#define Q_SUB(a, b)      vqsub_s16(a, b)
#define Q_MUL(a, b)      vqrdmulh_s16(a, b)
#define Q_MULN(a, c)     vqrdmulh_n_s16(a, c)
static inline void Q_ST4(int16_t *p, s4 a, s4 b, s4 c, s4 d) { int16x4x4_t x = { { a, b, c, d } }; vst4_s16(p, x); }
static inline void Q_ST2(int16_t *p, s4 a, s4 b) { int16x4x2_t x = { { a, b } }; vst2_s16(p, x); }
static inline void Q_LD2(const int16_t *p, s4 *a, s4 *b) { int16x4x2_t x = vld2_s16(p); *a = x.val[0]; *b = x.val[1]; }
#else
typedef struct { int16_t v[4]; } s4;
static inline int16_t sat16i(int32_t x) { return (int16_t)(x > 32767 ? 32767 : x < -32768 ? -32768 : x); }
static inline int16_t qrdmulh1(int16_t a, int16_t b) { return sat16i((int32_t)(((int64_t)2 * a * b + 32768) >> 16)); }
static inline s4 Q_LD(const int16_t *p) { s4 r; memcpy(r.v, p, 8); return r; }
static inline void Q_ST(int16_t *p, s4 a) { memcpy(p, a.v, 8); }
static inline s4 Q_DUPM(const int16_t *p) { s4 r; for (int i = 0; i < 4; i++) r.v[i] = *p; return r; }
static inline s4 Q_ADD(s4 a, s4 b) { s4 r; for (int i = 0; i < 4; i++) r.v[i] = sat16i(a.v[i] + b.v[i]); return r; }
static inline s4 Q_SUB(s4 a, s4 b) { s4 r; for (int i = 0; i < 4; i++) r.v[i] = sat16i(a.v[i] - b.v[i]); return r; }
static inline s4 Q_MUL(s4 a, s4 b) { s4 r; for (int i = 0; i < 4; i++) r.v[i] = qrdmulh1(a.v[i], b.v[i]); return r; }
static inline s4 Q_MULN(s4 a, int16_t c) { s4 r; for (int i = 0; i < 4; i++) r.v[i] = qrdmulh1(a.v[i], c); return r; }
static inline void Q_ST4(int16_t *p, s4 a, s4 b, s4 c, s4 d)
{ for (int i = 0; i < 4; i++) { p[4 * i] = a.v[i]; p[4 * i + 1] = b.v[i]; p[4 * i + 2] = c.v[i]; p[4 * i + 3] = d.v[i]; } }
static inline void Q_ST2(int16_t *p, s4 a, s4 b) { for (int i = 0; i < 4; i++) { p[2 * i] = a.v[i]; p[2 * i + 1] = b.v[i]; } }
static inline void Q_LD2(const int16_t *p, s4 *a, s4 *b) { for (int i = 0; i < 4; i++) { a->v[i] = p[2 * i]; b->v[i] = p[2 * i + 1]; } }
#endif

struct dtmb_fft {
    int nst, radix[8], stride[8], m[8];
    int16_t *twr[8], *twi[8];     /* Stufe 1: [t−1][pp] (Vektoren über pp), sonst [pp·(p−1)+t−1]; Q15 */
};
static int16_t q15(double c) { long v = lround(c * 32768.0); return (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v); }
dtmb_fft_t *dtmb_fft_create(void)
{
    dtmb_fft_t *f = (dtmb_fft_t *)calloc(1, sizeof *f);
    static const int rad[] = { 4, 3, 3, 3, 5, 7 };
    f->nst = 6;
    int s = 1;
    for (int k = 0; k < f->nst; k++) {
        int p = rad[k], ns = FFT_N / s, m = ns / p;
        f->radix[k] = p; f->stride[k] = s; f->m[k] = m;
        size_t n = (size_t)(m + FFT_PAD) * (p - 1);
        f->twr[k] = (int16_t *)calloc(n, sizeof(int16_t)); f->twi[k] = (int16_t *)calloc(n, sizeof(int16_t));
        for (int pp = 0; pp < m; pp++)
            for (int t = 1; t < p; t++) {
                double a = 2 * M_PI * (double)pp * t / ns;
                size_t i = k == 0 ? (size_t)(t - 1) * (m + FFT_PAD) + pp : (size_t)pp * (p - 1) + t - 1;
                f->twr[k][i] = q15(cos(a)); f->twi[k][i] = q15(sin(a));
            }
        s *= p;
    }
    return f;
}
void dtmb_fft_free(dtmb_fft_t *f) { if (f) { for (int k = 0; k < f->nst; k++) { free(f->twr[k]); free(f->twi[k]); } free(f); } }
#ifdef DTMB_NEON
const char *dtmb_fft_backend(void) { return "neon-int16-stockham"; }
#else
const char *dtmb_fft_backend(void) { return "c-int16-stockham"; }
#endif
size_t dtmb_fft_buflen(void) { return FFT_N + FFT_PAD; }

#define CMUL(yr, yi, ar, ai, wr, wi) do { yr = Q_SUB(Q_MUL(ar, wr), Q_MUL(ai, wi)); yi = Q_ADD(Q_MUL(ar, wi), Q_MUL(ai, wr)); } while (0)
/* inverse Butterflies: A_t = Σ_r a_r·e^{+j2π·rt/p} */
static inline void bf4(s4 *ar, s4 *ai)
{
    s4 t0r = Q_ADD(ar[0], ar[2]), t0i = Q_ADD(ai[0], ai[2]), t1r = Q_SUB(ar[0], ar[2]), t1i = Q_SUB(ai[0], ai[2]);
    s4 t2r = Q_ADD(ar[1], ar[3]), t2i = Q_ADD(ai[1], ai[3]), t3r = Q_SUB(ar[1], ar[3]), t3i = Q_SUB(ai[1], ai[3]);
    ar[0] = Q_ADD(t0r, t2r); ai[0] = Q_ADD(t0i, t2i);
    ar[2] = Q_SUB(t0r, t2r); ai[2] = Q_SUB(t0i, t2i);
    ar[1] = Q_SUB(t1r, t3i); ai[1] = Q_ADD(t1i, t3r);   /* t1 + j·t3 */
    ar[3] = Q_ADD(t1r, t3i); ai[3] = Q_SUB(t1i, t3r);   /* t1 − j·t3 */
}
#define K3C (-16384)   /* cos 120° */
#define K3S 28378      /* sin 120° */
static inline void bf3(s4 *ar, s4 *ai)
{
    s4 sr = Q_ADD(ar[1], ar[2]), si = Q_ADD(ai[1], ai[2]), dr = Q_SUB(ar[1], ar[2]), di = Q_SUB(ai[1], ai[2]);
    s4 cr = Q_ADD(ar[0], Q_MULN(sr, K3C)), ci = Q_ADD(ai[0], Q_MULN(si, K3C));
    s4 tr = Q_MULN(dr, K3S), ti = Q_MULN(di, K3S);
    ar[0] = Q_ADD(ar[0], sr); ai[0] = Q_ADD(ai[0], si);
    ar[1] = Q_SUB(cr, ti); ai[1] = Q_ADD(ci, tr);
    ar[2] = Q_ADD(cr, ti); ai[2] = Q_SUB(ci, tr);
}
#define K51 10126
#define K52 (-26510)
#define K5S1 31164
#define K5S2 19261
static inline void bf5(s4 *ar, s4 *ai)
{
    s4 s1r = Q_ADD(ar[1], ar[4]), s1i = Q_ADD(ai[1], ai[4]), d1r = Q_SUB(ar[1], ar[4]), d1i = Q_SUB(ai[1], ai[4]);
    s4 s2r = Q_ADD(ar[2], ar[3]), s2i = Q_ADD(ai[2], ai[3]), d2r = Q_SUB(ar[2], ar[3]), d2i = Q_SUB(ai[2], ai[3]);
    s4 c1r = Q_ADD(ar[0], Q_ADD(Q_MULN(s1r, K51), Q_MULN(s2r, K52))), c1i = Q_ADD(ai[0], Q_ADD(Q_MULN(s1i, K51), Q_MULN(s2i, K52)));
    s4 c2r = Q_ADD(ar[0], Q_ADD(Q_MULN(s1r, K52), Q_MULN(s2r, K51))), c2i = Q_ADD(ai[0], Q_ADD(Q_MULN(s1i, K52), Q_MULN(s2i, K51)));
    s4 t1r = Q_ADD(Q_MULN(d1r, K5S1), Q_MULN(d2r, K5S2)), t1i = Q_ADD(Q_MULN(d1i, K5S1), Q_MULN(d2i, K5S2));
    s4 t2r = Q_SUB(Q_MULN(d1r, K5S2), Q_MULN(d2r, K5S1)), t2i = Q_SUB(Q_MULN(d1i, K5S2), Q_MULN(d2i, K5S1));
    ar[0] = Q_ADD(ar[0], Q_ADD(s1r, s2r)); ai[0] = Q_ADD(ai[0], Q_ADD(s1i, s2i));
    ar[1] = Q_SUB(c1r, t1i); ai[1] = Q_ADD(c1i, t1r);
    ar[4] = Q_ADD(c1r, t1i); ai[4] = Q_SUB(c1i, t1r);
    ar[2] = Q_SUB(c2r, t2i); ai[2] = Q_ADD(c2i, t2r);
    ar[3] = Q_ADD(c2r, t2i); ai[3] = Q_SUB(c2i, t2r);
}
#define K71 20431
#define K72 (-7292)
#define K73 (-29523)
#define K7S1 25619
#define K7S2 31946
#define K7S3 14217
static inline void bf7(s4 *ar, s4 *ai)
{
    s4 s1r = Q_ADD(ar[1], ar[6]), s1i = Q_ADD(ai[1], ai[6]), d1r = Q_SUB(ar[1], ar[6]), d1i = Q_SUB(ai[1], ai[6]);
    s4 s2r = Q_ADD(ar[2], ar[5]), s2i = Q_ADD(ai[2], ai[5]), d2r = Q_SUB(ar[2], ar[5]), d2i = Q_SUB(ai[2], ai[5]);
    s4 s3r = Q_ADD(ar[3], ar[4]), s3i = Q_ADD(ai[3], ai[4]), d3r = Q_SUB(ar[3], ar[4]), d3i = Q_SUB(ai[3], ai[4]);
#define LIN3(x1, x2, x3, k1, k2, k3) Q_ADD(Q_ADD(Q_MULN(x1, k1), Q_MULN(x2, k2)), Q_MULN(x3, k3))
    s4 c1r = Q_ADD(ar[0], LIN3(s1r, s2r, s3r, K71, K72, K73)), c1i = Q_ADD(ai[0], LIN3(s1i, s2i, s3i, K71, K72, K73));
    s4 c2r = Q_ADD(ar[0], LIN3(s1r, s2r, s3r, K72, K73, K71)), c2i = Q_ADD(ai[0], LIN3(s1i, s2i, s3i, K72, K73, K71));
    s4 c3r = Q_ADD(ar[0], LIN3(s1r, s2r, s3r, K73, K71, K72)), c3i = Q_ADD(ai[0], LIN3(s1i, s2i, s3i, K73, K71, K72));
    s4 t1r = LIN3(d1r, d2r, d3r, K7S1, K7S2, K7S3), t1i = LIN3(d1i, d2i, d3i, K7S1, K7S2, K7S3);
    s4 t2r = LIN3(d1r, d2r, d3r, K7S2, -K7S3, -K7S1), t2i = LIN3(d1i, d2i, d3i, K7S2, -K7S3, -K7S1);
    s4 t3r = LIN3(d1r, d2r, d3r, K7S3, -K7S1, K7S2), t3i = LIN3(d1i, d2i, d3i, K7S3, -K7S1, K7S2);
#undef LIN3
    ar[0] = Q_ADD(ar[0], Q_ADD(Q_ADD(s1r, s2r), s3r)); ai[0] = Q_ADD(ai[0], Q_ADD(Q_ADD(s1i, s2i), s3i));
    ar[1] = Q_SUB(c1r, t1i); ai[1] = Q_ADD(c1i, t1r); ar[6] = Q_ADD(c1r, t1i); ai[6] = Q_SUB(c1i, t1r);
    ar[2] = Q_SUB(c2r, t2i); ai[2] = Q_ADD(c2i, t2r); ar[5] = Q_ADD(c2r, t2i); ai[5] = Q_SUB(c2i, t2r);
    ar[3] = Q_SUB(c3r, t3i); ai[3] = Q_ADD(c3i, t3r); ar[4] = Q_ADD(c3r, t3i); ai[4] = Q_SUB(c3i, t3r);
}

/* Stufe mit Schrittweite s ≥ 4 (Vektor über q); pp = 0 ohne Drehfaktoren (alle 1) */
#define STAGE_Q(P, BF)                                                                                                    \
static void stage_q##P(const dtmb_fft_t *f, int k, const int16_t *xr, const int16_t *xi, int16_t *yr, int16_t *yi)        \
{                                                                                                                         \
    const int s = f->stride[k], m = f->m[k];                                                                              \
    const size_t xs = (size_t)s * m;                                                                                      \
    s4 ar[P], ai[P];                                                                                                      \
    for (int pp = 0; pp < m; pp++) {                                                                                      \
        const int16_t *wr = f->twr[k] + (size_t)pp * (P - 1), *wi = f->twi[k] + (size_t)pp * (P - 1);                    \
        const int16_t *xr0 = xr + (size_t)s * pp, *xi0 = xi + (size_t)s * pp;                                            \
        int16_t *yr0 = yr + (size_t)s * P * pp, *yi0 = yi + (size_t)s * P * pp;                                          \
        for (int q = 0; q < s; q += 4) {                                                                                  \
            for (int r = 0; r < P; r++) { ar[r] = Q_LD(xr0 + q + r * xs); ai[r] = Q_LD(xi0 + q + r * xs); }               \
            BF(ar, ai);                                                                                                   \
            Q_ST(yr0 + q, ar[0]); Q_ST(yi0 + q, ai[0]);                                                                   \
            if (pp) {                                                                                                     \
                for (int t = 1; t < P; t++) {                                                                             \
                    s4 cr = Q_DUPM(wr + t - 1), ci = Q_DUPM(wi + t - 1), br, bi;                                         \
                    CMUL(br, bi, ar[t], ai[t], cr, ci);                                                                   \
                    Q_ST(yr0 + q + (size_t)s * t, br); Q_ST(yi0 + q + (size_t)s * t, bi);                                 \
                }                                                                                                         \
            } else {                                                                                                      \
                for (int t = 1; t < P; t++) { Q_ST(yr0 + q + (size_t)s * t, ar[t]); Q_ST(yi0 + q + (size_t)s * t, ai[t]); } \
            }                                                                                                             \
        }                                                                                                                 \
    }                                                                                                                     \
}
STAGE_Q(3, bf3)
STAGE_Q(5, bf5)

/* letzte Stufe: p = 7, m = 1, s = 540 → Ausgabe y[q + 540·t] verschränkt I/Q, mit Nachskalierung (Q15, 32767 = keine) */
static void stage_last7(const dtmb_fft_t *f, const int16_t *xr, const int16_t *xi, int16_t *out, int16_t scale)
{
    const int s = f->stride[5];
    s4 ar[7], ai[7];
    for (int q = 0; q < s; q += 4) {
        for (int r = 0; r < 7; r++) { ar[r] = Q_LD(xr + q + (size_t)r * s); ai[r] = Q_LD(xi + q + (size_t)r * s); }
        bf7(ar, ai);
        if (scale < 32767)
            for (int t = 0; t < 7; t++) { ar[t] = Q_MULN(ar[t], scale); ai[t] = Q_MULN(ai[t], scale); }
        for (int t = 0; t < 7; t++) Q_ST2(out + 2 * ((size_t)q + (size_t)s * t), ar[t], ai[t]);
    }
}

/* Stufe 1: p = 4, s = 1, Vektor über pp (Ausgabe y[4pp + t] verschränkt). m = 945: der letzte Vektor läuft 3 Werte
   über das Ende hinaus (Puffer gepolstert, die Überläufer landen hinter N und werden nie gelesen). */
static void stage1(const dtmb_fft_t *f, const int16_t *x, int16_t *yr, int16_t *yi)
{
    const int m = f->m[0], mp = m + FFT_PAD;
    s4 ar[4], ai[4];
    for (int pp = 0; pp < m; pp += 4) {
        for (int r = 0; r < 4; r++) Q_LD2(x + 2 * (pp + (size_t)r * m), &ar[r], &ai[r]);
        bf4(ar, ai);
        for (int t = 1; t < 4; t++) {
            s4 cr = Q_LD(f->twr[0] + (size_t)(t - 1) * mp + pp), ci = Q_LD(f->twi[0] + (size_t)(t - 1) * mp + pp), br, bi;
            CMUL(br, bi, ar[t], ai[t], cr, ci); ar[t] = br; ai[t] = bi;
        }
        Q_ST4(yr + 4 * (size_t)pp, ar[0], ar[1], ar[2], ar[3]);
        Q_ST4(yi + 4 * (size_t)pp, ai[0], ai[1], ai[2], ai[3]);
    }
}
/* inverse DFT ohne 1/N: Eingang x I/Q verschränkt (2·dtmb_fft_buflen() int16), Ergebnis I/Q verschränkt nach out
   (3780 komplexe int16), nachskaliert mit scale (Q15). Arbeitspuffer re/im/wr/wi je dtmb_fft_buflen() int16. */
void dtmb_ifft(const dtmb_fft_t *f, const int16_t *x, int16_t *re, int16_t *im, int16_t *wr, int16_t *wi, int16_t *out, int16_t scale)
{
    stage1(f, x, wr, wi);            /* 4:  x → w */
    stage_q3(f, 1, wr, wi, re, im);  /* 3:  w → re */
    stage_q3(f, 2, re, im, wr, wi);  /* 3:  re → w */
    stage_q3(f, 3, wr, wi, re, im);  /* 3:  w → re */
    stage_q5(f, 4, re, im, wr, wi);  /* 5:  re → w */
    stage_last7(f, wr, wi, out, scale);
}

/* ================================================================ SRRC */
int dtmb_srrc_init(dtmb_srrc_t *s, int ntaps, double alpha, double beta)
{
    memset(s, 0, sizeof *s);
    if (ntaps < 16 || ntaps > 256 || (ntaps & 15)) return -1;
    s->ntaps = ntaps;
    /* ungerade Länge ntaps−1 (Tap 0 = 0), Mitte bei ntaps/2 Samples = ganzzahlige Symbolverzögerung: die Symbolzeitpunkte
       liegen nach Dezimation auf 1 Sample/Symbol genau auf Abtastwerten (mit gerader Länge 31,75 Symbole Laufzeit
       wählte der Prüfempfänger beim Nachsynchronisieren gelegentlich das Nachbarsample) */
    double h[256], mx = 0;
    h[0] = 0;
    for (int i = 1; i < ntaps; i++) {
        double t = (i - ntaps / 2) / 2.0, v;
        if (fabs(t) < 1e-9) v = 1 - alpha + 4 * alpha / M_PI;
        else if (fabs(fabs(t) - 1 / (4 * alpha)) < 1e-9)
            v = alpha / sqrt(2) * ((1 + 2 / M_PI) * sin(M_PI / (4 * alpha)) + (1 - 2 / M_PI) * cos(M_PI / (4 * alpha)));
        else v = (sin(M_PI * t * (1 - alpha)) + 4 * alpha * t * cos(M_PI * t * (1 + alpha))) / (M_PI * t * (1 - pow(4 * alpha * t, 2)));
        if (beta > 0) {   /* Kaiser */
            double x = 2.0 * (i - 1) / (ntaps - 2) - 1, a = beta * sqrt(fmax(0, 1 - x * x)), i0a = 1, i0b = 1, ta = 1, tb = 1;
            for (int k = 1; k < 30; k++) { ta *= (a / 2) / k; i0a += ta * ta; tb *= (beta / 2) / k; i0b += tb * tb; }
            v *= i0a / i0b;
        }
        h[i] = v; if (fabs(v) > mx) mx = fabs(v);
    }
    double e = 0;
    s->phase_gain[0] = s->phase_gain[1] = 0;
    for (int i = 0; i < ntaps; i++) {
        long q = lround(h[i] / mx * 32767.0);
        s->h[i] = (int16_t)q; s->phase_gain[i & 1] += q / 32768.0; e += (double)q * q;
        s->hp[i & 1][i / 2] = (int16_t)q;
    }
    s->power_gain = e / (2.0 * 32768.0 * 32768.0);
    s->hist = (int16_t *)calloc((size_t)ntaps, 2 * sizeof(int16_t));
    return 0;
}
void dtmb_srrc_free(dtmb_srrc_t *s) { free(s->hist); s->hist = NULL; }
static inline int16_t sat16(long v) { return (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v); }
void dtmb_srrc_run(dtmb_srrc_t *s, const int16_t *in, size_t n, int16_t *out)
{
    const int L = s->ntaps / 2;     /* Taps je Phase */
    /* Historie: die letzten L−1 Eingangssymbole, dann laufender Block */
    int16_t *buf = (int16_t *)malloc(2 * sizeof(int16_t) * (n + (size_t)L));
    memcpy(buf, s->hist, 2 * sizeof(int16_t) * (size_t)(L - 1));
    memcpy(buf + 2 * (L - 1), in, 4 * n);
    for (size_t k = 0; k < n; k++) {
        const int16_t *x = buf + 2 * (k + (size_t)L - 1);   /* x[0] = aktuelles Symbol, x[-1] = vorheriges */
        for (int ph = 0; ph < 2; ph++) {
            int64_t ai = 0, aq = 0;
            for (int j = 0; j < L; j++) { ai += (int32_t)s->hp[ph][j] * x[-2 * j]; aq += (int32_t)s->hp[ph][j] * x[-2 * j + 1]; }
            out[4 * k + 2 * ph] = sat16((long)((ai + 16384) >> 15));
            out[4 * k + 2 * ph + 1] = sat16((long)((aq + 16384) >> 15));
        }
    }
    memcpy(s->hist, buf + 2 * n, 2 * sizeof(int16_t) * (size_t)(L - 1));
    free(buf);
}
int dtmb_srrc_fir_text(const dtmb_srrc_t *s, char *buf, size_t len)
{
    size_t o = 0;
    o += (size_t)snprintf(buf + o, len - o, "# DTMB SRRC alpha=0.05, %d taps, INT 2, 7560000 Bd (FIR-Takt 15120000 Hz), erzeugt von dtmb_mod\n"
                          "# DC je Phase %.4f/%.4f\nTX 3 GAIN 0 INT 2\nRX 3 GAIN -6 DEC 2\n", s->ntaps, s->phase_gain[0], s->phase_gain[1]);
    for (int i = 0; i < s->ntaps && o < len; i++) o += (size_t)snprintf(buf + o, len - o, "%d,%d\n", s->h[i], s->h[i]);
    return o < len ? 0 : -1;
}

/* ================================================================ Sender */
#define DTMB_GATHER_SPLIT 1890
struct dtmb_tx {
    dtmb_cfg_t c;
    int fpg, cwpg, pk_frame, pk_group, nbch, m, frame_len, hdr;
    const pn_def_t *pd;
    dtmb_ldpc_t *ldpc;
    uint32_t scrw[12 * 47];            /* XOR-Folge eines Signalrahmens (Speicher-Bytefolge, 47 Wörter je Paket) */
    uint16_t bch_tab[256];
    int      grp_frame;
    uint32_t pkw[20 * 47 + 4];         /* Pakete der Gruppe (wortweise ausgerichtet) */
    uint32_t gbits[5 * DTMB_CW_WORDS + 4];
    uint32_t info[200];
    uint8_t  lab[2 * DTMB_DATA];       /* Labels (bzw. bei 4QAM-NR Bits) der Gruppe vor der Zeitverschachtelung */
    uint8_t  nrbits[DTMB_DATA], nrlab[DTMB_DATA];
    /* Faltungsverschachteler (Labels bzw. bei 4QAM-NR Bits) */
    uint8_t *ti;
    int      ti_off[52], ti_len[52], ti_pos[52];
    /* Abbildung: Label 0..63 Daten, 64..99 Systeminformation; Werte im Frequenz- (C = 3780) bzw. Zeitbereich (C = 1) */
    int16_t  map[100][2];
    uint32_t map32[100];               /* dieselben Werte als re | im << 16 (ein Speicherzugriff) */
    int16_t  post_scale;               /* Q15-Nachskalierung nach der IDFT */
    uint16_t d2l[DTMB_DATA];           /* Datensymbol n → logische Position im Rahmenkörper */
    uint16_t si_l[DTMB_SI];            /* SI-Symbol i → logische Position */
    uint16_t p2l[DTMB_BODY];           /* C = 3780: physikalischer Träger → logische Position (Anhang F, invers) */
    uint16_t seg_l[40], seg_d[40], seg_n[40]; int nseg;   /* zusammenhängende Datenabschnitte im Rahmenkörper */
    uint8_t  tiout[DTMB_DATA];
    uint32_t *hdrw;                    /* Rahmenkopf-Samples (I = Q) für Phase 0, Länge core + hdr: Kopf = hdrw[ph ..] */
    int16_t  hdr_amp;
    double   rms, peak_ratio;
    dtmb_fft_t *fft;
    int      frame_no;
    int      prof_on; double prof[4];
};

static inline uint32_t brev_bytes(uint32_t x)   /* jedes Byte bitumgekehrt, Bytereihenfolge bleibt */
{
#if defined(__arm__) && defined(__ARM_ARCH) && __ARM_ARCH >= 7
    uint32_t r; __asm__("rbit %0, %1" : "=r"(r) : "r"(x)); return __builtin_bswap32(r);
#else
    return (uint32_t)rev8_tab[x & 255] | (uint32_t)rev8_tab[x >> 8 & 255] << 8 | (uint32_t)rev8_tab[x >> 16 & 255] << 16 | (uint32_t)rev8_tab[x >> 24] << 24;
#endif
}
static inline void bw_put32(bw_t *b, uint32_t w)
{
    if (b->n) { b->w[b->idx++] = (uint32_t)b->acc | (w << b->n); b->acc = w >> (32 - b->n); }
    else b->w[b->idx++] = w;
}

dtmb_tx_t *dtmb_tx_create(const dtmb_cfg_t *c, double rms)
{
    char e[128];
    if (dtmb_cfg_check(c, e, sizeof e)) return NULL;
    static int once;
    if (!once) { rev8_init(); nr_init(); once = 1; }
    dtmb_tx_t *t = (dtmb_tx_t *)calloc(1, sizeof *t);
    t->c = *c;
    t->fpg = dtmb_frames_per_group(c); t->cwpg = dtmb_cw_per_group(c);
    t->nbch = nbch_per_cw(c->rate);
    t->pk_group = t->cwpg * pk_per_cw(c->rate); t->pk_frame = t->pk_group / t->fpg;
    t->m = map_bits(c->qam);
    t->hdr = c->pn; t->frame_len = dtmb_frame_len(c->pn); t->pd = pn_def(c->pn);
    t->ldpc = dtmb_ldpc_create(c->rate);
    /* Verwürfler: 1+x^14+x^15, Startzustand 100101010000000 (Bit N = Register N), Reset je Signalrahmen-Nutzlast */
    uint8_t scr[12 * 188];
    unsigned st = 0x00A9;
    for (int i = 0; i < t->pk_frame * 188; i++) {
        unsigned b = 0;
        for (int k = 0; k < 8; k++) { unsigned o = ((st >> 13) ^ (st >> 14)) & 1; st = ((st << 1) & 0x7FFF) | o; b = (b << 1) | o; }
        scr[i] = (uint8_t)b;
    }
    memcpy(t->scrw, scr, (size_t)t->pk_frame * 188);
    /* BCH(762,752): g(x) = x^10 + x^3 + 1, MSB zuerst, systematisch (Nachricht, dann 10 Prüfbits) */
    for (int v = 0; v < 256; v++) {
        unsigned r = (unsigned)v << 2;
        for (int k = 0; k < 8; k++) { unsigned fb = r >> 9 & 1; r = (r << 1) & 0x3FF; if (fb) r ^= 0x009; }
        t->bch_tab[v] = (uint16_t)r;
    }
    /* Zeitverschachtelung: Zweig b verzögert um b·M Zweigbesuche; Vorbelegung pseudozufällig */
    size_t tot = 0;
    for (int b = 0; b < 52; b++) { t->ti_off[b] = (int)tot; t->ti_len[b] = b * c->ti; tot += (size_t)t->ti_len[b]; }
    t->ti = (uint8_t *)malloc(tot + 1);
    uint32_t lcg = 0x12345678u;
    int vmask = (c->qam == DTMB_4QAMNR) ? 1 : (1 << t->m) - 1;
    for (size_t i = 0; i < tot; i++) { lcg = lcg * 1664525u + 1013904223u; t->ti[i] = (uint8_t)((lcg >> 24) & vmask); }
    /* Pegel */
    double pdata = qam_power(c->qam), psi = 162.0;
    double pbody = (DTMB_SI * psi + DTMB_DATA * pdata) / DTMB_BODY;
    t->rms = rms;
    double hdr_ratio = (c->pn == 595) ? 1.0 : 2.0;   /* Kopfleistung / Körperleistung */
    t->hdr_amp = (int16_t)lround(rms * sqrt(hdr_ratio / 2.0));
    t->peak_ratio = sqrt(hdr_ratio);
    /* C = 3780: Effektivwert wächst in der unskalierten IDFT um √3780; intern mindestens 6000 (Rundungsrauschen der
       int16-IDFT < −50 dB, Reserve bis Sättigung 14,7 dB), danach mit post_scale auf den Zielpegel */
    double g, rms_int = rms < 6000 ? 6000 : rms;
    t->post_scale = 32767;
    if (c->carriers == 3780) { g = rms_int / sqrt(pbody * DTMB_BODY); if (rms < rms_int) t->post_scale = (int16_t)lround(rms / rms_int * 32768.0); }
    else g = rms / sqrt(pbody);
    uint8_t sib[36]; si_bits(c, sib);
    for (int l = 0; l < (1 << t->m); l++) {
        int a, b; qam_point(c->qam, l, &a, &b);
        t->map[l][0] = (int16_t)lround(a * g); t->map[l][1] = (int16_t)lround(b * g);
    }
    for (int i = 0; i < 36; i++) t->map[64 + i][0] = t->map[64 + i][1] = (int16_t)lround((sib[i] ? 9.0 : -9.0) * g);
    for (int l = 0; l < 100; l++) t->map32[l] = (uint32_t)(uint16_t)t->map[l][0] | (uint32_t)(uint16_t)t->map[l][1] << 16;
    /* logische Positionen: C = 3780 SI an 36 festen Stellen, Daten der Reihe nach dazwischen; C = 1 SI vorn */
    static const uint16_t sipos[36] = { 0, 140, 279, 419, 420, 560, 699, 839, 840, 980, 1119, 1259, 1260, 1400, 1539, 1679,
        1680, 1820, 1959, 2099, 2100, 2240, 2379, 2519, 2520, 2660, 2799, 2939, 2940, 3080, 3219, 3359, 3360, 3500, 3639, 3779 };
    if (c->carriers == 3780) {
        uint8_t is_si[DTMB_BODY] = { 0 };
        for (int i = 0; i < 36; i++) { is_si[sipos[i]] = 1; t->si_l[i] = sipos[i]; }
        for (int lg = 0, d = 0; lg < DTMB_BODY; lg++) if (!is_si[lg]) t->d2l[d++] = (uint16_t)lg;
        /* Frequenzverschachtelung (Anhang F): logisch → physikalisch, hier invers gespeichert */
        for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) for (int k = 0; k < 3; k++) for (int l = 0; l < 2; l++)
        for (int mm = 0; mm < 2; mm++) for (int n = 0; n < 5; n++) for (int o = 0; o < 7; o++)
            t->p2l[o * 540 + n * 108 + mm * 54 + l * 27 + k * 9 + j * 3 + i] = (uint16_t)(i * 1260 + j * 420 + k * 140 + l * 70 + mm * 35 + n * 7 + o);
        t->fft = dtmb_fft_create();
    } else {
        for (int i = 0; i < 36; i++) t->si_l[i] = (uint16_t)i;
        for (int d = 0; d < DTMB_DATA; d++) t->d2l[d] = (uint16_t)(DTMB_SI + d);
    }
    for (int dd = 0; dd < DTMB_DATA; dd++) {
        if (dd == 0 || t->d2l[dd] != t->d2l[dd - 1] + 1) { t->seg_l[t->nseg] = t->d2l[dd]; t->seg_d[t->nseg] = (uint16_t)dd; t->seg_n[t->nseg] = 0; t->nseg++; }
        t->seg_n[t->nseg - 1]++;
    }
    /* Rahmenkopf: Chip 0 → +A, 1 → −A, I = Q; Phase ph = zyklischer Versatz → hdrw[ph .. ph+hdr) */
    const pn_def_t *d = t->pd;
    uint8_t pnb[1023]; pn_bits(d, d->core, pnb);
    t->hdrw = (uint32_t *)malloc(sizeof(uint32_t) * (size_t)(d->core + d->hdr));
    for (int i = 0; i < d->core + d->hdr; i++) {
        int16_t v = pnb[i % d->core] ? (int16_t)-t->hdr_amp : t->hdr_amp;
        uint16_t u = (uint16_t)v; t->hdrw[i] = (uint32_t)u | ((uint32_t)u << 16);
    }
    return t;
}
void dtmb_tx_free(dtmb_tx_t *t)
{
    if (!t) return;
    dtmb_ldpc_free(t->ldpc); free(t->ti); dtmb_fft_free(t->fft); free(t->hdrw); free(t);
}
dtmb_job_t *dtmb_job_alloc(const dtmb_tx_t *t) { (void)t; return (dtmb_job_t *)calloc(1, sizeof(dtmb_job_t)); }
void dtmb_job_free(dtmb_job_t *j) { free(j); }
int  dtmb_tx_frame_len(const dtmb_tx_t *t) { return t->frame_len; }
double dtmb_tx_peak_ratio(const dtmb_tx_t *t) { return t->peak_ratio; }
int  dtmb_tx_group_boundary(const dtmb_tx_t *t) { return t->grp_frame == 0; }
void dtmb_tx_set_profile(dtmb_tx_t *t, int on) { t->prof_on = on; }
void dtmb_tx_profile(const dtmb_tx_t *t, double out[4]) { memcpy(out, t->prof, sizeof t->prof); }

/* Faltungsverschachteler über einen Rahmen (3744 = 72·52, jeder Rahmen beginnt mit Zweig 0). Je Zweig liegen die 72
   Zugriffe auf aufeinanderfolgenden FIFO-Stellen → höchstens zwei zusammenhängende Abschnitte. */
static void ti_frame(dtmb_tx_t *t, const uint8_t *in, uint8_t *out)
{
    for (int b = 0; b < 52; b++) {
        const uint8_t *ip = in + b; uint8_t *op = out + b;
        int len = t->ti_len[b];
        if (!len) { for (int k = 0; k < 72; k++) op[52 * k] = ip[52 * k]; continue; }
        uint8_t *f = t->ti + t->ti_off[b];
        int p = t->ti_pos[b], k = 0;
        while (k < 72) {
            int run = len - p; if (run > 72 - k) run = 72 - k;
            uint8_t *fp = f + p;
            for (int j = 0; j < run; j++, k++) { uint8_t o = fp[j]; fp[j] = ip[52 * k]; op[52 * k] = o; }
            p += run; if (p == len) p = 0;
        }
        t->ti_pos[b] = p;
    }
}
/* Datensymbole (3744, der Reihe nach) in den Rahmenkörper (logische Reihenfolge) einsetzen: Abschnitte zwischen den SI */
static void place_data(const dtmb_tx_t *t, const uint8_t *d, uint8_t *lab)
{
    for (int i = 0; i < t->nseg; i++) memcpy(lab + t->seg_l[i], d + t->seg_d[i], t->seg_n[i]);
}

static void fec_group(dtmb_tx_t *t)
{
    double t0 = t->prof_on ? now_s() : 0;
    /* Verwürfelung je Signalrahmen-Nutzlast (wortweise; 188 Byte = 47 Wörter) */
    const int fw = t->pk_frame * 47;
    for (int f = 0; f < t->fpg; f++) {
        uint32_t *p = t->pkw + (size_t)f * fw;
        for (int i = 0; i < fw; i++) p[i] ^= t->scrw[i];
    }
    double t1 = 0, tl = 0;
    const uint8_t *src = (const uint8_t *)t->pkw;
    for (int cw = 0; cw < t->cwpg; cw++) {
        bw_t b = { t->info, 0, 0, 0 };
        for (int k = 0; k < t->nbch; k++, src += 94) {
            unsigned r = 0;
            for (int i = 0; i < 92; i += 4) {
                uint32_t x; memcpy(&x, src + i, 4);
                r = ((r << 8) & 0x3FF) ^ t->bch_tab[((r >> 2) ^ x) & 0xFF];
                r = ((r << 8) & 0x3FF) ^ t->bch_tab[((r >> 2) ^ (x >> 8)) & 0xFF];
                r = ((r << 8) & 0x3FF) ^ t->bch_tab[((r >> 2) ^ (x >> 16)) & 0xFF];
                r = ((r << 8) & 0x3FF) ^ t->bch_tab[((r >> 2) ^ (x >> 24)) & 0xFF];
                bw_put32(&b, brev_bytes(x));
            }
            r = ((r << 8) & 0x3FF) ^ t->bch_tab[((r >> 2) ^ src[92]) & 0xFF];
            r = ((r << 8) & 0x3FF) ^ t->bch_tab[((r >> 2) ^ src[93]) & 0xFF];
            bw_put(&b, (uint32_t)rev8_tab[src[92]] | (uint32_t)rev8_tab[src[93]] << 8, 16);
            /* Prüfbits r9 zuerst → LSB-zuerst-Wert = bitumgekehrtes r */
            uint32_t rr = (uint32_t)rev8_tab[r >> 2] | (uint32_t)(rev8_tab[r & 3] >> 6) << 8;
            bw_put(&b, rr, 10);
        }
        bw_flush(&b);
        t->info[b.idx] = 0; t->info[b.idx + 1] = 0;
        if (t->prof_on) { t1 = now_s(); }
        dtmb_ldpc_encode(t->ldpc, t->info, t->gbits + (size_t)cw * DTMB_CW_WORDS);
        if (t->prof_on) tl += now_s() - t1;
    }
    t->gbits[t->cwpg * DTMB_CW_WORDS] = 0; t->gbits[t->cwpg * DTMB_CW_WORDS + 1] = 0;
    double t2 = t->prof_on ? now_s() : 0;
    /* Labels der Gruppe: m Bits je Symbol, b0 zuerst = Bit 0 */
    if (t->c.qam != DTMB_4QAMNR) {
        /* Bytesicht auf den LSB-zuerst-Bitstrom (little endian): Bit t = Byte t/8, Stelle t%8 */
        int m = t->m, n = t->fpg * DTMB_DATA;
        const uint8_t *by = (const uint8_t *)t->gbits;
        uint8_t *L = t->lab;
        if (m == 2) for (int i = 0; i < n / 4; i++) { unsigned b = by[i]; L[4 * i] = b & 3; L[4 * i + 1] = b >> 2 & 3; L[4 * i + 2] = b >> 4 & 3; L[4 * i + 3] = (uint8_t)(b >> 6); }
        else if (m == 4) for (int i = 0; i < n / 2; i++) { unsigned b = by[i]; L[2 * i] = b & 15; L[2 * i + 1] = (uint8_t)(b >> 4); }
        else if (m == 6) for (int i = 0; i < n / 4; i++) {
            unsigned b0 = by[3 * i], b1 = by[3 * i + 1], b2 = by[3 * i + 2];
            L[4 * i] = b0 & 63; L[4 * i + 1] = (uint8_t)((b0 >> 6) | (b1 & 15) << 2); L[4 * i + 2] = (uint8_t)((b1 >> 4) | (b2 & 3) << 4); L[4 * i + 3] = (uint8_t)(b2 >> 2);
        } else {
            size_t pos = 0; uint32_t mask = (1u << m) - 1;
            for (int k = 0; k < n; k++, pos += (size_t)m) L[k] = (uint8_t)(get32(t->gbits, pos) & mask);
        }
    } else {
        const uint8_t *by = (const uint8_t *)t->gbits;
        for (int i = 0; i < 2 * DTMB_DATA / 8; i++) { unsigned b = by[i]; for (int k = 0; k < 8; k++) t->lab[8 * i + k] = b >> k & 1; }
    }
    if (t->prof_on) { double t3 = now_s(); t->prof[1] += tl; t->prof[0] += (t2 - t0) - tl; t->prof[2] += t3 - t2; }
}

int dtmb_tx_encode(dtmb_tx_t *t, dtmb_pkt_fn src, void *ctx, dtmb_job_t *job)
{
    if (t->grp_frame == 0) {
        for (int k = 0; k < t->pk_group; k++) {
            uint8_t *p = (uint8_t *)(t->pkw + (size_t)k * 47);
            if (!src(ctx, p)) {
                if (k == 0) return 0;
                p[0] = 0x47; p[1] = 0x1F; p[2] = 0xFF; p[3] = 0x10; memset(p + 4, 0xFF, 184);
            }
        }
        fec_group(t);
    }
    double t0 = t->prof_on ? now_s() : 0;
    if (t->c.qam != DTMB_4QAMNR) {
        ti_frame(t, t->lab + (size_t)t->grp_frame * DTMB_DATA, t->tiout);
        place_data(t, t->tiout, job->lab);
    } else {
        /* 4QAM-NR: Bitverschachtelung vor dem NR-Code, dann 8 Bit → 16 Bit → 8 4QAM-Symbole (b0 = I) */
        ti_frame(t, t->lab + (size_t)t->grp_frame * DTMB_DATA, t->nrbits);
        for (int byte = 0; byte < DTMB_DATA / 8; byte++) {
            unsigned in = 0;
            for (int k = 0; k < 8; k++) in = (in << 1) | t->nrbits[8 * byte + k];
            unsigned w = nr_tab[in];
            for (int s = 0; s < 8; s++) t->tiout[8 * byte + s] = (uint8_t)((w >> (15 - 2 * s) & 1) | ((w >> (14 - 2 * s) & 1) << 1));
        }
        place_data(t, t->tiout, job->lab);
    }
    for (int i = 0; i < DTMB_SI; i++) job->lab[t->si_l[i]] = (uint8_t)(64 + i);
    if (t->c.carriers == 3780) {   /* Abbildung + Frequenzverschachtelung (Anhang F) als Gather, erste Hälfte hier,
                                      zweite Hälfte in der Ausgabestufe (gleichmäßige Last auf beiden Kernen) */
        const uint8_t *lab = job->lab; uint32_t *x = job->x;
        for (int k = 0; k < DTMB_GATHER_SPLIT; k++) x[k] = t->map32[lab[t->p2l[k]]];
    }
    job->frame_no = t->frame_no;
    job->pn_phase = pn_phase(t->pd, t->c.pn_rotate, t->frame_no);
    t->frame_no++;
    t->grp_frame = (t->grp_frame + 1) % t->fpg;
    if (t->prof_on) t->prof[3] += now_s() - t0;
    return 1;
}

size_t dtmb_tx_scratch_len(const dtmb_tx_t *t) { (void)t; return 4 * dtmb_fft_buflen(); }

int dtmb_tx_render(const dtmb_tx_t *t, const dtmb_job_t *job, int16_t *scratch, int16_t *out)
{
    memcpy(out, t->hdrw + job->pn_phase, sizeof(uint32_t) * (size_t)t->hdr);
    int16_t *body = out + 2 * t->hdr;
    const uint8_t *lab = job->lab;
    if (t->c.carriers == 3780) {
        size_t L = dtmb_fft_buflen();
        int16_t *re = scratch, *im = scratch + L, *wr = scratch + 2 * L, *wi = scratch + 3 * L;
        uint32_t *x = (uint32_t *)job->x;   /* zweite Hälfte des Gathers (job gehört jetzt der Ausgabestufe) */
        for (int k = DTMB_GATHER_SPLIT; k < DTMB_BODY; k++) x[k] = t->map32[lab[t->p2l[k]]];
        dtmb_ifft(t->fft, (const int16_t *)job->x, re, im, wr, wi, body, t->post_scale);
    } else {
        uint32_t *b32 = (uint32_t *)body;
        for (int n = 0; n < DTMB_BODY; n++) b32[n] = t->map32[lab[n]];
    }
    return t->frame_len;
}

/* ================================================================ Selbsttest */
int dtmb_selftest(int verbose)
{
    int fail = 0;
    rev8_init(); nr_init();
    /* LDPC: schneller Pfad = direkte Rotation, für alle Raten */
    for (int rate = 0; rate < 3; rate++) {
        dtmb_ldpc_t *l = dtmb_ldpc_create(rate);
        if (!l) { printf("ldpc rate %d: create failed\n", rate); fail++; continue; }
        uint32_t info[200] = { 0 }, a[DTMB_CW_WORDS + 2], b[DTMB_CW_WORDS + 2];
        uint32_t s = 1u + (uint32_t)rate * 77u;
        int bad = 0;
        for (int it = 0; it < 6; it++) {
            int K = l->kb * 127;
            memset(info, 0, sizeof info);
            for (int i = 0; i < K; i++) {
                s = s * 1103515245u + 12345u;
                int bit = it == 0 ? (i == 0) : it == 1 ? (i == K - 1) : (int)((s >> 16) & 1);
                if (bit) info[i >> 5] |= 1u << (i & 31);
            }
            dtmb_ldpc_encode(l, info, a); dtmb_ldpc_encode_ref(l, info, b);
            if (memcmp(a, b, sizeof(uint32_t) * DTMB_CW_WORDS)) bad++;
        }
        double t0 = now_s(); int n = 0;
        while (now_s() - t0 < 0.2) { dtmb_ldpc_encode(l, info, a); n++; }
        if (verbose) printf("ldpc rate %s: %s, %.1f us/Codewort (%s)\n", dtmb_rate_name[rate], bad ? "FEHLER" : "ok", (now_s() - t0) / n * 1e6, dtmb_ldpc_backend());
        fail += bad;
        dtmb_ldpc_free(l);
    }
    /* IDFT (int16) gegen direkte DFT in double: SNR über alle Ausgänge */
    {
        dtmb_fft_t *f = dtmb_fft_create();
        size_t L = dtmb_fft_buflen();
        int16_t *re = (int16_t *)calloc(L, 2), *im = (int16_t *)calloc(L, 2), *wr = (int16_t *)calloc(L, 2), *wi = (int16_t *)calloc(L, 2);
        int16_t *in = (int16_t *)calloc(2 * L, 2), *out = (int16_t *)calloc(2 * L, 2);
        uint32_t s = 7;
        for (int i = 0; i < 2 * FFT_N; i++) { s = s * 1103515245u + 12345u; in[i] = (int16_t)(((int)(s >> 16 & 7) * 2 - 7) * 19); }
        int16_t *xin = (int16_t *)calloc(2 * L, 2);
        for (int k = 0; k < FFT_N; k++) { xin[2 * k] = in[k]; xin[2 * k + 1] = in[FFT_N + k]; }
        dtmb_ifft(f, xin, re, im, wr, wi, out, 32767);
        double es = 0, ee = 0;
        double *cs = (double *)malloc(sizeof(double) * FFT_N), *sn = (double *)malloc(sizeof(double) * FFT_N);
        for (int k = 0; k < FFT_N; k++) { cs[k] = cos(2 * M_PI * k / FFT_N); sn[k] = sin(2 * M_PI * k / FFT_N); }
        for (int n = 0; n < FFT_N; n++) {
            double xr = 0, xi = 0;
            for (int k = 0, a = 0; k < FFT_N; k++, a += n, a -= a >= FFT_N ? FFT_N : 0) {
                xr += in[k] * cs[a] - in[FFT_N + k] * sn[a]; xi += in[k] * sn[a] + in[FFT_N + k] * cs[a];
            }
            es += xr * xr + xi * xi; ee += pow(xr - out[2 * n], 2) + pow(xi - out[2 * n + 1], 2);
        }
        double snr = 10 * log10(es / ee);
        int ok = snr > 50;
        double t0 = now_s(); int n = 0;
        while (now_s() - t0 < 0.2) { dtmb_ifft(f, xin, re, im, wr, wi, out, 32767); n++; }
        if (verbose) printf("idft 3780 int16: %s (SNR %.1f dB, RMS aus %.0f), %.1f us (%s)\n", ok ? "ok" : "FEHLER", snr, sqrt(es / FFT_N / 2), (now_s() - t0) / n * 1e6, dtmb_fft_backend());
        fail += !ok;
        free(re); free(im); free(wr); free(wi); free(in); free(xin); free(out); free(cs); free(sn); dtmb_fft_free(f);
    }
    /* BCH: Tabelle gegen bitweise Rechnung */
    {
        dtmb_cfg_t c = { 3780, DTMB_64QAM, 1, 945, 720, 1 };
        dtmb_tx_t *t = dtmb_tx_create(&c, 3000);
        uint8_t m[94]; uint32_t s = 3; int bad = 0;
        for (int it = 0; it < 50; it++) {
            for (int i = 0; i < 94; i++) { s = s * 1103515245u + 12345u; m[i] = (uint8_t)(s >> 16); }
            unsigned r = 0, rb = 0;
            for (int i = 0; i < 94; i++) r = ((r << 8) & 0x3FF) ^ t->bch_tab[((r >> 2) ^ m[i]) & 0xFF];
            for (int i = 0; i < 752; i++) { unsigned bit = m[i >> 3] >> (7 - (i & 7)) & 1; unsigned fb = (rb >> 9 & 1) ^ bit; rb = (rb << 1) & 0x3FF; if (fb) rb ^= 9; }
            /* Syndrom über das ganze Codewort muss 0 sein */
            unsigned syn = 0;
            for (int i = 0; i < 762; i++) {
                unsigned bit = i < 752 ? (m[i >> 3] >> (7 - (i & 7)) & 1) : (r >> (9 - (i - 752)) & 1);
                syn = (syn << 1) | bit; if (syn & 0x400) syn ^= 0x409;
            }
            if (r != rb || syn) bad++;
        }
        if (verbose) printf("bch(762,752): %s\n", bad ? "FEHLER" : "ok");
        fail += bad;
        /* PN-Perioden */
        for (int k = 0; k < 3; k++) {
            const pn_def_t *d = &pn_defs[k];
            if (d->core == d->hdr) continue;
            uint8_t b[2048]; pn_bits(d, 2 * d->core, b);
            int per = memcmp(b, b + d->core, (size_t)d->core) == 0;
            int ones = 0; for (int i = 0; i < d->core; i++) ones += b[i];
            if (verbose) printf("pn%d: Periode %d %s, Einsen %d\n", d->hdr, d->core, per ? "ok" : "FEHLER", ones);
            fail += !per || ones != (d->core + 1) / 2;
        }
        dtmb_tx_free(t);
    }
    if (verbose) printf("selftest: %s\n", fail ? "FEHLER" : "ok");
    return fail;
}
