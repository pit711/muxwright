/*
 * fft16.c – inverse FFT in int16-Festkomma, Stockham-Autosort Radix-4 (+ eine Radix-2-Endstufe),
 * feste Skalierung 1/2 je Radix-4-Stufe (Halbierungs-Addierer), Twiddles Q15, sättigende Arithmetik.
 * Die C-Referenz und die NEON-Variante liefern bitidentische Ergebnisse (gleiche Rundungsregeln:
 * VRHADD, VHSUB, VQADD/VQSUB, VQRDMULH).
 *
 * Eingang: planar re/im (n Werte), Ausgang: interleaved I/Q mit zyklischem Präfix (n+guard Samples).
 * Die Endstufe (Radix-2 ohne Twiddles) schreibt direkt ins Ausgabeformat inklusive Guard-Kopie.
 */
#include "dvbmod.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

#if defined(__ARM_NEON) || defined(__ARM_NEON__)
#include <arm_neon.h>
#define FFT16_NEON 1
#else
#define FFT16_NEON 0
#endif

#define MAX_STAGES 7
#ifndef FFT_PLD
#define FFT_PLD 64   /* Prefetch-Abstand in Elementen (int16) */
#endif

struct fft16 {
    int n, nst4;                   /* Radix-4-Stufen */
    int16_t *w1r[MAX_STAGES], *w1i[MAX_STAGES], *w2r[MAX_STAGES], *w2i[MAX_STAGES], *w3r[MAX_STAGES], *w3i[MAX_STAGES];
    int16_t *ar, *ai, *br, *bi;    /* Ping-Pong-Puffer */
    int16_t *sr, *si;              /* L1-Scratch für fusionierte Stufen */
};

static void *al64(size_t bytes)
{
    void *p = NULL;
#if defined(_ISOC11_SOURCE) || (defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L)
    bytes = (bytes + 63) & ~(size_t)63;
    p = aligned_alloc(64, bytes);
#else
    if (posix_memalign(&p, 64, bytes)) p = NULL;
#endif
    return p;
}

const char *fft16_backend(void) { return FFT16_NEON ? "int16-radix4-neon" : "int16-radix4-c"; }



fft16_t *fft16_create(int n)
{
    int nst4 = 0, t = n;
    while (t > 2) { if (t % 4) return NULL; t /= 4; nst4++; }
    if (t != 2 || nst4 > MAX_STAGES) return NULL;
    fft16_t *f = (fft16_t *)calloc(1, sizeof *f);
    f->n = n; f->nst4 = nst4;
    int nsub = n;
    for (int s = 0; s < nst4; s++) {
        int m = nsub / 4;
        f->w1r[s] = (int16_t *)al64(m * 2); f->w1i[s] = (int16_t *)al64(m * 2);
        f->w2r[s] = (int16_t *)al64(m * 2); f->w2i[s] = (int16_t *)al64(m * 2);
        f->w3r[s] = (int16_t *)al64(m * 2); f->w3i[s] = (int16_t *)al64(m * 2);
        for (int p = 0; p < m; p++) {
            double th = 2.0 * M_PI * p / nsub;   /* inverse: e^{+j th} */
            f->w1r[s][p] = (int16_t)lrint(32767.0 * cos(th));     f->w1i[s][p] = (int16_t)lrint(32767.0 * sin(th));
            f->w2r[s][p] = (int16_t)lrint(32767.0 * cos(2 * th)); f->w2i[s][p] = (int16_t)lrint(32767.0 * sin(2 * th));
            f->w3r[s][p] = (int16_t)lrint(32767.0 * cos(3 * th)); f->w3i[s][p] = (int16_t)lrint(32767.0 * sin(3 * th));
        }
        nsub = m;
    }
    f->ar = (int16_t *)al64(n * 2); f->ai = (int16_t *)al64(n * 2);
    f->br = (int16_t *)al64(n * 2); f->bi = (int16_t *)al64(n * 2);
    f->sr = (int16_t *)al64(8192 * 2); f->si = (int16_t *)al64(8192 * 2);
    return f;
}

void fft16_free(fft16_t *f)
{
    if (!f) return;
    for (int s = 0; s < f->nst4; s++) { free(f->w1r[s]); free(f->w1i[s]); free(f->w2r[s]); free(f->w2i[s]); free(f->w3r[s]); free(f->w3i[s]); }
    free(f->ar); free(f->ai); free(f->br); free(f->bi); free(f->sr); free(f->si); free(f);
}

