/*
 * atsc.c – ATSC A/53 Part 2 (8-VSB): Randomizer, RS(207,187), Faltungs-Byte-Interleaver (B=52, M=4),
 * 12-fach verschachtelter 2/3-Trelliscoder mit Precoder, Segment-/Feldsync und VSB-Formung (Festkomma).
 * Copyright (c) 2026 DAB-Lab. Lizenz: MIT.
 */
#include "atsc.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>
#ifdef __ARM_NEON
#include <arm_neon.h>
#endif

/* ================================================================ GF(256), Polynom 0x11d, α = 2 */
static uint8_t gf_exp[512], gf_log[256];
static void gf_init(void)
{
    int x = 1;
    for (int i = 0; i < 255; i++) { gf_exp[i] = (uint8_t)x; gf_log[x] = (uint8_t)i; x <<= 1; if (x & 0x100) x ^= 0x11d; }
    for (int i = 255; i < 512; i++) gf_exp[i] = gf_exp[i - 255];
}
static uint8_t gf_mul(uint8_t a, uint8_t b) { return (a && b) ? gf_exp[gf_log[a] + gf_log[b]] : 0; }

/* ================================================================ Randomizer (A/53 §5.3.2)
   G(x) = x^16+x^13+x^12+x^11+x^7+x^6+x^3+x+1, Vorbelegung F180h zu Beginn jedes Felds; hier als
   bitgespiegeltes Galois-Register (Vorbelegung 018Fh, Rückkopplungsmaske A638h), Ausgangsbits D0..D7
   aus den Registerstellen 15, 13, 12, 9, 5, 4, 3, 2. Das MPEG-Sync-Byte wird nicht übertragen. */
static void rand_build(uint8_t *seq)
{
    static const uint16_t tap[8] = {0x8000, 0x2000, 0x1000, 0x0200, 0x0020, 0x0010, 0x0008, 0x0004};
    unsigned s = 0x018f;
    for (int i = 0; i < ATSC_DATA_SEGS * ATSC_DATA_LEN; i++) {
        uint8_t o = 0;
        for (int b = 0; b < 8; b++) if (s & tap[b]) o |= (uint8_t)(1u << b);
        seq[i] = o;
        s = (s & 1) ? (((s ^ 0xa638u) >> 1) | 0x8000u) : (s >> 1);
    }
}

/* ================================================================ RS(207,187), t = 10 (verkürzter RS(255,235))
   g(x) = Π_{i=0..19} (x − α^i). Paritätsregister r[0..19] in 5 Wörtern (Byte j in Wort j/4, Bits 8·(j%4)). */
static void rs_build(uint32_t tab[256][5])
{
    uint8_t g[21] = {1};             /* g[i] = Koeffizient von x^i */
    int deg = 0;
    for (int i = 0; i < 20; i++) {   /* g ← g·(x + α^i) */
        uint8_t r = gf_exp[i];
        for (int j = deg + 1; j >= 0; j--) g[j] = (uint8_t)((j ? g[j - 1] : 0) ^ gf_mul(g[j], r));
        deg++;
    }
    for (int fb = 0; fb < 256; fb++) {
        uint8_t b[20];
        for (int j = 0; j < 20; j++) b[j] = gf_mul((uint8_t)fb, g[19 - j]);   /* r[j] ^= fb·g[19−j] nach dem Schieben */
        for (int k = 0; k < 5; k++)
            tab[fb][k] = (uint32_t)b[4 * k] | (uint32_t)b[4 * k + 1] << 8 | (uint32_t)b[4 * k + 2] << 16 | (uint32_t)b[4 * k + 3] << 24;
    }
}

