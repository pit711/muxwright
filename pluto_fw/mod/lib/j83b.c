/*
 * j83b.c – ITU-T J.83 Annex B (SCTE 07) Kanalcodierung, eigene Implementierung nach Rec. ITU-T J.83 (12/2007):
 *   B.4   MPEG-Framing: Prüfsumme über die 187 Nutzbytes (Paritätsmatrix P aus dem Vektor C, Figur B.4/B.5), Syndrom
 *         eines gültigen Pakets = 47h; die Prüfsumme folgt den 187 Bytes (steht an der Stelle des nächsten Sync-Bytes)
 *   B.5.1 RS(128,122) über GF(128), p(x) = x^7 + x^3 + 1, Wurzeln α^1..α^5, erweitertes Symbol c_ = c(α^6)
 *   B.5.2 Faltungsinterleaver, Zweig i mit i·J Zellen, Kommutator am Rahmenanfang auf Zweig 0
 *   B.5.3 Rahmen: 60 RS-Blöcke + 42-Bit-Trailer (64-QAM) bzw. 88 + 40 Bit (256-QAM), Steuerwort im Trailer
 *   B.5.4 Randomizer f(x) = x^3 + x + α^3 über GF(128), je Rahmen mit „alles Eins“ geladen, Trailer unverwürfelt
 *   B.5.5 Trellis: Präcoder (Figur B.16), BCC G1 = 25, G2 = 37 (oktal), Punktierung [0001;1111], Bitzuordnung nach
 *         Figur B.13/B.15, Konstellation nach Figur B.18/B.19 (aus der Norm-PDF ausgelesen, deckungsgleich mit gr-dtv)
 * Lizenz: MIT (siehe LICENSE). Kein Code aus gr-dtv übernommen; gr-dtv dient nur als Prüfreferenz (tests/j83b_*).
 */
#include "j83b.h"
#include "dvbmod.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

