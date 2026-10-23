/*
 * rrc.c – Wurzel-Nyquist-Filter (Root Raised Cosine), Entwurf nach EN 300 429 Kap. 9 / Annex A
 * (α = 0,15) und Festkomma-Polyphasenfilter für 2 (allgemein sps) Samples/Symbol.
 */
#include "dvbmod.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

/* h[i], i = 0..ntaps-1, Symbolabstand = sps Samples, normiert auf Σh = sps (DC-Verstärkung 1 je Phase) */
int rrc_design(double alpha, int sps, int ntaps, double *h)
{
    double sum = 0;
    for (int i = 0; i < ntaps; i++) {
        double t = (i - (ntaps - 1) / 2.0) / sps;   /* in Symbolen */
        double v;
        if (fabs(t) < 1e-9) v = 1.0 - alpha + 4.0 * alpha / M_PI;
        else if (fabs(fabs(4.0 * alpha * t) - 1.0) < 1e-9)
            v = alpha / sqrt(2.0) * ((1 + 2 / M_PI) * sin(M_PI / (4 * alpha)) + (1 - 2 / M_PI) * cos(M_PI / (4 * alpha)));
        else
            v = (sin(M_PI * t * (1 - alpha)) + 4 * alpha * t * cos(M_PI * t * (1 + alpha))) /
                (M_PI * t * (1 - (4 * alpha * t) * (4 * alpha * t)));
        h[i] = v; sum += v;
    }
    for (int i = 0; i < ntaps; i++) h[i] *= sps / sum;
    return 0;
}

int rrc_init(rrc_t *r, double alpha, int sps, int ntaps)
{
    memset(r, 0, sizeof *r);
    if (ntaps % sps) return -1;
    r->ntaps = ntaps; r->sps = sps;
    double *h = (double *)malloc(ntaps * sizeof(double));
    rrc_design(alpha, sps, ntaps, h);
    double mx = 0; for (int i = 0; i < ntaps; i++) if (fabs(h[i]) > mx) mx = fabs(h[i]);
    /* Q15, größter Tap = 32767 (wie AD9361-FIR); Verstärkung = 32767/mx/32768 je Phase (ca. 0,96 bei α = 0,15) */
    r->taps = (int32_t *)malloc(ntaps * sizeof(int32_t));
    for (int i = 0; i < ntaps; i++) r->taps[i] = (int32_t)lrint(h[i] * 32767.0 / mx);
    free(h);
    int L = ntaps / sps;
    r->hist_i = (int16_t *)calloc(2 * L, 2); r->hist_q = (int16_t *)calloc(2 * L, 2);
    r->hpos = 0;
    return 0;
}

void rrc_free(rrc_t *r) { free(r->taps); free(r->hist_i); free(r->hist_q); memset(r, 0, sizeof *r); }

size_t rrc_run(rrc_t *r, const int16_t *sym, size_t n, int16_t *out)
{
    const int sps = r->sps, L = r->ntaps / sps;
    size_t no = 0;
    for (size_t s = 0; s < n; s++) {
        /* Historie doppelt führen, damit das Fenster zusammenhängend ist */
        r->hpos = (r->hpos + L - 1) % L;
        r->hist_i[r->hpos] = r->hist_i[r->hpos + L] = sym[2 * s];
        r->hist_q[r->hpos] = r->hist_q[r->hpos + L] = sym[2 * s + 1];
        const int16_t *hi = r->hist_i + r->hpos, *hq = r->hist_q + r->hpos;   /* hi[k] = x[n-k] */
        for (int p = 0; p < sps; p++) {
            int32_t ai = 0, aq = 0;
            const int32_t *t = r->taps + p;
            for (int k = 0; k < L; k++) { ai += t[k * sps] * hi[k]; aq += t[k * sps] * hq[k]; }
            ai = (ai + 16384) >> 15; aq = (aq + 16384) >> 15;
            out[2 * no] = (int16_t)(ai > 32767 ? 32767 : ai < -32768 ? -32768 : ai);
            out[2 * no + 1] = (int16_t)(aq > 32767 ? 32767 : aq < -32768 ? -32768 : aq);
            no++;
        }
    }
    return no;
}