static void rs_encode(const uint32_t tab[256][5], const uint8_t *d, uint8_t *par)
{
    uint32_t w0 = 0, w1 = 0, w2 = 0, w3 = 0, w4 = 0;
    for (int i = 0; i < ATSC_DATA_LEN; i++) {
        unsigned fb = (d[i] ^ w0) & 0xff;
        const uint32_t *t = tab[fb];
        w0 = ((w0 >> 8) | (w1 << 24)) ^ t[0];
        w1 = ((w1 >> 8) | (w2 << 24)) ^ t[1];
        w2 = ((w2 >> 8) | (w3 << 24)) ^ t[2];
        w3 = ((w3 >> 8) | (w4 << 24)) ^ t[3];
        w4 = (w4 >> 8) ^ t[4];
    }
    uint32_t w[5] = {w0, w1, w2, w3, w4};
    for (int j = 0; j < 20; j++) par[j] = (uint8_t)(w[j >> 2] >> (8 * (j & 3)));
}

/* ================================================================ Trelliscoder (A/53 §5.3.4)
   X2 → Precoder Y2 = X2 ⊕ Y2(−12) → Z2;  X1 → Z1, Faltungscoder: Z0 = S0, S0' = X1 ⊕ S1, S1' = S0.
   Zustand = p<<2 | S1<<1 | S0. Symbolindex = 4·Z2 + 2·Z1 + Z0 (Pegel 2·i − 7). */
static void trellis_build(uint16_t tt[8][256])
{
    for (int st = 0; st < 8; st++)
        for (int byte = 0; byte < 256; byte++) {
            int p = st >> 2, s1 = (st >> 1) & 1, s0 = st & 1, syms = 0;
            for (int sh = 6; sh >= 0; sh -= 2) {
                int x2 = (byte >> (sh + 1)) & 1, x1 = (byte >> sh) & 1;
                int z2 = x2 ^ p, z1 = x1, z0 = s0;
                p = z2; int n0 = x1 ^ s1; s1 = s0; s0 = n0;
                syms = (syms << 3) | (z2 << 2 | z1 << 1 | z0);
            }
            tt[st][byte] = (uint16_t)(syms | ((p << 2 | s1 << 1 | s0) << 12));
        }
}

/* ================================================================ Feldsync (A/53 §5.3.5) */
static void pn_seq(int len, int nbits, unsigned taps_mask, unsigned preload, uint8_t *out)
{
    /* Fibonacci-Register, Bit nbits-1 = Ausgang (erste Stelle der Vorbelegung) */
    unsigned s = preload;
    for (int i = 0; i < len; i++) {
        out[i] = (uint8_t)((s >> (nbits - 1)) & 1);
        unsigned fb = (unsigned)__builtin_parity(s & taps_mask);
        s = ((s << 1) | fb) & ((1u << nbits) - 1);
    }
}

static void fsync_build(uint8_t fs[2][ATSC_SEG_SYMS])
{
    uint8_t pn511[511], pn63[63];
    /* PN511: x^9+x^7+x^6+x^4+x^3+x+1, PN63: x^6+x+1 (A/53 Part 2, §5.3.5). Hier als links schiebendes Register,
       Ausgang = MSB, Zustand = die ersten 9 bzw. 6 Ausgangsbits (000000010 bzw. 111001); Rückkopplung als Maske
       über die Registerstellen. Folge gegen gr-dtv geprüft (Feldsync-Segment bitgleich). */
    pn_seq(511, 9, 0x1b6, 0x002, pn511);
    pn_seq(63, 6, 0x30, 0x39, pn63);
    static const uint8_t mode[24] = {0,0,0,0, 1,0,1,0, 0,1,0,1, 1,1,1,1, 0,1,0,1, 1,0,1,0};
    for (int f = 0; f < 2; f++) {
        uint8_t *p = fs[f]; int i = 0;
        p[i++] = 1; p[i++] = 0; p[i++] = 0; p[i++] = 1;            /* Segmentsync +5 −5 −5 +5 */
        for (int j = 0; j < 511; j++) p[i++] = pn511[j];
        for (int j = 0; j < 63; j++) p[i++] = pn63[j];
        for (int j = 0; j < 63; j++) p[i++] = pn63[j] ^ (uint8_t)f; /* Feld 2: invertiert */
        for (int j = 0; j < 63; j++) p[i++] = pn63[j];
        for (int j = 0; j < 24; j++) p[i++] = mode[j];              /* VSB-Modus */
        for (int j = 0; j < 92; j++) p[i++] = pn63[j % 63];          /* reserviert (wie gr-dtv) */
        /* i == 820; die letzten 12 Symbole = letzte 12 Symbole des Vorsegments (Precode) */
        for (int j = 0; j < i; j++) p[j] = p[j] ? 6 : 1;             /* binär → ±5 (Index 6 / 1) */
        for (int j = i; j < ATSC_SEG_SYMS; j++) p[j] = 0;
    }
}