/* Konstellation: Label (C5..C0 bzw. C7..C0) → I/Q (ungerade Stufen) */
static const int8_t j83b_i64[64] = {1,1,1,-3,-3,1,-3,-3,-1,-1,3,-1,-1,3,3,3,5,1,1,-7,-3,5,-7,-3,-1,-5,7,-1,-5,3,3,7,1,5,5,-3,-7,1,-3,-7,-5,-1,3,-5,-1,7,7,3,5,5,5,-7,-7,5,-7,-7,-5,-5,7,-5,-5,7,7,7};
static const int8_t j83b_q64[64] = {1,-1,-3,-1,1,3,-3,3,1,-1,1,3,-3,-1,-3,3,1,-5,-7,-1,5,3,-3,7,5,-1,1,7,-3,-5,-7,3,5,-1,-3,-5,1,7,-7,3,1,-5,5,3,-7,-1,-3,7,5,-5,-7,-5,5,7,-7,7,5,-5,5,7,-7,-5,-7,7};
static const int8_t j83b_i256[256] = {1,1,-3,1,1,5,-7,1,1,9,-11,1,1,13,-15,1,-1,-1,-1,3,-5,-1,-1,7,-9,-1,-1,11,-13,-1,-1,15,1,-3,-3,-3,5,-3,-3,-7,9,-3,-3,-11,13,-3,-3,-15,3,-1,3,3,3,-5,7,3,3,-9,11,3,3,-13,15,3,5,1,-3,5,5,5,-7,5,5,9,-11,5,5,13,-15,5,-1,-5,-5,3,-5,-5,-5,7,-9,-5,-5,11,-13,-5,-5,15,1,-7,-7,-3,5,-7,-7,-7,9,-7,-7,-11,13,-7,-7,-15,7,-1,3,7,7,-5,7,7,7,-9,11,7,7,-13,15,7,9,1,-3,9,9,5,-7,9,9,9,-11,9,9,13,-15,9,-1,-9,-9,3,-5,-9,-9,7,-9,-9,-9,11,-13,-9,-9,15,1,-11,-11,-3,5,-11,-11,-7,9,-11,-11,-11,13,-11,-11,-15,11,-1,3,11,11,-5,7,11,11,-9,11,11,11,-13,15,11,13,1,-3,13,13,5,-7,13,13,9,-11,13,13,13,-15,13,-1,-13,-13,3,-5,-13,-13,7,-9,-13,-13,11,-13,-13,-13,15,1,-15,-15,-3,5,-15,-15,-7,9,-15,-15,-11,13,-15,-15,-15,15,-1,3,15,15,-5,7,15,15,-9,11,15,15,-13,15,15};
static const int8_t j83b_q256[256] = {1,-1,1,3,5,-1,1,7,9,-1,1,11,13,-1,1,15,1,-1,-3,-1,1,-5,-7,-1,1,-9,-11,-1,1,-13,-15,-1,-3,-1,-3,3,-3,-5,-7,3,-3,-9,-11,3,-3,-13,-15,3,1,3,-3,3,5,3,-3,7,9,3,-3,11,13,3,-3,15,1,-5,5,3,5,-5,5,7,9,-5,5,11,13,-5,5,15,5,-1,-3,-5,5,-5,-7,-5,5,-9,-11,-5,5,-13,-15,-5,-7,-1,-3,7,-7,-5,-7,7,-7,-9,-11,7,-7,-13,-15,7,1,7,-7,3,5,7,-7,7,9,7,-7,11,13,7,-7,15,1,-9,9,3,5,-9,9,7,9,-9,9,11,13,-9,9,15,9,-1,-3,-9,9,-5,-7,-9,9,-9,-11,-9,9,-13,-15,-9,-11,-1,-3,11,-11,-5,-7,11,-11,-9,-11,11,-11,-13,-15,11,1,11,-11,3,5,11,-11,7,9,11,-11,11,13,11,-11,15,1,-13,13,3,5,-13,13,7,9,-13,13,11,13,-13,13,15,13,-1,-3,-13,13,-5,-7,-13,13,-9,-11,-13,13,-13,-15,-13,-15,-1,-3,15,-15,-5,-7,15,-15,-9,-11,15,-15,-13,-15,15,1,15,-15,3,5,15,-15,7,9,15,-15,11,13,15,-15,15};

/* Vektor C (1497 Bit) aus Figur B.4: 93 16-Bit-Wörter + 8 Bit, dann eine binäre 1 */
static const char *C_HEX =
    "b0f3 857f 97a5 0ddb eba0 caa3 58c1 2da9 a7ee 67b2 1039 2627 5688 a47c 05c7 78b3 61e7 0aff 2f4a 1bb7 d741 9546 "
    "b182 5b53 4fdc cf64 2072 4c4e ad11 48f8 0b8e f166 c3ce 15fe 5e94 376f ae83 2a8d 6304 b6a6 9fb9 9ec8 40e4 989d "
    "5a22 91f0 171d e2cd 879c 2bfc bd28 6edf 5d06 551a c609 6d4d 3f73 3d90 81c9 313a b445 23e0 2e3b c59b 0f38 57f9 "
    "7a50 ddbe ba0c aa35 8c12 da9a 7ee6 7b21 0392 6275 688a 47c0 5c77 8b36 1e70 aff2 f4a1 bb7d 7419 546b 1825 b534 "
    "fdcc f642 0724 c4ea d114 8f";

static uint8_t gexp[254], glog[128];
static uint8_t gmul(uint8_t a, uint8_t b) { return (a && b) ? gexp[glog[a] + glog[b]] : 0; }

static const int cw_I[16] = {128, 128, 128, 64, 128, 32, 128, 16, 128, 8, 128, 0, 128, 0, 128, 0};
static const int cw_J[16] = {1, 1, 2, 2, 3, 4, 4, 8, 5, 16, 6, 0, 7, 0, 8, 0};