int fft16_shift_bits(const fft16_t *f) { return f->nst4; }

/* ---------------------------------------------------------------- skalare Hilfsfunktionen (NEON-Semantik) */
static inline int16_t sat16(int32_t x) { return (int16_t)(x > 32767 ? 32767 : (x < -32768 ? -32768 : x)); }
static inline int16_t qadd(int16_t a, int16_t b) { return sat16((int32_t)a + b); }
static inline int16_t qsub(int16_t a, int16_t b) { return sat16((int32_t)a - b); }
static inline int16_t rhadd(int16_t a, int16_t b) { return (int16_t)(((int32_t)a + b + 1) >> 1); }
static inline int16_t hsub(int16_t a, int16_t b) { return (int16_t)(((int32_t)a - b) >> 1); }
static inline int16_t qrdmulh(int16_t a, int16_t b) { return sat16((int32_t)(((int64_t)2 * a * b + 32768) >> 16)); }

/* xs = Elementabstand des Eingangs (1 planar, 2 interleaved: xi = xr + 1) */
static void stage_r4_c(const int16_t *xr, const int16_t *xi, int xs, int16_t *yr, int16_t *yi, int nsub, int s,
                       const int16_t *w1r, const int16_t *w1i, const int16_t *w2r, const int16_t *w2i,
                       const int16_t *w3r, const int16_t *w3i)
{
    const int m = nsub / 4;
    for (int p = 0; p < m; p++) {
        const int16_t c1r = w1r[p], c1i = w1i[p], c2r = w2r[p], c2i = w2i[p], c3r = w3r[p], c3i = w3i[p];
        const int16_t *xa = xr + xs * s * p, *xb = xr + xs * s * (p + m), *xc = xr + xs * s * (p + 2 * m), *xd = xr + xs * s * (p + 3 * m);
        const int16_t *ya = xi + xs * s * p, *yb = xi + xs * s * (p + m), *yc = xi + xs * s * (p + 2 * m), *yd = xi + xs * s * (p + 3 * m);
        int16_t *o0r = yr + s * 4 * p, *o1r = o0r + s, *o2r = o1r + s, *o3r = o2r + s;
        int16_t *o0i = yi + s * 4 * p, *o1i = o0i + s, *o2i = o1i + s, *o3i = o2i + s;
        for (int q = 0; q < s; q++) {
            const int qx = q * xs;
            int16_t apcr = rhadd(xa[qx], xc[qx]), apci = rhadd(ya[qx], yc[qx]);
            int16_t amcr = hsub(xa[qx], xc[qx]),  amci = hsub(ya[qx], yc[qx]);
            int16_t bpdr = rhadd(xb[qx], xd[qx]), bpdi = rhadd(yb[qx], yd[qx]);
            int16_t bmdr = hsub(xb[qx], xd[qx]),  bmdi = hsub(yb[qx], yd[qx]);
            o0r[q] = qadd(apcr, bpdr); o0i[q] = qadd(apci, bpdi);
            int16_t t1r = qsub(amcr, bmdi), t1i = qadd(amci, bmdr);     /* amc + j·bmd */
            int16_t t2r = qsub(apcr, bpdr), t2i = qsub(apci, bpdi);
            int16_t t3r = qadd(amcr, bmdi), t3i = qsub(amci, bmdr);     /* amc − j·bmd */
            o1r[q] = qsub(qrdmulh(t1r, c1r), qrdmulh(t1i, c1i)); o1i[q] = qadd(qrdmulh(t1r, c1i), qrdmulh(t1i, c1r));
            o2r[q] = qsub(qrdmulh(t2r, c2r), qrdmulh(t2i, c2i)); o2i[q] = qadd(qrdmulh(t2r, c2i), qrdmulh(t2i, c2r));
            o3r[q] = qsub(qrdmulh(t3r, c3r), qrdmulh(t3i, c3i)); o3i[q] = qadd(qrdmulh(t3r, c3i), qrdmulh(t3i, c3r));
        }
    }
}