void atsc_enc_init(atsc_enc_t *e)
{
    memset(e, 0, sizeof *e);
    gf_init();
    rand_build(e->rand_seq);
    rs_build(e->rs_tab);
    trellis_build(e->tt);
    fsync_build(e->fsync);
}

/* 12 verschachtelte Segmente (12·207 Byte) → 12·832 Symbole. Byte-/Encoder-Zuordnung nach A/53 Tab. 5.2:
   Je 12 Byte werden reihum auf die Encoder verteilt; jeder Encoder gibt seine 4 Dibits (MSB zuerst) aus,
   die Encoder werden je Symbol weitergeschaltet, mit jedem Segmentsync um 4 versetzt. */
static void trellis_group(atsc_enc_t *e, uint8_t *out)
{
    const uint8_t *in = e->grp;
    int enc = ATSC_NCODERS - 4, o = 0, next_seg = 0;
    /* sy[pass][k] und sy[pass][k+12]: Symbol des Encoders k im Durchlauf pass, doppelt abgelegt, damit die 12
       Symbole eines Durchlaufs (Encoder enc, enc+1, … mod 12) ein zusammenhängender 12-Byte-Block sind */
    uint8_t sy[4][2 * ATSC_NCODERS];
    for (int c = 0; c < ATSC_GROUP_SEGS * ATSC_RS_LEN; c += ATSC_NCODERS) {
        int bumped = 0;
        if (o >= next_seg) { enc = (enc + 4) % ATSC_NCODERS; bumped = 1; }
        for (int i = 0, k = enc; i < ATSC_NCODERS; i++) {
            unsigned v = e->tt[e->tstate[k]][in[c + i]];
            e->tstate[k] = (uint8_t)(v >> 12);
            sy[0][k] = sy[0][k + 12] = (uint8_t)((v >> 9) & 7); sy[1][k] = sy[1][k + 12] = (uint8_t)((v >> 6) & 7);
            sy[2][k] = sy[2][k + 12] = (uint8_t)((v >> 3) & 7); sy[3][k] = sy[3][k + 12] = (uint8_t)(v & 7);
            if (++k == ATSC_NCODERS) k = 0;
        }
        for (int pass = 0; pass < 4; pass++) {
            if (o >= next_seg) {
                out[o++] = 6; out[o++] = 1; out[o++] = 1; out[o++] = 6;
                next_seg = o + ATSC_SEG_SYMS - 4;
                if (!bumped) enc = (enc + 4) % ATSC_NCODERS;
                bumped = 0;
            }
            memcpy(out + o, &sy[pass][enc], ATSC_NCODERS);   /* Encoder läuft 12 Schritte weiter = gleicher Wert */
            o += ATSC_NCODERS;
        }
    }
}