int j83b_cw_valid(int cw, int *I, int *J)
{
    if (cw < 0 || cw > 15 || !cw_I[cw]) return 0;
    if (I) *I = cw_I[cw];
    if (J) *J = cw_J[cw];
    return 1;
}

double j83b_ts_rate(int qam, double sr)
{
    /* Nutzbits je Rahmen / QAM-Symbole je Rahmen: 64-QAM 60·122·7 / (53802·5/28) = 16/3, 256-QAM 88·122·7 / (2076·5) */
    return qam == 64 ? sr * 60.0 * 122 * 7 * 28 / (53802.0 * 5) : sr * 88.0 * 122 * 7 / (2076.0 * 5);
}

/* RS-Generator g(x) = Π (x + α^i), i = 1..5 */
static uint32_t rsp[128];                 /* g3·fb<<24 | g2·fb<<16 | g1·fb<<8 | g0·fb (r0 im untersten Byte) */
static uint8_t gmt[5][128], a6[128];

static void tables_init(void)
{
    static int done;
    if (done) return;
    int x = 1;
    for (int i = 0; i < 127; i++) { gexp[i] = gexp[i + 127] = (uint8_t)x; glog[x] = (uint8_t)i; x <<= 1; if (x & 0x80) x ^= 0x89; }
    uint8_t g[6] = {1};
    int n = 1;
    for (int i = 1; i <= 5; i++) {
        uint8_t r[6] = {0};
        for (int j = 0; j < n; j++) { r[j] ^= g[j]; r[j + 1] ^= gmul(g[j], gexp[i]); }
        n++; memcpy(g, r, sizeof g);
    }
    for (int k = 0; k < 5; k++) for (int v = 0; v < 128; v++) gmt[k][v] = gmul((uint8_t)v, g[k + 1]);
    for (int v = 0; v < 128; v++) a6[v] = gmul((uint8_t)v, gexp[6]);
    for (int v = 0; v < 128; v++) rsp[v] = gmt[0][v] | (uint32_t)gmt[1][v] << 8 | (uint32_t)gmt[2][v] << 16 | (uint32_t)gmt[3][v] << 24;
    done = 1;
}

static void rs_encode(const uint8_t *m, uint8_t *out)
{
    uint32_t R = 0;                               /* r0 | r1<<8 | r2<<16 | r3<<24 */
    uint8_t r4 = 0, ev = 0;
    for (int i = 0; i < 122; i++) {
        uint8_t fb = m[i] ^ (uint8_t)R;
        R = ((R >> 8) | (uint32_t)r4 << 24) ^ rsp[fb]; r4 = gmt[4][fb];
        ev = a6[ev] ^ m[i];
        out[i] = m[i];
    }
    uint8_t r0 = (uint8_t)R, r1 = (uint8_t)(R >> 8), r2 = (uint8_t)(R >> 16), r3 = (uint8_t)(R >> 24);
    out[122] = r0; out[123] = r1; out[124] = r2; out[125] = r3; out[126] = r4;
    ev = a6[ev] ^ r0; ev = a6[ev] ^ r1; ev = a6[ev] ^ r2; ev = a6[ev] ^ r3; ev = a6[ev] ^ r4;
    out[127] = ev;                                      /* c_ = c(α^6) (Horner über alle 127 Symbole) */
}

/* Bitreihenfolgen der 256-QAM-Trellisgruppen (Figur B.15): A_i → i, B_i → 32 + i, S_i → 64 + i */
static const uint8_t ord256[38] = {0, 32, 1, 2, 3, 33, 34, 35, 4, 36, 5, 6, 7, 37, 38, 39, 8, 40, 9, 10, 11, 41, 42, 43,
                                   12, 44, 13, 14, 15, 45, 46, 47, 16, 17, 18, 48, 49, 50};
static const uint8_t sync256[38] = {64, 65, 1, 2, 3, 33, 34, 35, 66, 67, 5, 6, 7, 37, 38, 39, 68, 69, 9, 10, 11, 41, 42, 43,
                                    70, 71, 13, 14, 15, 45, 46, 47, 16, 17, 18, 48, 49, 50};