/* Endstufe Radix-2 (nsub = 2, s = n/2): y[q] = x[q] + x[q+s], y[q+s] = x[q] − x[q+s]; Ausgabe interleaved + Guard */
static void stage_r2_out_c(const int16_t *xr, const int16_t *xi, int16_t *out, int n, int guard)
{
    const int s = n / 2;
    int16_t *o = out + 2 * guard;
    for (int q = 0; q < s; q++) {
        int16_t r0 = qadd(xr[q], xr[q + s]), i0 = qadd(xi[q], xi[q + s]);
        int16_t r1 = qsub(xr[q], xr[q + s]), i1 = qsub(xi[q], xi[q + s]);
        o[2 * q] = r0; o[2 * q + 1] = i0;
        o[2 * (q + s)] = r1; o[2 * (q + s) + 1] = i1;
    }
    memcpy(out, out + 2 * n, (size_t)guard * 4);   /* zyklisches Präfix = letzte guard Samples */
}

#if FFT16_NEON
static inline void cmul_q15(int16x8_t xr, int16x8_t xi, int16x8_t wr, int16x8_t wi, int16x8_t *yr, int16x8_t *yi)
{
    *yr = vqsubq_s16(vqrdmulhq_s16(xr, wr), vqrdmulhq_s16(xi, wi));
    *yi = vqaddq_s16(vqrdmulhq_s16(xr, wi), vqrdmulhq_s16(xi, wr));
}

#define R4_CORE()                                                                 \
    int16x8_t apcr = vrhaddq_s16(ar_, cr_), apci = vrhaddq_s16(ai_, ci_);         \
    int16x8_t amcr = vhsubq_s16(ar_, cr_),  amci = vhsubq_s16(ai_, ci_);          \
    int16x8_t bpdr = vrhaddq_s16(br_, dr_), bpdi = vrhaddq_s16(bi_, di_);         \
    int16x8_t bmdr = vhsubq_s16(br_, dr_),  bmdi = vhsubq_s16(bi_, di_);          \
    int16x8_t y0r = vqaddq_s16(apcr, bpdr), y0i = vqaddq_s16(apci, bpdi);         \
    int16x8_t t1r = vqsubq_s16(amcr, bmdi), t1i = vqaddq_s16(amci, bmdr);         \
    int16x8_t t2r = vqsubq_s16(apcr, bpdr), t2i = vqsubq_s16(apci, bpdi);         \
    int16x8_t t3r = vqaddq_s16(amcr, bmdi), t3i = vqsubq_s16(amci, bmdr);         \
    int16x8_t y1r, y1i, y2r, y2i, y3r, y3i;                                       \
    cmul_q15(t1r, t1i, c1r, c1i, &y1r, &y1i);                                     \
    cmul_q15(t2r, t2i, c2r, c2i, &y2r, &y2i);                                     \
    cmul_q15(t3r, t3i, c3r, c3i, &y3r, &y3i);

/* Radix-4-Stufe für p in [p0,p1): Eingang x[s·p + k·kstride + q], Ausgang y[4·s·p + j·s + q]
   (Basiszeiger werden vom Aufrufer so gelegt, dass die Indizes stimmen → erlaubt Stufen-Fusion über L1-Scratch). */