int atsc_enc_packet(atsc_enc_t *e, const uint8_t *ts, uint8_t *seg_out)
{
    uint8_t seg[ATSC_RS_LEN];
    const uint8_t *r = e->rand_seq + e->seg * ATSC_DATA_LEN;
    for (int i = 0; i < ATSC_DATA_LEN; i++) seg[i] = ts[i + 1] ^ r[i];
    rs_encode(e->rs_tab, seg, seg + ATSC_DATA_LEN);
    /* Interleaver: Byte t wird um 208·(t mod 52) Bytes verzögert (Zweig k: k·M Zellen, M = 4, B = 52) */
    uint8_t *g = e->grp + e->grp_n * ATSC_RS_LEN;
    uint32_t t = e->il_t;
    unsigned k = t % 52;
    for (int i = 0; i < ATSC_RS_LEN; i++, t++) {
        e->il_buf[t & 16383] = seg[i];
        g[i] = e->il_buf[(t - 208u * k) & 16383];
        if (++k == 52) k = 0;
    }
    e->il_t = t;
    int first_seg = e->seg - e->grp_n;          /* Segmentnummer am Gruppenanfang */
    e->grp_n++;
    if (++e->seg == ATSC_DATA_SEGS) e->seg = 0;
    if (e->grp_n < ATSC_GROUP_SEGS) return 0;
    e->grp_n = 0;
    int nseg = 0;
    uint8_t *o = seg_out;
    if (first_seg == 0) {                        /* Feldanfang: zuerst das Feldsync-Segment */
        memcpy(o, e->fsync[e->field], ATSC_SEG_SYMS - 12);
        memcpy(o + ATSC_SEG_SYMS - 12, e->last12, 12);
        o += ATSC_SEG_SYMS; nseg++;
    }
    trellis_group(e, o);
    nseg += ATSC_GROUP_SEGS;
    if (e->seg == 0) {                           /* Gruppe schloss das Feld ab */
        memcpy(e->last12, o + ATSC_GROUP_SEGS * ATSC_SEG_SYMS - 12, 12);
        e->field ^= 1;
    }
    return nseg;
}

/* ================================================================ VSB-Formung */
static double bessel_i0(double x)
{
    double s = 1, t = 1;
    for (int k = 1; k < 50; k++) { t *= (x / (2 * k)) * (x / (2 * k)); s += t; if (t < 1e-12 * s) break; }
    return s;
}

int atsc_vsb_init(atsc_vsb_t *v, int ntaps, double beta, double rms_target, int max_block)
{
    memset(v, 0, sizeof *v);
    if (ntaps < 9 || !(ntaps & 1)) return -1;
    const double a = 0.1152;   /* Roll-off bezogen auf die halbe Symbolrate (0,31 MHz je Flanke) */
    double *h = (double *)malloc(ntaps * sizeof(double)), mx = 0;
    for (int i = 0; i < ntaps; i++) {
        double t = (i - (ntaps - 1) / 2.0) / 2.0, x;   /* Zeit in „Halbsymbolen“ (2 Samples) */
        if (fabs(t) < 1e-9) x = 1 - a + 4 * a / M_PI;
        else if (fabs(fabs(4 * a * t) - 1) < 1e-9)
            x = a / sqrt(2.0) * ((1 + 2 / M_PI) * sin(M_PI / (4 * a)) + (1 - 2 / M_PI) * cos(M_PI / (4 * a)));
        else x = (sin(M_PI * t * (1 - a)) + 4 * a * t * cos(M_PI * t * (1 + a))) / (M_PI * t * (1 - (4 * a * t) * (4 * a * t)));
        if (beta > 0) { double r = 2.0 * i / (ntaps - 1) - 1.0; x *= bessel_i0(beta * sqrt(1 - r * r)) / bessel_i0(beta); }
        h[i] = x; if (fabs(x) > mx) mx = fabs(x);
    }
    v->ntaps = ntaps; v->cap = max_block;
    v->h = (int16_t *)malloc(ntaps * sizeof(int16_t));
    double e2 = 0, hr = 0, hi = 0;
    for (int i = 0; i < ntaps; i++) {
        v->h[i] = (int16_t)lrint(h[i] * 32767.0 / mx);
        double q = v->h[i] / 32768.0; e2 += q * q;
        hr += q * cos(M_PI / 2 * i); hi += q * sin(M_PI / 2 * i);   /* H(fs/4) für den Pilot */
    }
    free(h);
    /* Ausgangsleistung = A²·(21·Σh² + 1,25²·|H(fs/4)|²); Datensymbole gleichverteilt (E[x²] = 21) */
    double p1 = 21.0 * e2 + 1.5625 * (hr * hr + hi * hi);
    v->A = rms_target / sqrt(p1);
    if (v->A * 8.25 > 32000) v->A = 32000 / 8.25;
    v->rms = v->A * sqrt(p1);
    static const int c[4] = {1, -1, -1, 1};   /* (−j)^m: Re bei geradem m, Im bei ungeradem m */
    for (int ph = 0; ph < 4; ph++)
        for (int i = 0; i < 8; i++) v->wtab[ph][i] = (int16_t)lrint(c[ph] * (2 * i - 7 + 1.25) * v->A);
    v->peak_w = 8.25 * v->A;
    /* NEON-Form: gerade/ungerade Taps getrennt, auf Vielfache von 4 mit Nullen aufgefüllt */
    v->ngroups = (ntaps + 7) / 8;
    v->he = (int16_t *)calloc(4 * v->ngroups, sizeof(int16_t)); v->ho = (int16_t *)calloc(4 * v->ngroups, sizeof(int16_t));
    for (int i = 0; i < ntaps; i++) { if (i & 1) v->ho[i >> 1] = v->h[i]; else v->he[i >> 1] = v->h[i]; }
    v->buf = (int16_t *)calloc(atsc_vsb_wbuf_len(v, max_block), sizeof(int16_t));
    v->hist = (int16_t *)calloc((size_t)ntaps, sizeof(int16_t));
    return 0;
}

