/*
 * dvbt2_fft.c – inverse FFT in int16-Festkomma für dvbt2_mod: Stockham-Autosort, gemischt Radix-8 (+ Radix-2/-4-Endstufe),
 * N = 1K … 32K (2^10 … 2^15). Gegenüber fft16.c (Radix-4) weniger Speicherdurchläufe (32K: 5 statt 8) und etwas
 * weniger Multiplikationen; außerdem direkt für 1K/4K/16K.
 *
 * Skalierung: insgesamt 2^-S mit S = floor(log2 N / 2) (wie fft16 bzw. die DIF-Variante für 1K/4K/16K): die
 * Radix-2-Ebenen ℓ = 0, 2, 4, … 2S-2 halbieren (VRHADD/VHSUB), alle übrigen addieren sättigend (VQADD/VQSUB).
 * Twiddles Q15 (VQRDMULH). Die C-Referenz und der NEON-Pfad verwenden dieselben Operationen in derselben
 * Reihenfolge → bitidentisch (Prüfung: md5 ARM = x86).
 * Radix-8-Schmetterling (DIF, inverse Richtung): a_i, a_{i+4} → u_i = a_i + a_{i+4}, v_i = (a_i − a_{i+4})·w8^i;
 * dann je Hälfte zwei Radix-2-Ebenen mit ·j; Ausgang y_k · W_nsub^{k·p} an y[s(8p+k)+q].
 */
#include "dvbt2_fft.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

#if defined(__ARM_NEON) || defined(__ARM_NEON__)
#include <arm_neon.h>
#define FN 1
#else
#define FN 0
#endif

#define MAXST 6
#include <time.h>
#ifndef T2FFT_PROF
#define T2FFT_PROF 0
#endif
struct t2fft {
    int n, L, S, nst8, rem;                 /* nst8 Radix-8-Stufen, Endstufe Radix 2^rem (rem = 0: letzte Radix-8-Stufe) */
    int16_t *wr[MAXST][8], *wi[MAXST][8];   /* Twiddles je Stufe, k = 1..7, Länge m */
    int16_t *ar, *ai, *br, *bi;
    int16_t *tail;                          /* Kopie der letzten Samples (Guard-Quelle): der Ausgang kann ungecachter
                                               DMA-Speicher sein (IIO direkt) – aus ihm zurückzulesen wäre teuer */
};

static void *al64(size_t b) { void *p = NULL; if (posix_memalign(&p, 64, (b + 63) & ~(size_t)63)) return NULL; memset(p, 0, b); return p; }

t2fft_t *t2fft_create(int n)
{
    int L = 0; while ((1 << L) < n) L++;
    if ((1 << L) != n || L < 10 || L > 15) return NULL;
    t2fft_t *f = calloc(1, sizeof *f);
    f->n = n; f->L = L; f->S = L / 2; f->nst8 = L / 3; f->rem = L % 3;
    int nsub = n;
    for (int st = 0; st < f->nst8; st++) {
        int m = nsub / 8;
        if (m > 1 || f->rem) {
            for (int k = 1; k < 8; k++) {
                f->wr[st][k] = al64((size_t)m * 2 + 16); f->wi[st][k] = al64((size_t)m * 2 + 16);
                for (int p = 0; p < m; p++) {
                    double th = 2.0 * M_PI * (double)k * p / nsub;
                    f->wr[st][k][p] = (int16_t)lrint(32767.0 * cos(th)); f->wi[st][k][p] = (int16_t)lrint(32767.0 * sin(th));
                }
            }
        }
        nsub = m;
    }
    f->ar = al64((size_t)n * 2); f->ai = al64((size_t)n * 2); f->br = al64((size_t)n * 2); f->bi = al64((size_t)n * 2);
    f->tail = al64(((size_t)n / 4 + 16) * 4);   /* Guard ≤ N/4 */
    return f;
}
void t2fft_free(t2fft_t *f)
{
    if (!f) return;
    for (int st = 0; st < MAXST; st++) for (int k = 0; k < 8; k++) { free(f->wr[st][k]); free(f->wi[st][k]); }
    free(f->ar); free(f->ai); free(f->br); free(f->bi); free(f->tail); free(f);
}
int t2fft_shift_bits(const t2fft_t *f) { return f->S; }
const char *t2fft_backend(void) { return FN ? "int16-radix8-neon" : "int16-radix8-c"; }