static void stage_r4_neon(const int16_t *xr, const int16_t *xi, int16_t *yr, int16_t *yi, int s, int p0, int p1, int kstride,
                          const int16_t *w1r, const int16_t *w1i, const int16_t *w2r, const int16_t *w2i,
                          const int16_t *w3r, const int16_t *w3i)
{
    const int m = kstride;   /* Abstand der 4 Eingänge */
    if (s == 1) {
        /* Eingang interleaved I/Q (xr = Basis); Lanes = 8 aufeinanderfolgende p; Ausgabe y[4p+j] → vst4 */
        for (int p = p0; p < p1; p += 8) {
            int16x8x2_t la = vld2q_s16(xr + 2 * p), lb = vld2q_s16(xr + 2 * (p + m)), lc = vld2q_s16(xr + 2 * (p + 2 * m)), ld = vld2q_s16(xr + 2 * (p + 3 * m));
            int16x8_t ar_ = la.val[0], br_ = lb.val[0], cr_ = lc.val[0], dr_ = ld.val[0];
            int16x8_t ai_ = la.val[1], bi_ = lb.val[1], ci_ = lc.val[1], di_ = ld.val[1];
            __builtin_prefetch(xr + 2 * (p + FFT_PLD)); __builtin_prefetch(xr + 2 * (p + m + FFT_PLD));
            __builtin_prefetch(xr + 2 * (p + 2 * m + FFT_PLD)); __builtin_prefetch(xr + 2 * (p + 3 * m + FFT_PLD));
            int16x8_t c1r = vld1q_s16(w1r + p), c1i = vld1q_s16(w1i + p), c2r = vld1q_s16(w2r + p), c2i = vld1q_s16(w2i + p);
            int16x8_t c3r = vld1q_s16(w3r + p), c3i = vld1q_s16(w3i + p);
            R4_CORE()
            int16x8x4_t vr = {{y0r, y1r, y2r, y3r}}, vi = {{y0i, y1i, y2i, y3i}};
            vst4q_s16(yr + 4 * p, vr); vst4q_s16(yi + 4 * p, vi);
        }
    } else if (s == 4) {
        /* Lanes = (p, p+1) × q 0..3 */
        for (int p = p0; p < p1; p += 2) {
            const int16_t *xa = xr + 4 * p, *ya = xi + 4 * p;
            int16x8_t ar_ = vld1q_s16(xa), br_ = vld1q_s16(xa + m), cr_ = vld1q_s16(xa + 2 * m), dr_ = vld1q_s16(xa + 3 * m);
            int16x8_t ai_ = vld1q_s16(ya), bi_ = vld1q_s16(ya + m), ci_ = vld1q_s16(ya + 2 * m), di_ = vld1q_s16(ya + 3 * m);
            __builtin_prefetch(xa + FFT_PLD); __builtin_prefetch(xa + m + FFT_PLD); __builtin_prefetch(xa + 2 * m + FFT_PLD); __builtin_prefetch(xa + 3 * m + FFT_PLD);
            __builtin_prefetch(ya + FFT_PLD); __builtin_prefetch(ya + m + FFT_PLD); __builtin_prefetch(ya + 2 * m + FFT_PLD); __builtin_prefetch(ya + 3 * m + FFT_PLD);
            int16x8_t c1r = vcombine_s16(vdup_n_s16(w1r[p]), vdup_n_s16(w1r[p + 1])), c1i = vcombine_s16(vdup_n_s16(w1i[p]), vdup_n_s16(w1i[p + 1]));
            int16x8_t c2r = vcombine_s16(vdup_n_s16(w2r[p]), vdup_n_s16(w2r[p + 1])), c2i = vcombine_s16(vdup_n_s16(w2i[p]), vdup_n_s16(w2i[p + 1]));
            int16x8_t c3r = vcombine_s16(vdup_n_s16(w3r[p]), vdup_n_s16(w3r[p + 1])), c3i = vcombine_s16(vdup_n_s16(w3i[p]), vdup_n_s16(w3i[p + 1]));
            R4_CORE()
            int16_t *o = yr + 16 * p, *oi = yi + 16 * p;
            vst1_s16(o + 0,  vget_low_s16(y0r)); vst1_s16(o + 16, vget_high_s16(y0r));
            vst1_s16(o + 4,  vget_low_s16(y1r)); vst1_s16(o + 20, vget_high_s16(y1r));
            vst1_s16(o + 8,  vget_low_s16(y2r)); vst1_s16(o + 24, vget_high_s16(y2r));
            vst1_s16(o + 12, vget_low_s16(y3r)); vst1_s16(o + 28, vget_high_s16(y3r));
            vst1_s16(oi + 0,  vget_low_s16(y0i)); vst1_s16(oi + 16, vget_high_s16(y0i));
            vst1_s16(oi + 4,  vget_low_s16(y1i)); vst1_s16(oi + 20, vget_high_s16(y1i));
            vst1_s16(oi + 8,  vget_low_s16(y2i)); vst1_s16(oi + 24, vget_high_s16(y2i));
            vst1_s16(oi + 12, vget_low_s16(y3i)); vst1_s16(oi + 28, vget_high_s16(y3i));
        }
    } else {
        for (int p = p0; p < p1; p++) {
            int16x8_t c1r = vdupq_n_s16(w1r[p]), c1i = vdupq_n_s16(w1i[p]), c2r = vdupq_n_s16(w2r[p]), c2i = vdupq_n_s16(w2i[p]);
            int16x8_t c3r = vdupq_n_s16(w3r[p]), c3i = vdupq_n_s16(w3i[p]);
            const int16_t *xa = xr + s * p, *xb = xa + m, *xc = xb + m, *xd = xc + m;
            const int16_t *ya = xi + s * p, *yb = ya + m, *yc = yb + m, *yd = yc + m;
            int16_t *o0r = yr + 4 * s * p, *o1r = o0r + s, *o2r = o1r + s, *o3r = o2r + s;
            int16_t *o0i = yi + 4 * s * p, *o1i = o0i + s, *o2i = o1i + s, *o3i = o2i + s;
            for (int q = 0; q < s; q += 8) {
                int16x8_t ar_ = vld1q_s16(xa + q), br_ = vld1q_s16(xb + q), cr_ = vld1q_s16(xc + q), dr_ = vld1q_s16(xd + q);
                int16x8_t ai_ = vld1q_s16(ya + q), bi_ = vld1q_s16(yb + q), ci_ = vld1q_s16(yc + q), di_ = vld1q_s16(yd + q);
                __builtin_prefetch(xa + q + FFT_PLD); __builtin_prefetch(xb + q + FFT_PLD); __builtin_prefetch(xc + q + FFT_PLD); __builtin_prefetch(xd + q + FFT_PLD);
                __builtin_prefetch(ya + q + FFT_PLD); __builtin_prefetch(yb + q + FFT_PLD); __builtin_prefetch(yc + q + FFT_PLD); __builtin_prefetch(yd + q + FFT_PLD);
                R4_CORE()
                vst1q_s16(o0r + q, y0r); vst1q_s16(o1r + q, y1r); vst1q_s16(o2r + q, y2r); vst1q_s16(o3r + q, y3r);
                vst1q_s16(o0i + q, y0i); vst1q_s16(o1i + q, y1i); vst1q_s16(o2i + q, y2i); vst1q_s16(o3i + q, y3i);
            }
        }
    }
}