/* 64-QAM (Figur B.13): erstes Symbol A10 A8 A7 A5 A4 A2 A1, zweites A9 A6 A3 A0 A13 A12 A11 (MSB → LSB) */
static const uint8_t ord64a[7] = {10, 8, 7, 5, 4, 2, 1}, ord64b[7] = {9, 6, 3, 0, 13, 12, 11};

int j83b_init(j83b_enc_t *e, int qam, int cw, int A)
{
    memset(e, 0, sizeof *e);
    if (qam != 64 && qam != 256) return -1;
    if (!j83b_cw_valid(cw, &e->I, &e->J)) return -1;
    tables_init();
    e->qam = qam; e->m = qam == 64 ? 6 : 8; e->cw = cw;
    e->nblk = qam == 64 ? 60 : 88; e->N = e->nblk * 128;

    /* Prüfsumme: Zeile i der Matrix P als 8-Bit-Wert (Spalte 0 = MSB), P[i][k] = C[i-k] */
    uint8_t cvec[1497];
    int nb = 0;
    for (const char *h = C_HEX; *h; h++) {
        if (*h == ' ') continue;
        int v = (*h <= '9') ? *h - '0' : *h - 'a' + 10;
        for (int b = 3; b >= 0; b--) cvec[nb++] = (uint8_t)((v >> b) & 1);
    }
    if (nb != 1496) return -1;
    cvec[1496] = 1;
    uint8_t prow[1504];
    for (int i = 0; i < 1504; i++) {
        uint8_t r = 0;
        for (int k = 0; k < 8; k++) if (i - k >= 0 && i - k < 1497 && cvec[i - k]) r |= (uint8_t)(0x80 >> k);
        prow[i] = r;
    }
    for (int j = 0; j < 374; j++)                /* Halbbyte j (MSB-Hälfte zuerst) → Syndrombeitrag; 6 KB, passt in L1 */
        for (int v = 0; v < 16; v++) {
            uint8_t s = 0;
            for (int b = 0; b < 4; b++) if (v & (8 >> b)) s ^= prow[4 * j + b];
            e->chk_nib[j][v] = s;
        }
    for (int w = 0; w < 256; w++) {   /* Dreieckssystem: Prüfbit j wirkt nur auf Syndrombits 0..j (MSB zuerst), C[1496] = 1 */
        int want = w, c = 0;
        for (int j = 0; j < 8; j++)
            if (want & (0x80 >> j)) { c |= 0x80 >> j; want ^= prow[1496 + j]; }
        e->chk_fix[w] = (uint8_t)c;
    }

    /* Interleaver (Zweig 0 ohne Speicher) */
    /* Interleaver als Ring aus D = (I-1)·J + 1 Kommutatorzyklen à I Symbole: Zweig b liest den Zyklus t − b·J */
    e->ilD = (e->I - 1) * e->J + 1; e->ilrow = 0;
    e->ilmem = (uint8_t *)calloc((size_t)e->ilD * e->I, 1);
    if (!e->ilmem) return -1;

    /* Randomizer-Folge eines Rahmens (Figur B.11): r0 → (+) → r1 → r2, Ausgang r2, r0' = α^3·r2, r1' = r0 ^ r2 */
    {
        uint8_t r0 = 0x7f, r1 = 0x7f, r2 = 0x7f, a3 = gexp[3];
        for (int k = 0; k < e->N; k++) { e->rnd[k] = r2; uint8_t n0 = gmul(a3, r2), n1 = r0 ^ r2; r2 = r1; r1 = n1; r0 = n0; }
    }

    /* Trailer: 64-QAM 6 Symbole à 7 Bit (75 2C 0D 6C, Steuerwort + 10 Nullbits), 256-QAM 5 Bytes (71 E8 4D D4, CW, 0000) */
    if (qam == 64) { const uint8_t t[6] = {0x75, 0x2c, 0x0d, 0x6c, (uint8_t)(cw << 3), 0}; memcpy(e->trail, t, 6); }
    else { const uint8_t t[5] = {0x71, 0xe8, 0x4d, 0xd4, (uint8_t)(cw << 4)}; memcpy(e->trail, t, 5); }

    /* Präcoder über 4 Takte: X = W ^ X' ^ Z(X' ^ Y'), Y = Z ^ W ^ Y' ^ Z(X' ^ Y'); Bit 0 = zeitlich erstes */
    for (int st = 0; st < 4; st++)
        for (int W = 0; W < 16; W++)
            for (int Z = 0; Z < 16; Z++) {
                int px = st & 1, py = st >> 1, X = 0, Y = 0;
                for (int k = 0; k < 4; k++) {
                    int w = (W >> k) & 1, z = (Z >> k) & 1, a = px ^ py;
                    int x = w ^ px ^ (z & a), y = z ^ w ^ py ^ (z & a);
                    X |= x << k; Y |= y << k; px = x; py = y;
                }
                e->pre[st][W][Z] = (uint16_t)(X | Y << 4 | (px | py << 1) << 8);
            }
    /* BCC: Zustand = letzte 4 Eingangsbits (Bit 0 = jüngstes); 4 Bits → G2, G2, G2, G1, G2 (= U1..U5) */
    for (int st = 0; st < 16; st++)
        for (int in = 0; in < 16; in++) {
            int s = st, o = 0, n = 0;
            for (int k = 0; k < 4; k++) {
                int b = (in >> k) & 1, d1 = s & 1, d2 = (s >> 1) & 1, d3 = (s >> 2) & 1, d4 = (s >> 3) & 1;
                int g1 = b ^ d2 ^ d4, g2 = b ^ d1 ^ d2 ^ d3 ^ d4;
                if (k == 3) o |= g1 << n++;
                o |= g2 << n++;
                s = ((s << 1) | b) & 15;
            }
            e->bcc[st][in] = (uint16_t)(o | s << 5);
        }
    for (int v = 0; v < 128; v++)
        for (int b = 0; b < 7; b++)
            if (v & (0x40 >> b)) { e->a64[0][v] |= 1u << ord64a[b]; e->a64[1][v] |= 1u << ord64b[b]; }
    for (int k = 0; k < 5; k++)
        for (int v = 0; v < 256; v++) {
            uint64_t w = 0;
            int nbits = k < 4 ? 8 : 6;
            if (v >> nbits) continue;
            for (int b = 0; b < nbits; b++)
                if (v & (1 << (nbits - 1 - b))) {
                    int c = ord256[8 * k + b];
                    w |= (c < 32) ? (uint64_t)1 << c : (uint64_t)1 << (19 + c - 32);
                }
            e->a256[k][v] = w;
        }

    /* Mapper (Figur B.18/B.19), Skalierung wie dvbc_map_init */
    double norm = dvbc_map_rms(qam);
    for (int w = 0; w < qam; w++) {
        int I = qam == 64 ? j83b_i64[w] : j83b_i256[w], Q = qam == 64 ? j83b_q64[w] : j83b_q256[w];
        e->re[w] = (int16_t)lrint(I * A / norm); e->im[w] = (int16_t)lrint(Q * A / norm);
        int16_t pr[2] = {e->re[w], e->im[w]};
        memcpy(&e->iq32[w], pr, 4);                /* I/Q-Paar als ein 32-Bit-Wort (Speicherreihenfolge wie int16[2]) */
    }
    return 0;
}