/* ---------------------------------------------------------------- skalare Grundoperationen (NEON-Semantik) */
static inline int16_t s16(int32_t x) { return (int16_t)(x > 32767 ? 32767 : x < -32768 ? -32768 : x); }
static inline int16_t qa(int16_t a, int16_t b) { return s16((int32_t)a + b); }
static inline int16_t qs(int16_t a, int16_t b) { return s16((int32_t)a - b); }
static inline int16_t ha(int16_t a, int16_t b) { return (int16_t)(((int32_t)a + b + 1) >> 1); }
static inline int16_t hs(int16_t a, int16_t b) { return (int16_t)(((int32_t)a - b) >> 1); }
static inline int16_t qm(int16_t a, int16_t b) { return s16((int32_t)(((int64_t)2 * a * b + 32768) >> 16)); }
static inline int16_t qn(int16_t a) { return s16(-(int32_t)a); }
#define C8 23170   /* round(32767/√2) */

typedef struct { int16_t r, i; } c16;
static inline c16 cadd(c16 a, c16 b, int h) { c16 o; if (h) { o.r = ha(a.r, b.r); o.i = ha(a.i, b.i); } else { o.r = qa(a.r, b.r); o.i = qa(a.i, b.i); } return o; }
static inline c16 csub(c16 a, c16 b, int h) { c16 o; if (h) { o.r = hs(a.r, b.r); o.i = hs(a.i, b.i); } else { o.r = qs(a.r, b.r); o.i = qs(a.i, b.i); } return o; }
static inline c16 mulj(c16 a) { c16 o; o.r = qn(a.i); o.i = a.r; return o; }
static inline c16 mw1(c16 a) { int16_t pr = qm(a.r, C8), pi = qm(a.i, C8); c16 o; o.r = qs(pr, pi); o.i = qa(pr, pi); return o; }
static inline c16 mw3(c16 a) { int16_t pr = qm(a.r, C8), pi = qm(a.i, C8); c16 o; o.r = qs(qn(pr), pi); o.i = qs(pr, pi); return o; }
static inline c16 cmul(c16 a, int16_t wr, int16_t wi) { c16 o; o.r = qs(qm(a.r, wr), qm(a.i, wi)); o.i = qa(qm(a.r, wi), qm(a.i, wr)); return o; }

/* Radix-8 (in a[0..7] → y[0..7] natürliche Reihenfolge), Ebenen-Halbierung hA/hB/hC */
static inline void r8(const c16 *a, c16 *y, int hA, int hB, int hC)
{
    c16 u0 = cadd(a[0], a[4], hA), u1 = cadd(a[1], a[5], hA), u2 = cadd(a[2], a[6], hA), u3 = cadd(a[3], a[7], hA);
    c16 v0 = csub(a[0], a[4], hA), v1 = mw1(csub(a[1], a[5], hA)), v2 = mulj(csub(a[2], a[6], hA)), v3 = mw3(csub(a[3], a[7], hA));
    c16 U0 = cadd(u0, u2, hB), U1 = cadd(u1, u3, hB), U2 = csub(u0, u2, hB), U3 = mulj(csub(u1, u3, hB));
    c16 V0 = cadd(v0, v2, hB), V1 = cadd(v1, v3, hB), V2 = csub(v0, v2, hB), V3 = mulj(csub(v1, v3, hB));
    y[0] = cadd(U0, U1, hC); y[4] = csub(U0, U1, hC); y[2] = cadd(U2, U3, hC); y[6] = csub(U2, U3, hC);
    y[1] = cadd(V0, V1, hC); y[5] = csub(V0, V1, hC); y[3] = cadd(V2, V3, hC); y[7] = csub(V2, V3, hC);
}
static inline void r4(const c16 *a, c16 *y, int hA, int hB)
{
    c16 u0 = cadd(a[0], a[2], hA), u1 = cadd(a[1], a[3], hA), v0 = csub(a[0], a[2], hA), v1 = mulj(csub(a[1], a[3], hA));
    y[0] = cadd(u0, u1, hB); y[2] = csub(u0, u1, hB); y[1] = cadd(v0, v1, hB); y[3] = csub(v0, v1, hB);
}
static inline int halve(const t2fft_t *f, int layer) { return !(layer & 1) && layer < 2 * f->S; }