void atsc_vsb_free(atsc_vsb_t *v) { free(v->h); free(v->he); free(v->ho); free(v->buf); free(v->hist); memset(v, 0, sizeof *v); }

const char *atsc_vsb_backend(void)
{
#ifdef __ARM_NEON
    return "neon";
#else
    return "c";
#endif
}

void atsc_vsb_map(atsc_vsb_t *v, const uint8_t *sym, size_t n, int16_t *wbuf)
{
    const int N = v->ntaps;
    memcpy(wbuf, v->hist, (size_t)(N - 1) * sizeof(int16_t));
    int16_t *w = wbuf + (N - 1);
    const int16_t *t0 = v->wtab[0], *t1 = v->wtab[1], *t2 = v->wtab[2], *t3 = v->wtab[3];
    for (size_t i = 0; i < n; i += 4) {
        uint32_t q; memcpy(&q, sym + i, 4);              /* 4 Symbole auf einmal (little endian) */
        w[i] = t0[q & 7]; w[i + 1] = t1[(q >> 8) & 7]; w[i + 2] = t2[(q >> 16) & 7]; w[i + 3] = t3[q >> 24];
    }
    memcpy(v->hist, wbuf + n, (size_t)(N - 1) * sizeof(int16_t));
}

size_t atsc_vsb_run(atsc_vsb_t *v, const uint8_t *sym, size_t n, int16_t *out)
{
    atsc_vsb_map(v, sym, n, v->buf);
    atsc_vsb_filter(v, v->buf, 0, n, out);
    return n;
}

/* y[n] = Σ_t h[t]·w[n−N+1+t]; gerade t → E, ungerade t → O (N ungerade, h symmetrisch).
   Ausgabe: n gerade: I = E, Q = O; n ungerade: I = O, Q = E. */