void j83b_free(j83b_enc_t *e) { free(e->ilmem); e->ilmem = NULL; }

/* Laufzeitpfad: alles blockweise (ein RS-Block = 128 Symbole) mit lokalen Kopien des Zustands, damit der Compiler ihn in
   Registern hält (uint8_t-Speicherzugriffe dürfen sonst alles aliasen). Ein Rahmen besteht aus ganzen RS-Blöcken, der
   Kommutator steht an jeder Blockgrenze auf Zweig 0 (I teilt 128). */
typedef struct { int pst, su, sv; } tstate_t;

static inline void put5(int16_t *o, const j83b_enc_t *e, unsigned a, unsigned b, unsigned c, unsigned d, unsigned f)
{
    memcpy(o, &e->iq32[a], 4); memcpy(o + 2, &e->iq32[b], 4); memcpy(o + 4, &e->iq32[c], 4);
    memcpy(o + 6, &e->iq32[d], 4); memcpy(o + 8, &e->iq32[f], 4);
}

/* eine Trellisgruppe aus den Bitwörtern A (Bit i = A_i) und B → 5 QAM-Symbole (als re | im<<16) */
static inline void grp64(const j83b_enc_t *e, tstate_t *t, uint32_t A, uint32_t B, int16_t *out, uint8_t *lab)
{
    int W = (int)((A & 1) | ((A >> 2) & 2) | ((A >> 4) & 4) | ((A >> 6) & 8));     /* A0 A3 A6 A9 */
    int Z = (int)((B & 1) | ((B >> 2) & 2) | ((B >> 4) & 4) | ((B >> 6) & 8));
    unsigned p = e->pre[t->pst][W][Z];
    t->pst = (int)(p >> 8);
    unsigned u = e->bcc[t->su][p & 15], v = e->bcc[t->sv][(p >> 4) & 15];
    t->su = (int)(u >> 5); t->sv = (int)(v >> 5);
    u <<= 3; v <<= 0;
    unsigned L0 = (A << 3 & 0x30) | (u & 0x08) | (B >> 0 & 0x06) | (v & 1);            /* C5 C4 = A2 A1, C2 C1 = B2 B1 */
    unsigned L1 = (A & 0x30) | (u >> 1 & 0x08) | (B >> 3 & 0x06) | (v >> 1 & 1);       /* A5 A4 / B5 B4 */
    unsigned L2 = (A >> 3 & 0x30) | (u >> 2 & 0x08) | (B >> 6 & 0x06) | (v >> 2 & 1);  /* A8 A7 / B8 B7 */
    unsigned L3 = (A >> 6 & 0x30) | (u >> 3 & 0x08) | (B >> 9 & 0x06) | (v >> 3 & 1);  /* A11 A10 / B11 B10 */
    unsigned L4 = (A >> 8 & 0x30) | (u >> 4 & 0x08) | (B >> 11 & 0x06) | (v >> 4 & 1); /* A13 A12 / B13 B12 */
    put5(out, e, L0, L1, L2, L3, L4);
    if (lab) { lab[0] = (uint8_t)L0; lab[1] = (uint8_t)L1; lab[2] = (uint8_t)L2; lab[3] = (uint8_t)L3; lab[4] = (uint8_t)L4; }
}