/* ---------------------------------------------------------------- C-Referenz */
static void stage8_c(const t2fft_t *f, int st, const int16_t *xr, const int16_t *xi, int xs, int16_t *yr, int16_t *yi, int s, int m)
{
    const int hA = halve(f, 3 * st), hB = halve(f, 3 * st + 1), hC = halve(f, 3 * st + 2);
    for (int p = 0; p < m; p++)
        for (int q = 0; q < s; q++) {
            c16 a[8], y[8];
            for (int i = 0; i < 8; i++) { int idx = s * (p + i * m) + q; a[i].r = xr[xs * idx]; a[i].i = xi[xs * idx]; }
            r8(a, y, hA, hB, hC);
            for (int k = 0; k < 8; k++) {
                c16 o = k ? cmul(y[k], f->wr[st][k][p], f->wi[st][k][p]) : y[0];
                yr[s * (8 * p + k) + q] = o.r; yi[s * (8 * p + k) + q] = o.i;
            }
        }
}
/* Endstufe (m = 1): Radix 8/4/2, Ausgabe interleaved ab out + 2·guard */
static void final_c(const t2fft_t *f, int radix, const int16_t *xr, const int16_t *xi, int s, int16_t *out, int guard)
{
    const int base = 3 * f->nst8 - (radix == 8 ? 3 : 0);
    int16_t *o = out + 2 * guard;
    for (int q = 0; q < s; q++) {
        c16 a[8], y[8] = {{0, 0}};
        for (int i = 0; i < radix; i++) { a[i].r = xr[s * i + q]; a[i].i = xi[s * i + q]; }
        if (radix == 8) r8(a, y, halve(f, base), halve(f, base + 1), halve(f, base + 2));
        else if (radix == 4) r4(a, y, halve(f, base), halve(f, base + 1));
        else { y[0] = cadd(a[0], a[1], halve(f, base)); y[1] = csub(a[0], a[1], halve(f, base)); }
        for (int k = 0; k < radix; k++) { o[2 * (s * k + q)] = y[k].r; o[2 * (s * k + q) + 1] = y[k].i; }
    }
}

#if FN
/* ---------------------------------------------------------------- NEON (8 Lanes, int16x8_t), zwei Durchläufe je Schmetterling:
   gerade Ausgänge (aus u_i = a_i + a_{i+4}) und ungerade (aus v_i = (a_i − a_{i+4})·w8^i) getrennt, Eingänge je zweimal
   geladen → höchstens ~12 q-Register belegt (kein Auslagern). Arithmetik identisch zur C-Referenz. */
#define AI static inline __attribute__((always_inline))
typedef struct { int16x8_t r, i; } q16;
#define QADD(h, a, b) ((h) ? vrhaddq_s16(a, b) : vqaddq_s16(a, b))
#define QSUB(h, a, b) ((h) ? vhsubq_s16(a, b) : vqsubq_s16(a, b))
AI q16 qadd2(q16 a, q16 b, int h) { q16 o; o.r = QADD(h, a.r, b.r); o.i = QADD(h, a.i, b.i); return o; }
AI q16 qsub2(q16 a, q16 b, int h) { q16 o; o.r = QSUB(h, a.r, b.r); o.i = QSUB(h, a.i, b.i); return o; }
AI q16 qmulj(q16 a) { q16 o; o.r = vqnegq_s16(a.i); o.i = a.r; return o; }
AI q16 qmw1(q16 a) { int16x8_t pr = vqrdmulhq_n_s16(a.r, C8), pi = vqrdmulhq_n_s16(a.i, C8); q16 o; o.r = vqsubq_s16(pr, pi); o.i = vqaddq_s16(pr, pi); return o; }
AI q16 qmw3(q16 a) { int16x8_t pr = vqrdmulhq_n_s16(a.r, C8), pi = vqrdmulhq_n_s16(a.i, C8); q16 o; o.r = vqsubq_s16(vqnegq_s16(pr), pi); o.i = vqsubq_s16(pr, pi); return o; }
AI q16 qcmul(q16 a, int16x8_t wr, int16x8_t wi)
{ q16 o; o.r = vqsubq_s16(vqrdmulhq_s16(a.r, wr), vqrdmulhq_s16(a.i, wi)); o.i = vqaddq_s16(vqrdmulhq_s16(a.r, wi), vqrdmulhq_s16(a.i, wr)); return o; }