void atsc_vsb_filter(const atsc_vsb_t *v, const int16_t *wbuf, size_t i0, size_t i1, int16_t *out)
{
    const int N = v->ntaps;
    const int16_t *h = v->h;
#ifdef __ARM_NEON
    /* je Gruppe 4 gerade + 4 ungerade Taps (he/ho, mit Nullen aufgefüllt), 8 Ausgänge je Durchlauf */
    static const uint16_t msk[4] = {0xffff, 0, 0xffff, 0};
    const uint16x4_t m = vld1_u16(msk);
    const int16_t *he = v->he, *ho = v->ho; const int G = v->ngroups; (void)h;
    for (size_t i = i0; i < i1; i += 8) {
        const int16_t *x = wbuf + i;
        int32x4_t E0 = vdupq_n_s32(0), O0 = vdupq_n_s32(0), E1 = vdupq_n_s32(0), O1 = vdupq_n_s32(0);
        int32x4_t F0 = vdupq_n_s32(0), P0 = vdupq_n_s32(0), F1 = vdupq_n_s32(0), P1 = vdupq_n_s32(0);   /* 2. Satz: kürzere Abhängigkeitsketten */
        for (int g = 0; g < G; g++, x += 8) {
            const int16x4_t ce = vld1_s16(he + 4 * g), co = vld1_s16(ho + 4 * g);
            int16x8_t a0 = vld1q_s16(x), a1 = vld1q_s16(x + 1), a2 = vld1q_s16(x + 2), a3 = vld1q_s16(x + 3);
            E0 = vmlal_lane_s16(E0, vget_low_s16(a0), ce, 0); E1 = vmlal_lane_s16(E1, vget_high_s16(a0), ce, 0);
            O0 = vmlal_lane_s16(O0, vget_low_s16(a1), co, 0); O1 = vmlal_lane_s16(O1, vget_high_s16(a1), co, 0);
            F0 = vmlal_lane_s16(F0, vget_low_s16(a2), ce, 1); F1 = vmlal_lane_s16(F1, vget_high_s16(a2), ce, 1);
            P0 = vmlal_lane_s16(P0, vget_low_s16(a3), co, 1); P1 = vmlal_lane_s16(P1, vget_high_s16(a3), co, 1);
            int16x8_t a4 = vld1q_s16(x + 4), a5 = vld1q_s16(x + 5), a6 = vld1q_s16(x + 6), a7 = vld1q_s16(x + 7);
            E0 = vmlal_lane_s16(E0, vget_low_s16(a4), ce, 2); E1 = vmlal_lane_s16(E1, vget_high_s16(a4), ce, 2);
            O0 = vmlal_lane_s16(O0, vget_low_s16(a5), co, 2); O1 = vmlal_lane_s16(O1, vget_high_s16(a5), co, 2);
            F0 = vmlal_lane_s16(F0, vget_low_s16(a6), ce, 3); F1 = vmlal_lane_s16(F1, vget_high_s16(a6), ce, 3);
            P0 = vmlal_lane_s16(P0, vget_low_s16(a7), co, 3); P1 = vmlal_lane_s16(P1, vget_high_s16(a7), co, 3);
        }
        E0 = vaddq_s32(E0, F0); E1 = vaddq_s32(E1, F1); O0 = vaddq_s32(O0, P0); O1 = vaddq_s32(O1, P1);
        int16x4_t e0 = vqrshrn_n_s32(E0, 15), o0 = vqrshrn_n_s32(O0, 15);
        int16x4_t e1 = vqrshrn_n_s32(E1, 15), o1 = vqrshrn_n_s32(O1, 15);
        int16x4x2_t z0, z1;
        z0.val[0] = vbsl_s16(m, e0, o0); z0.val[1] = vbsl_s16(m, o0, e0);
        z1.val[0] = vbsl_s16(m, e1, o1); z1.val[1] = vbsl_s16(m, o1, e1);
        vst2_s16(out + 2 * i, z0); vst2_s16(out + 2 * i + 8, z1);
    }
#else
    for (size_t i = i0; i < i1; i += 4) {
        const int16_t *b = wbuf + i;
        int32_t E[4] = {0, 0, 0, 0}, O[4] = {0, 0, 0, 0};
        for (int t = 0; t < N; t += 2) for (int j = 0; j < 4; j++) E[j] += (int32_t)h[t] * b[t + j];
        for (int t = 1; t < N; t += 2) for (int j = 0; j < 4; j++) O[j] += (int32_t)h[t] * b[t + j];
        for (int j = 0; j < 4; j++) {
            int32_t e = (E[j] + 16384) >> 15, o = (O[j] + 16384) >> 15;
            e = e > 32767 ? 32767 : e < -32768 ? -32768 : e;
            o = o > 32767 ? 32767 : o < -32768 ? -32768 : o;
            int16_t I = (int16_t)((j & 1) ? o : e), Q = (int16_t)((j & 1) ? e : o);
            out[2 * (i + j)] = I; out[2 * (i + j) + 1] = Q;
        }
    }
#endif
}