#ifndef FFT_FUSED
#define FFT_FUSED 0   /* 1 = Stufenpaare fusionieren (NEON); Standard 0, bis auf dem Pluto per CRC verifiziert */
#endif
/* Zwei aufeinanderfolgende Radix-4-Stufen (s, m) und (4s, m/4) über einen L1-Scratch:
   Block von P Werten p'; Stufe A liefert für p = p' + k·m/4 (k = 0..3) je einen Lauf von 4·s·P Werten in den
   Scratch, Stufe B liest die 4 Läufe (Abstand run) und schreibt z an die normale Position. Halbiert den
   Speicherverkehr gegenüber zwei vollen Durchläufen. */
static void fused_pair_neon(const fft16_t *f, int st, const int16_t *xr, const int16_t *xi, int16_t *zr, int16_t *zi, int s, int m)
{
    const int n4 = f->n / 4, m4 = m / 4;
    int P = (s <= 4) ? 8 : (4 * s * 8 <= 2048 ? 8 : 1);   /* run ≤ 2048 Werte (8 KB je Ebene) */
    if (P > m4) P = m4;
    const int run = 4 * s * P;
    int16_t *sr = f->sr, *si = f->si;
    for (int pb = 0; pb < m4; pb += P) {
        for (int k = 0; k < 4; k++) {
            int pa = pb + k * m4;
            stage_r4_neon(xr, xi, sr + k * run - 4 * s * pa, si + k * run - 4 * s * pa, s, pa, pa + P, n4,
                          f->w1r[st], f->w1i[st], f->w2r[st], f->w2i[st], f->w3r[st], f->w3i[st]);
        }
        stage_r4_neon(sr - 4 * s * pb, si - 4 * s * pb, zr, zi, 4 * s, pb, pb + P, run,
                      f->w1r[st + 1], f->w1i[st + 1], f->w2r[st + 1], f->w2i[st + 1], f->w3r[st + 1], f->w3i[st + 1]);
    }
}

static void stage_r2_out_neon(const int16_t *xr, const int16_t *xi, int16_t *out, int n, int guard)
{
    const int s = n / 2;
    int16_t *o = out + 2 * guard;
    for (int q = 0; q < s; q += 8) {
        int16x8_t ar_ = vld1q_s16(xr + q), br_ = vld1q_s16(xr + q + s), ai_ = vld1q_s16(xi + q), bi_ = vld1q_s16(xi + q + s);
        __builtin_prefetch(xr + q + FFT_PLD); __builtin_prefetch(xr + q + s + FFT_PLD); __builtin_prefetch(xi + q + FFT_PLD); __builtin_prefetch(xi + q + s + FFT_PLD);
        int16x8x2_t v0 = {{vqaddq_s16(ar_, br_), vqaddq_s16(ai_, bi_)}};
        int16x8x2_t v1 = {{vqsubq_s16(ar_, br_), vqsubq_s16(ai_, bi_)}};
        vst2q_s16(o + 2 * q, v0);
        vst2q_s16(o + 2 * (q + s), v1);
        if (q + s >= n - guard) vst2q_s16(out + 2 * (q + s - (n - guard)), v1);
    }
}
#endif