/* Ladefunktionen: planar (Abstand st zwischen den 8 Eingängen) bzw. interleaved I/Q */
AI q16 ldp(const int16_t *r, const int16_t *i, int off) { q16 o; o.r = vld1q_s16(r + off); o.i = vld1q_s16(i + off); return o; }
AI q16 ldi(const int16_t *in, int off) { int16x8x2_t t = vld2q_s16(in + 2 * off); q16 o; o.r = t.val[0]; o.i = t.val[1]; return o; }

/* gerade Hälfte: y0, y2, y4, y6 aus u */
#define EVEN_HALF(LD, hA, hB, hC, OUT)                                                            \
    do {                                                                                          \
        q16 u0 = qadd2(LD(0), LD(4), hA), u2 = qadd2(LD(2), LD(6), hA);                           \
        q16 U0 = qadd2(u0, u2, hB), U2 = qsub2(u0, u2, hB);                                       \
        q16 u1 = qadd2(LD(1), LD(5), hA), u3 = qadd2(LD(3), LD(7), hA);                           \
        q16 U1 = qadd2(u1, u3, hB), U3 = qmulj(qsub2(u1, u3, hB));                                \
        OUT(0, qadd2(U0, U1, hC)); OUT(4, qsub2(U0, U1, hC));                                     \
        OUT(2, qadd2(U2, U3, hC)); OUT(6, qsub2(U2, U3, hC));                                     \
    } while (0)
/* ungerade Hälfte: y1, y3, y5, y7 aus v */
#define ODD_HALF(LD, hA, hB, hC, OUT)                                                             \
    do {                                                                                          \
        q16 v0 = qsub2(LD(0), LD(4), hA), v2 = qmulj(qsub2(LD(2), LD(6), hA));                    \
        q16 V0 = qadd2(v0, v2, hB), V2 = qsub2(v0, v2, hB);                                       \
        q16 v1 = qmw1(qsub2(LD(1), LD(5), hA)), v3 = qmw3(qsub2(LD(3), LD(7), hA));               \
        q16 V1 = qadd2(v1, v3, hB), V3 = qmulj(qsub2(v1, v3, hB));                                \
        OUT(1, qadd2(V0, V1, hC)); OUT(5, qsub2(V0, V1, hC));                                     \
        OUT(3, qadd2(V2, V3, hC)); OUT(7, qsub2(V2, V3, hC));                                     \
    } while (0)

/* 8×8-Transposition int16 (Zeilen r[0..7] → Spalten) */
AI void tr8x8(int16x8_t *r)
{
    int16x8x2_t a = vtrnq_s16(r[0], r[1]), b = vtrnq_s16(r[2], r[3]), c = vtrnq_s16(r[4], r[5]), d = vtrnq_s16(r[6], r[7]);
    int32x4x2_t e = vtrnq_s32(vreinterpretq_s32_s16(a.val[0]), vreinterpretq_s32_s16(b.val[0]));
    int32x4x2_t f = vtrnq_s32(vreinterpretq_s32_s16(a.val[1]), vreinterpretq_s32_s16(b.val[1]));
    int32x4x2_t g = vtrnq_s32(vreinterpretq_s32_s16(c.val[0]), vreinterpretq_s32_s16(d.val[0]));
    int32x4x2_t h = vtrnq_s32(vreinterpretq_s32_s16(c.val[1]), vreinterpretq_s32_s16(d.val[1]));
    r[0] = vreinterpretq_s16_s32(vcombine_s32(vget_low_s32(e.val[0]), vget_low_s32(g.val[0])));
    r[4] = vreinterpretq_s16_s32(vcombine_s32(vget_high_s32(e.val[0]), vget_high_s32(g.val[0])));
    r[1] = vreinterpretq_s16_s32(vcombine_s32(vget_low_s32(f.val[0]), vget_low_s32(h.val[0])));
    r[5] = vreinterpretq_s16_s32(vcombine_s32(vget_high_s32(f.val[0]), vget_high_s32(h.val[0])));
    r[2] = vreinterpretq_s16_s32(vcombine_s32(vget_low_s32(e.val[1]), vget_low_s32(g.val[1])));
    r[6] = vreinterpretq_s16_s32(vcombine_s32(vget_high_s32(e.val[1]), vget_high_s32(g.val[1])));
    r[3] = vreinterpretq_s16_s32(vcombine_s32(vget_low_s32(f.val[1]), vget_low_s32(h.val[1])));
    r[7] = vreinterpretq_s16_s32(vcombine_s32(vget_high_s32(f.val[1]), vget_high_s32(h.val[1])));
}