static inline void grp256(const j83b_enc_t *e, tstate_t *t, uint32_t A, uint32_t B, int16_t *out, uint8_t *lab)
{
    int W = (int)((A & 1) | ((A >> 3) & 2) | ((A >> 6) & 4) | ((A >> 9) & 8));     /* A0 A4 A8 A12 */
    int Z = (int)((B & 1) | ((B >> 3) & 2) | ((B >> 6) & 4) | ((B >> 9) & 8));
    unsigned p = e->pre[t->pst][W][Z];
    t->pst = (int)(p >> 8);
    unsigned u = e->bcc[t->su][p & 15], v = e->bcc[t->sv][(p >> 4) & 15];
    t->su = (int)(u >> 5); t->sv = (int)(v >> 5);
    u <<= 4;
    unsigned L0 = (A << 4 & 0xe0) | (u & 0x10) | (B & 0x0e) | (v & 1);                 /* C7 C6 C5 = A3 A2 A1 */
    unsigned L1 = (A & 0xe0) | (u >> 1 & 0x10) | (B >> 4 & 0x0e) | (v >> 1 & 1);       /* A7 A6 A5 */
    unsigned L2 = (A >> 4 & 0xe0) | (u >> 2 & 0x10) | (B >> 8 & 0x0e) | (v >> 2 & 1);  /* A11 A10 A9 */
    unsigned L3 = (A >> 8 & 0xe0) | (u >> 3 & 0x10) | (B >> 12 & 0x0e) | (v >> 3 & 1); /* A15 A14 A13 */
    unsigned L4 = (A >> 11 & 0xe0) | (u >> 4 & 0x10) | (B >> 15 & 0x0e) | (v >> 4 & 1);/* A18 A17 A16 */
    put5(out, e, L0, L1, L2, L3, L4);
    if (lab) { lab[0] = (uint8_t)L0; lab[1] = (uint8_t)L1; lab[2] = (uint8_t)L2; lab[3] = (uint8_t)L3; lab[4] = (uint8_t)L4; }
}

