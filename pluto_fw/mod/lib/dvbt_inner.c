/*
 * dvbt_inner.c – DVB-T innerer Interleaver (Demux, Bit-Interleaver, Symbol-Interleaver) und
 * QAM-Mapper nach EN 300 744 V1.6.1 Kap. 4.3.4 / 4.3.5 (nicht-hierarchisch).
 */
#include "dvbmod.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>
#if defined(__ARM_NEON) || defined(__ARM_NEON__)
#include <arm_neon.h>
#define INNER_NEON 1
#else
#define INNER_NEON 0
#endif

/* Tabellen 3a/3b: R'_i-Bitposition → R_i-Bitposition */
static const uint8_t bitperm_2k[10] = {4, 3, 9, 6, 2, 8, 1, 5, 7, 0};
static const uint8_t bitperm_8k[12] = {7, 1, 4, 2, 9, 6, 8, 10, 0, 3, 11, 5};

static void gen_H(dvbt_inner_t *in)
{
    const int Nr = (in->mode == DVBT_8K) ? 13 : 11;
    const int Mmax = in->nfft, Nmax = in->ncells;
    const uint8_t *perm = (in->mode == DVBT_8K) ? bitperm_8k : bitperm_2k;
    const int nb = Nr - 1;                       /* Breite von R'_i */
    unsigned rp = 0;                             /* R'_i */
    int q = 0;
    for (int i = 0; i < Mmax && q < Nmax; i++) {
        if (i == 0 || i == 1) rp = 0;
        else if (i == 2) rp = 1;
        else {
            unsigned nb_bit;
            if (in->mode == DVBT_8K) nb_bit = (rp ^ (rp >> 1) ^ (rp >> 4) ^ (rp >> 6)) & 1;
            else                     nb_bit = (rp ^ (rp >> 3)) & 1;
            rp = ((rp >> 1) | (nb_bit << (nb - 1))) & ((1u << nb) - 1);
        }
        unsigned R = 0;
        for (int k = 0; k < nb; k++) R |= ((rp >> k) & 1) << perm[k];
        unsigned H = ((unsigned)(i & 1) << nb) + R;
        if ((int)H < Nmax) in->H[q++] = (uint16_t)H;
    }
}

int dvbt_inner_init(dvbt_inner_t *in, int mode, int constellation, int A)
{
    memset(in, 0, sizeof *in);
    in->mode = mode;
    in->v = (constellation == DVBT_QPSK) ? 2 : (constellation == DVBT_16QAM) ? 4 : 6;
    in->ncells = (mode == DVBT_8K) ? 6048 : 1512;
    in->nfft = (mode == DVBT_8K) ? 8192 : 2048;
    in->Kmax = (mode == DVBT_8K) ? 6816 : 1704;
    in->K = in->Kmax + 1;
    in->A = A;
    in->H = (uint16_t *)malloc(in->ncells * sizeof(uint16_t));
    if (!in->H) return -1;
    gen_H(in);
    static const uint8_t he[6] = {0, 63, 105, 42, 21, 84};
    memcpy(in->he_off, he, 6);
    /* Demultiplexer (Kap. 4.3.4.1): x_i → b_e,0 */
    if (in->v == 2)      { in->demux_pos[0] = 0; in->demux_pos[1] = 1; }
    else if (in->v == 4) { in->demux_pos[0] = 0; in->demux_pos[1] = 2; in->demux_pos[2] = 1; in->demux_pos[3] = 3; }
    else { const uint8_t d[6] = {0, 3, 1, 4, 2, 5}; memcpy(in->demux_pos, d, 6); }
    /* Mapper (Figur 9, Gray, nicht-hierarchisch): I aus y0,y2,y4; Q aus y1,y3,y5; y0 = MSB */
    double norm = (in->v == 2) ? sqrt(2.0) : (in->v == 4) ? sqrt(10.0) : sqrt(42.0);
    for (int w = 0; w < (1 << in->v); w++) {
        int y[6] = {0};
        for (int b = 0; b < in->v; b++) y[b] = (w >> (in->v - 1 - b)) & 1;
        int I, Q;
        if (in->v == 2)      { I = 1; Q = 1; }
        else if (in->v == 4) { I = 3 - 2 * y[2]; Q = 3 - 2 * y[3]; }
        else {
            static const int g[4] = {7, 5, 1, 3};   /* Index (y2<<1)|y4: 00→7 01→5 10→1 11→3 */
            I = g[(y[2] << 1) | y[4]]; Q = g[(y[3] << 1) | y[5]];
        }
        if (y[0]) I = -I;
        if (y[1]) Q = -Q;
        in->map_re[w] = (int16_t)lrint(I * A / norm);
        in->map_im[w] = (int16_t)lrint(Q * A / norm);
        in->map_iq[w] = (uint16_t)in->map_re[w] | ((uint32_t)(uint16_t)in->map_im[w] << 16);
    }
    return 0;
}

void dvbt_inner_free(dvbt_inner_t *in) { free(in->H); in->H = NULL; }