/* erste Stufe (s = 1): Lanes = 8 aufeinanderfolgende p, Eingang interleaved; Ergebnis über L1-Puffer transponiert nach y[8p+k] */
AI void first8_n(const t2fft_t *f, const int16_t *in, int16_t *yr, int16_t *yi, int m, const int hA, const int hB, const int hC)
{
    int16_t S[2][8][8] __attribute__((aligned(16)));
    for (int p = 0; p < m; p += 8) {
#define LDF(i) ldi(in, p + (i) * m)
#define OUTF(k, v) do { q16 o_ = (k) ? qcmul(v, vld1q_s16(f->wr[0][k] + p), vld1q_s16(f->wi[0][k] + p)) : (v); vst1q_s16(S[0][k], o_.r); vst1q_s16(S[1][k], o_.i); } while (0)
        __builtin_prefetch(in + 2 * (p + 64)); __builtin_prefetch(in + 2 * (p + 4 * m + 64));
        EVEN_HALF(LDF, hA, hB, hC, OUTF);
        ODD_HALF(LDF, hA, hB, hC, OUTF);
#undef LDF
#undef OUTF
        for (int c = 0; c < 2; c++) {
            int16x8_t r[8];
            for (int k = 0; k < 8; k++) r[k] = vld1q_s16(S[c][k]);
            tr8x8(r);
            int16_t *d = (c ? yi : yr) + 8 * p;
            for (int k = 0; k < 8; k++) vst1q_s16(d + 8 * k, r[k]);
        }
    }
}
/* mittlere Stufen (s ≥ 8): Lanes = 8 aufeinanderfolgende q, Twiddles je p (vld1q_dup) */
AI void mid8_n(const t2fft_t *f, int st, const int16_t *xr, const int16_t *xi, int16_t *yr, int16_t *yi, int s, int m, const int hA, const int hB, const int hC)
{
    const int sm = s * m;
    for (int p = 0; p < m; p++) {
        for (int q = 0; q < s; q += 8) {
            const int16_t *br = xr + s * p + q, *bi = xi + s * p + q;
            int16_t *orr = yr + 8 * s * p + q, *oi = yi + 8 * s * p + q;
#define LDM(i) ldp(br, bi, (i) * sm)
#define OUTM(k, v) do { q16 o_ = (k) ? qcmul(v, vld1q_dup_s16(&f->wr[st][k][p]), vld1q_dup_s16(&f->wi[st][k][p])) : (v); vst1q_s16(orr + (k) * s, o_.r); vst1q_s16(oi + (k) * s, o_.i); } while (0)
            __builtin_prefetch(br + 64); __builtin_prefetch(bi + 64); __builtin_prefetch(br + 4 * sm + 64); __builtin_prefetch(bi + 4 * sm + 64);
            EVEN_HALF(LDM, hA, hB, hC, OUTM);
            ODD_HALF(LDM, hA, hB, hC, OUTM);
#undef LDM
#undef OUTM
        }
    }
}
AI void tail_st(int16_t *tl, int off, int16x8x2_t t)
{
    if (off >= 0) { vst2q_s16(tl + 2 * off, t); return; }
    int16_t tmp[16]; vst2q_s16(tmp, t); memcpy(tl, tmp - 2 * off, (size_t)(8 + off) * 4);
}
AI void final_n(int radix, const int16_t *xr, const int16_t *xi, int s, int16_t *out, int guard, int16_t *tl, const int h0, const int h1, const int h2)
{
    int16_t *o = out + 2 * guard;
    const int gs = radix * s - guard;           /* ab hier Guard-Quelle → zusätzlich nach tl */
    for (int q = 0; q < s; q += 8) {
#define LDE(i) ldp(xr + q, xi + q, (i) * s)
#define OUTE(k, v) do { q16 v_ = (v); int16x8x2_t t_ = {{ v_.r, v_.i }}; const int ix_ = (k) * s + q; vst2q_s16(o + 2 * ix_, t_); \
            if (ix_ + 8 > gs) tail_st(tl, ix_ - gs, t_); } while (0)
        __builtin_prefetch(xr + q + 64); __builtin_prefetch(xi + q + 64);
        if (radix == 8) { EVEN_HALF(LDE, h0, h1, h2, OUTE); ODD_HALF(LDE, h0, h1, h2, OUTE); }
        else if (radix == 4) {
            q16 a0 = LDE(0), a1 = LDE(1), a2 = LDE(2), a3 = LDE(3);
            q16 u0 = qadd2(a0, a2, h0), u1 = qadd2(a1, a3, h0), v0 = qsub2(a0, a2, h0), v1 = qmulj(qsub2(a1, a3, h0));
            OUTE(0, qadd2(u0, u1, h1)); OUTE(2, qsub2(u0, u1, h1)); OUTE(1, qadd2(v0, v1, h1)); OUTE(3, qsub2(v0, v1, h1));
        } else { q16 a0 = LDE(0), a1 = LDE(1); OUTE(0, qadd2(a0, a1, h0)); OUTE(1, qsub2(a0, a1, h0)); }
#undef LDE
#undef OUTE
    }
}
#define HSW(h0, h1, h2, CALL) do { int c_ = (h0) << 2 | (h1) << 1 | (h2); switch (c_) { \
    case 0: CALL(0,0,0); break; case 1: CALL(0,0,1); break; case 2: CALL(0,1,0); break; case 3: CALL(0,1,1); break; \
    case 4: CALL(1,0,0); break; case 5: CALL(1,0,1); break; case 6: CALL(1,1,0); break; default: CALL(1,1,1); break; } } while (0)