/* 64-QAM: 7-Bit-Symbole (verwürfelte Daten oder Trailer), je 4 eine Trellisgruppe */
static size_t trellis64(j83b_enc_t *e, tstate_t *t, const uint8_t *s, int ns, int16_t *out, uint8_t *lab)
{
    size_t n = 0;
    int g4n = e->g4n;
    uint8_t g0 = e->g4[0], g1 = e->g4[1], g2 = e->g4[2];
    for (int k = 0; k < ns; k++) {
        uint8_t c = s[k];
        if (g4n == 0) { g0 = c; g4n = 1; continue; }
        if (g4n == 1) { g1 = c; g4n = 2; continue; }
        if (g4n == 2) { g2 = c; g4n = 3; continue; }
        g4n = 0;
        uint32_t A = e->a64[0][g0] | e->a64[1][g1], B = e->a64[0][g2] | e->a64[1][c];
        grp64(e, t, A, B, out + 2 * n, lab ? lab + n : NULL);
        n += 5;
    }
    e->g4n = g4n; e->g4[0] = g0; e->g4[1] = g1; e->g4[2] = g2;
    return n;
}

/* 256-QAM: Datenbits sammeln; Gruppen 0..2070 mit 38 Bit, 2071..2075 mit 30 Bit + 8 Sync-Bits (Trailer-Byte) */
static size_t trellis256(j83b_enc_t *e, tstate_t *t, const uint8_t *s, int ns, int16_t *out, uint8_t *lab)
{
    size_t n = 0;
    uint64_t acc = e->tacc;
    int tn = e->tn, grp = e->grp;
    for (int k = 0; k < ns; k++) {
        acc = (acc << 7) | s[k]; tn += 7;
        if (grp < 2071) {
            if (tn < 38) continue;
            tn -= 38;
            uint64_t w = acc >> tn;                    /* obere Bits sind bereits ausgeblendet (acc < 2^(tn+38)) */
            acc &= ((uint64_t)1 << tn) - 1;
            uint32_t hi = (uint32_t)(w >> 6), lo = (uint32_t)w & 63;
            uint64_t ab = e->a256[0][hi >> 24] | e->a256[1][(hi >> 16) & 255] | e->a256[2][(hi >> 8) & 255] |
                          e->a256[3][hi & 255] | e->a256[4][lo];
            grp256(e, t, (uint32_t)ab & 0x7ffff, (uint32_t)(ab >> 19), out + 2 * n, lab ? lab + n : NULL);
            n += 5;
            grp++;
        } else {
            if (tn < 30) continue;
            tn -= 30;
            uint32_t w = (uint32_t)(acc >> tn);
            acc &= ((uint64_t)1 << tn) - 1;
            uint8_t sb = e->trail[grp - 2071];
            uint32_t A = 0, B = 0;
            int di = 29;
            for (int p = 0; p < 38; p++) {
                int c = sync256[p], bit;
                if (c >= 64) { int si = c - 64; bit = (sb >> (7 - si)) & 1; c = (si & 1) ? 32 + (si >> 1) * 4 : (si >> 1) * 4; }
                else bit = (int)((w >> di--) & 1);
                if (c < 32) A |= (uint32_t)bit << c; else B |= (uint32_t)bit << (c - 32);
            }
            grp256(e, t, A, B, out + 2 * n, lab ? lab + n : NULL);
            n += 5;
            if (++grp == 2076) grp = 0;
        }
    }
    e->tacc = acc; e->tn = tn; e->grp = grp;
    return n;
}