/* x-Wörter (v Bit, MSB zuerst) aus dem Bitstrom extrahieren */
static inline void extract_xwords(const uint8_t *bits, uint8_t *xw, int n, int v)
{
    if (v == 6) {
#if INNER_NEON
        for (int i = 0; i < n; i += 32, bits += 24) {   /* 24 Bytes → 32 Wörter */
            uint8x8x3_t b = vld3_u8(bits);
            uint8x8x4_t o;
            o.val[0] = vshr_n_u8(b.val[0], 2);
            o.val[1] = vorr_u8(vshl_n_u8(vand_u8(b.val[0], vdup_n_u8(3)), 4), vshr_n_u8(b.val[1], 4));
            o.val[2] = vorr_u8(vshl_n_u8(vand_u8(b.val[1], vdup_n_u8(15)), 2), vshr_n_u8(b.val[2], 6));
            o.val[3] = vand_u8(b.val[2], vdup_n_u8(63));
            vst4_u8(xw + i, o);
        }
        return;
#endif
        for (int i = 0; i < n; i += 4, bits += 3) {
            unsigned b0 = bits[0], b1 = bits[1], b2 = bits[2];
            xw[i]     = (uint8_t)(b0 >> 2);
            xw[i + 1] = (uint8_t)(((b0 & 3) << 4) | (b1 >> 4));
            xw[i + 2] = (uint8_t)(((b1 & 15) << 2) | (b2 >> 6));
            xw[i + 3] = (uint8_t)(b2 & 63);
        }
    } else if (v == 4) {
        for (int i = 0; i < n; i += 2, bits++) { xw[i] = (uint8_t)(bits[0] >> 4); xw[i + 1] = (uint8_t)(bits[0] & 15); }
    } else {
        for (int i = 0; i < n; i += 4, bits++) {
            unsigned b = bits[0];
            xw[i] = (uint8_t)(b >> 6); xw[i + 1] = (uint8_t)((b >> 4) & 3); xw[i + 2] = (uint8_t)((b >> 2) & 3); xw[i + 3] = (uint8_t)(b & 3);
        }
    }
}

#define BI_BLOCK 126

void dvbt_bit_interleave(const dvbt_inner_t *in, const uint8_t *bits, uint8_t *yp)
{
    const int v = in->v, n = in->ncells;
    uint8_t xw[6048];
    uint8_t d[2 * BI_BLOCK];
    extract_xwords(bits, xw, n, v);
    const int s0 = v - 1 - in->demux_pos[0], s1 = v - 1 - in->demux_pos[1];
    const int s2 = v - 1 - in->demux_pos[2], s3 = v - 1 - in->demux_pos[3];
    const int s4 = v - 1 - in->demux_pos[4], s5 = v - 1 - in->demux_pos[5]; (void)s4; (void)s5;
    for (int blk = 0; blk < n; blk += BI_BLOCK) {
        memcpy(d, xw + blk, BI_BLOCK);
        memcpy(d + BI_BLOCK, xw + blk, BI_BLOCK);
        const uint8_t *p0 = d, *p1 = d + 63, *p2 = d + 105, *p3 = d + 42, *p4 = d + 21, *p5 = d + 84;
        uint8_t *o = yp + blk;
        if (v == 6) {
            /* Demux 64-QAM: b0←x0 b1←x3 b2←x1 b3←x4 b4←x2 b5←x5 (x0 = MSB = Bit 5 des x-Worts)
               y'-Bit5 = x-Bit5 von p0, Bit4 = x-Bit2 von p1, Bit3 = x-Bit4 von p2, Bit2 = x-Bit1 von p3, Bit1 = x-Bit3 von p4, Bit0 = x-Bit0 von p5 */
#if INNER_NEON
            for (int w = 0; w < BI_BLOCK - 6; w += 8) {
                uint8x8_t r = vand_u8(vld1_u8(p0 + w), vdup_n_u8(0x20));
                r = vorr_u8(r, vshl_n_u8(vand_u8(vld1_u8(p1 + w), vdup_n_u8(0x04)), 2));
                r = vorr_u8(r, vshr_n_u8(vand_u8(vld1_u8(p2 + w), vdup_n_u8(0x10)), 1));
                r = vorr_u8(r, vshl_n_u8(vand_u8(vld1_u8(p3 + w), vdup_n_u8(0x02)), 1));
                r = vorr_u8(r, vshr_n_u8(vand_u8(vld1_u8(p4 + w), vdup_n_u8(0x08)), 2));
                r = vorr_u8(r, vand_u8(vld1_u8(p5 + w), vdup_n_u8(0x01)));
                vst1_u8(o + w, r);
            }
            for (int w = BI_BLOCK - 6; w < BI_BLOCK; w++)
#else
            for (int w = 0; w < BI_BLOCK; w++)
#endif
                o[w] = (uint8_t)((p0[w] & 0x20) | ((p1[w] & 0x04) << 2) | ((p2[w] & 0x10) >> 1) |
                                 ((p3[w] & 0x02) << 1) | ((p4[w] & 0x08) >> 2) | (p5[w] & 0x01));
        } else if (v == 4) {
            for (int w = 0; w < BI_BLOCK; w++)
                o[w] = (uint8_t)((((p0[w] >> s0) & 1) << 3) | (((p1[w] >> s1) & 1) << 2) |
                                 (((p2[w] >> s2) & 1) << 1) | ((p3[w] >> s3) & 1));
        } else {
            for (int w = 0; w < BI_BLOCK; w++)
                o[w] = (uint8_t)((((p0[w] >> s0) & 1) << 1) | ((p1[w] >> s1) & 1));
        }
    }
}

void dvbt_symbol_interleave(const dvbt_inner_t *in, const uint8_t *yp, uint8_t *y, int odd)
{
    const uint16_t *H = in->H; const int n = in->ncells;
    if (!odd) for (int q = 0; q < n; q++) y[H[q]] = yp[q];
    else      for (int q = 0; q < n; q++) y[q] = yp[H[q]];
}