#endif

double t2fft_stage_s[8];
static double fnow(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return ts.tv_sec + ts.tv_nsec * 1e-9; }
void t2fft_ifft(t2fft_t *f, const int16_t *in, int16_t *out, int guard)
{
    double T0 = T2FFT_PROF ? fnow() : 0;
    const int n = f->n;
    const int16_t *xr = in, *xi = in + 1; int xs = 2;
    int16_t *yr = f->ar, *yi = f->ai;
    int nsub = n, s = 1;
    const int last8 = f->rem ? f->nst8 : f->nst8 - 1;    /* Anzahl Radix-8-Stufen mit Twiddles */
    for (int st = 0; st < last8; st++) {
        int m = nsub / 8;
#if FN
        const int hA = halve(f, 3 * st), hB = halve(f, 3 * st + 1), hC = halve(f, 3 * st + 2);
        if (st == 0) {
#define CF(a, b, c) first8_n(f, in, yr, yi, m, a, b, c)
            HSW(hA, hB, hC, CF);
#undef CF
        } else {
#define CM(a, b, c) mid8_n(f, st, xr, xi, yr, yi, s, m, a, b, c)
            HSW(hA, hB, hC, CM);
#undef CM
        }
#else
        stage8_c(f, st, xr, xi, xs, yr, yi, s, m);
#endif
        if (T2FFT_PROF) { double T1 = fnow(); t2fft_stage_s[st] += T1 - T0; T0 = T1; }
        xr = yr; xi = yi; xs = 1;
        if (yr == f->ar) { yr = f->br; yi = f->bi; } else { yr = f->ar; yi = f->ai; }
        nsub = m; s *= 8;
    }
    const int radix = f->rem ? (1 << f->rem) : 8;
#if FN
    {
        const int base = 3 * f->nst8 - (radix == 8 ? 3 : 0);
        const int h0 = halve(f, base), h1 = halve(f, base + 1), h2 = halve(f, base + 2);
#define CE(a, b, c) final_n(radix, xr, xi, s, out, guard, f->tail, a, b, c)
        HSW(h0, h1, h2, CE);
#undef CE
    }
    memcpy(out, f->tail, (size_t)guard * 4);
#else
    final_c(f, radix, xr, xi, s, out, guard);
    memcpy(out, out + 2 * n, (size_t)guard * 4);
#endif
    (void)xs;
    if (T2FFT_PROF) t2fft_stage_s[7] += fnow() - T0;
}
