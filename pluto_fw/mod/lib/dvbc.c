/*
 * dvbc.c – DVB-C (EN 300 429 V1.2.1) Byte→m-Tupel, Differenzcodierung der 2 MSB (Kap. 8) und
 * QAM-Mapper nach Figur 7/8 + Tabelle 1 (Quadrant 1 → 2/3/4 durch Drehung um +π/2, +π, +3π/2).
 */
#include "dvbmod.h"
#include <string.h>
#include <math.h>

/* Quadrant-1-Punkte (I,Q ungerade, positiv) je LSB-Wort q */
static void q1_point(int qam, unsigned q, int *I, int *Q)
{
    static const int g3[8] = {1, 3, 7, 5, 15, 13, 9, 11};  /* 3-Bit-Gray (q4 q2 q0 bzw. q5 q3 q1): 000→1 001→3 011→5 010→7 110→9 111→11 101→13 100→15 */
    static const int g2[4] = {1, 3, 7, 5};                 /* 2-Bit-Gray: 00→1 01→3 11→5 10→7 */
    switch (qam) {
    case 16:  *I = 1 + 2 * (q & 1); *Q = 1 + 2 * ((q >> 1) & 1); break;
    case 32: {
        /* Figur 7: (1,1)=000 (3,1)=001 (5,1)=011 (1,3)=100 (3,3)=101 (5,3)=111 (1,5)=110 (3,5)=010 */
        static const int tI[8] = {1, 3, 3, 5, 1, 3, 1, 5}, tQ[8] = {1, 1, 5, 1, 3, 3, 5, 3};
        *I = tI[q & 7]; *Q = tQ[q & 7]; break;
    }
    case 64:  *I = g2[((q >> 2) & 1) << 1 | (q & 1)]; *Q = g2[((q >> 3) & 1) << 1 | ((q >> 1) & 1)]; break;
    case 128: {
        /* Figur 8 (128-QAM), Quadrant 1, Index q = q4..q0 */
        /* q=0..31 (q4..q0) → (I,Q), abgelesen aus Figur 8:
           00000 (1,1) 00001 (3,1) 00010 (1,3) 00011 (3,3) 00100 (7,1) 00101 (5,1) 00110 (7,3) 00111 (5,3)
           01000 (7,9) 01001 (5,9) 01010 (7,11) 01011 (5,11) 01100 (9,1) 01101 (11,1) 01110 (9,3) 01111 (11,3)
           10000 (1,7) 10001 (3,7) 10010 (1,5) 10011 (3,5) 10100 (7,7) 10101 (5,7) 10110 (7,5) 10111 (5,5)
           11000 (1,9) 11001 (3,9) 11010 (1,11) 11011 (3,11) 11100 (9,7) 11101 (11,7) 11110 (9,5) 11111 (11,5) */
        static const int tI[32] = {1, 3, 1, 3, 7, 5, 7, 5,  7, 5, 7, 5, 9, 11, 9, 11,
                                   1, 3, 1, 3, 7, 5, 7, 5,  1, 3, 1, 3, 9, 11, 9, 11};
        static const int tQ[32] = {1, 1, 3, 3, 1, 1, 3, 3,  9, 9, 11, 11, 1, 1, 3, 3,
                                   7, 7, 5, 5, 7, 7, 5, 5,  9, 9, 11, 11, 7, 7, 5, 5};
        *I = tI[q & 31]; *Q = tQ[q & 31]; break;
    }
    default: /* 256 */
        *I = g3[((q >> 4) & 1) << 2 | ((q >> 2) & 1) << 1 | (q & 1)];
        *Q = g3[((q >> 5) & 1) << 2 | ((q >> 3) & 1) << 1 | ((q >> 1) & 1)];
        break;
    }
}

double dvbc_map_rms(int qam)
{
    switch (qam) { case 16: return sqrt(10.0); case 32: return sqrt(20.0); case 64: return sqrt(42.0);
                   case 128: return sqrt(82.0); default: return sqrt(170.0); }
}

int dvbc_map_init(dvbc_map_t *m, int qam, int A)
{
    memset(m, 0, sizeof *m);
    switch (qam) { case 16: m->m = 4; break; case 32: m->m = 5; break; case 64: m->m = 6; break;
                   case 128: m->m = 7; break; case 256: m->m = 8; break; default: return -1; }
    double norm = dvbc_map_rms(qam);
    for (int w = 0; w < (1 << m->m); w++) {
        int Ik = (w >> (m->m - 1)) & 1, Qk = (w >> (m->m - 2)) & 1;
        unsigned q = (unsigned)w & ((1u << (m->m - 2)) - 1);
        int x, y; q1_point(qam, q, &x, &y);
        int I, Q;
        if (!Ik && !Qk)      { I = x;  Q = y;  }   /* Quadrant 1 */
        else if (Ik && !Qk)  { I = -y; Q = x;  }   /* Quadrant 2: +π/2 */
        else if (Ik && Qk)   { I = -x; Q = -y; }   /* Quadrant 3: +π */
        else                 { I = y;  Q = -x; }   /* Quadrant 4: +3π/2 */
        m->re[w] = (int16_t)lrint(I * A / norm); m->im[w] = (int16_t)lrint(Q * A / norm);
    }
    return 0;
}

size_t dvbc_map_bytes(dvbc_map_t *m, const uint8_t *in, size_t n, int16_t *out)
{
    size_t ns = 0; uint32_t acc = m->bitacc; int bn = m->bitn;
    const int mb = m->m; int ip = m->ip, qp = m->qp;
    for (size_t i = 0; i < n; i++) {
        acc = (acc << 8) | in[i]; bn += 8;
        while (bn >= mb) {
            unsigned w = (acc >> (bn - mb)) & ((1u << mb) - 1); bn -= mb;
            int A = (w >> (mb - 1)) & 1, B = (w >> (mb - 2)) & 1, I, Q;
            if (A == B) { I = A ^ ip; Q = B ^ qp; } else { I = A ^ qp; Q = B ^ ip; }
            ip = I; qp = Q;
            unsigned idx = (unsigned)(I << (mb - 1)) | (unsigned)(Q << (mb - 2)) | (w & ((1u << (mb - 2)) - 1));
            out[2 * ns] = m->re[idx]; out[2 * ns + 1] = m->im[idx]; ns++;
        }
        acc &= (1u << bn) - 1;
    }
    m->bitacc = acc; m->bitn = bn; m->ip = ip; m->qp = qp;
    return ns;
}