/* Vorderteil: ein RS-Codewort (128 Symbole) → Interleaver → Randomizer → blk[0..127]; blk[128] = 1 am Rahmenende */
static void rs_block_front(j83b_enc_t *e, const uint8_t *cw, uint8_t *blk)
{
    const int I = e->I, J = e->J, D = e->ilD;
    uint8_t *mem = e->ilmem;
    int row = e->ilrow;
    for (int c0 = 0; c0 < 128; c0 += I) {
        memcpy(mem + row * I, cw + c0, (size_t)I);
        blk[c0] = cw[c0];
        int rr = row;
        for (int b = 1; b < I; b++) {
            rr -= J; if (rr < 0) rr += D;
            blk[c0 + b] = mem[rr * I + b];
        }
        if (++row == D) row = 0;
    }
    e->ilrow = row;
    const uint8_t *r = e->rnd + e->fpos;
    for (int k = 0; k < 128; k++) blk[k] ^= r[k];
    e->fpos += 128;
    blk[128] = e->fpos == e->N;
    if (blk[128]) e->fpos = 0;
}

size_t j83b_back(j83b_enc_t *e, const uint8_t *blk, int16_t *out, uint8_t *lab)
{
    tstate_t t = {e->pstate, e->su, e->sv};
    size_t n;
    if (e->qam == 64) {
        n = trellis64(e, &t, blk, 128, out, lab);
        if (blk[128]) n += trellis64(e, &t, e->trail, 6, out + 2 * n, lab ? lab + n : NULL);
    } else {
        n = trellis256(e, &t, blk, 128, out, lab);   /* 256-QAM: Trailer steckt in den 5 Sync-Gruppen */
    }
    e->pstate = t.pst; e->su = t.su; e->sv = t.sv;
    return n;
}

int j83b_front(j83b_enc_t *e, const uint8_t *pkt, uint8_t *blks)
{
    uint8_t s = 0;
    for (int j = 0; j < 187; j++) s ^= e->chk_nib[2 * j][pkt[1 + j] >> 4] ^ e->chk_nib[2 * j + 1][pkt[1 + j] & 15];
    uint8_t chk = e->chk_fix[s ^ 0x47];
    int nb = 0;
    uint32_t acc = e->bacc; int bn = e->bn, rsn = e->rsn;
    for (int j = 1; j <= 188; j++) {
        acc = (acc << 8) | (j < 188 ? pkt[j] : chk); bn += 8;
        while (bn >= 7) {
            bn -= 7;
            e->rsbuf[rsn++] = (uint8_t)((acc >> bn) & 0x7f);
            if (rsn == 122) {
                uint8_t cw[128];
                rs_encode(e->rsbuf, cw); rsn = 0;
                rs_block_front(e, cw, blks + nb * J83B_BLK); nb++;
            }
        }
        acc &= (1u << bn) - 1;
    }
    e->bacc = acc; e->bn = bn; e->rsn = rsn;
    return nb;
}

size_t j83b_packet(j83b_enc_t *e, const uint8_t *pkt, int16_t *iq, uint8_t *lab)
{
    uint8_t blks[2 * J83B_BLK];
    int nb = j83b_front(e, pkt, blks);
    size_t n = 0;
    for (int b = 0; b < nb; b++) n += j83b_back(e, blks + b * J83B_BLK, iq + 2 * n, lab ? lab + n : NULL);
    return n;
}