void fft16_ifft_ofdm(fft16_t *f, const int16_t *in, int16_t *out, int guard)
{
    const int16_t *xr = in, *xi = in + 1;
    int16_t *yr = f->ar, *yi = f->ai;
    int nsub = f->n, s = 1;
#if FFT16_NEON
    for (int st = 0; st < f->nst4; ) {
        int m = nsub / 4;
        if (FFT_FUSED && st + 1 < f->nst4 && m / 4 >= 1) {
            fused_pair_neon(f, st, xr, xi, yr, yi, s, m);
            st += 2; nsub /= 16; s *= 16;
        } else {
            stage_r4_neon(xr, xi, yr, yi, s, 0, m, f->n / 4, f->w1r[st], f->w1i[st], f->w2r[st], f->w2i[st], f->w3r[st], f->w3i[st]);
            st += 1; nsub /= 4; s *= 4;
        }
        xr = yr; xi = yi;
        if (yr == f->ar) { yr = f->br; yi = f->bi; } else { yr = f->ar; yi = f->ai; }
    }
    stage_r2_out_neon(xr, xi, out, f->n, guard);
#else
    for (int st = 0; st < f->nst4; st++) {
        stage_r4_c(xr, xi, st == 0 ? 2 : 1, yr, yi, nsub, s, f->w1r[st], f->w1i[st], f->w2r[st], f->w2i[st], f->w3r[st], f->w3i[st]);
        xr = yr; xi = yi;
        if (yr == f->ar) { yr = f->br; yi = f->bi; } else { yr = f->ar; yi = f->ai; }
        nsub /= 4; s *= 4;
    }
    stage_r2_out_c(xr, xi, out, f->n, guard);
#endif
}

int ofdm_window_init(ofdm_window_t *w, int W)
{
    memset(w, 0, sizeof *w);
    w->W = W;
    if (W <= 0) return 0;
    w->rise = (int16_t *)al64((size_t)W * 2);
    w->suffix = (int16_t *)al64((size_t)W * 4);
    if (!w->rise || !w->suffix) return -1;
    for (int i = 0; i < W; i++) w->rise[i] = (int16_t)lrint(32767.0 * 0.5 * (1.0 - cos(M_PI * (i + 0.5) / W)));
    memset(w->suffix, 0, (size_t)W * 4);
    return 0;
}

void ofdm_window_free(ofdm_window_t *w) { free(w->rise); free(w->suffix); memset(w, 0, sizeof *w); }

void ofdm_window_apply(ofdm_window_t *w, int16_t *sym, int guard)
{
    const int W = w->W;
    if (W <= 0) return;
    int16_t *x = sym;                    /* Anfang des CP */
    const int16_t *nx = sym + 2 * guard; /* Anfang des Nutzteils = zyklische Fortsetzung nach Symbolende */
    if (w->have_prev) {
#if FFT16_NEON
        for (int i = 0; i < W; i += 8) {
            int16x8_t r = vld1q_s16(w->rise + i);
            int16x8_t f = vsubq_s16(vdupq_n_s16(32767), r);
            int16x8x2_t a = vld2q_s16(x + 2 * i), b = vld2q_s16(w->suffix + 2 * i);
            int16x8x2_t o;
            o.val[0] = vqaddq_s16(vqrdmulhq_s16(a.val[0], r), vqrdmulhq_s16(b.val[0], f));
            o.val[1] = vqaddq_s16(vqrdmulhq_s16(a.val[1], r), vqrdmulhq_s16(b.val[1], f));
            vst2q_s16(x + 2 * i, o);
        }
#else
        for (int i = 0; i < W; i++) {
            int16_t r = w->rise[i], f = (int16_t)(32767 - r);
            x[2 * i]     = qadd(qrdmulh(x[2 * i], r),     qrdmulh(w->suffix[2 * i], f));
            x[2 * i + 1] = qadd(qrdmulh(x[2 * i + 1], r), qrdmulh(w->suffix[2 * i + 1], f));
        }
#endif
    }
    memcpy(w->suffix, nx, (size_t)W * 4);
    w->have_prev = 1;
}
