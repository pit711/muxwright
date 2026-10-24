/*
 * dvbt2.c – DVB-T2-Sender (ETSI EN 302 755 V1.4.1), siehe dvbt2.h.
 * Eigene Implementierung nach der Norm; Tabellen aus der Norm (dvbt2_tables.h, tests/dvbt2_gen_tables.py) bzw. die
 * S2-identischen LDPC-Tabellen aus dvbs2_tables.h. gr-dtv (GPL-3.0) diente nur als Prüfreferenz, kein Code übernommen.
 * Bitreihenfolge: gepackte Bytes, MSB zuerst = erstes Bit im Normsinn. Zellen: uint32 = (uint16)I | (uint16)Q << 16.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "dvbt2.h"
#include "dvbmod.h"
#include "dvbs2_tables.h"
#include "dvbt2_tables.h"
#include "dvbt2_fft.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#if defined(__ARM_NEON) || defined(__ARM_NEON__)
#include <arm_neon.h>
#define T2_NEON 1
#else
#define T2_NEON 0
#endif

const char *const t2_mod_name[4] = { "qpsk", "16qam", "64qam", "256qam" };
const char *const t2_rate_name[6] = { "1/2", "3/5", "2/3", "3/4", "4/5", "5/6" };
const char *const t2_gi_name[7] = { "1/32", "1/16", "1/8", "1/4", "1/128", "19/128", "19/256" };
const char *const t2_l1_name[4] = { "bpsk", "qpsk", "16qam", "64qam" };

static const int GI_NUM[7] = { 1, 1, 1, 1, 1, 19, 19 }, GI_DEN[7] = { 32, 16, 8, 4, 128, 128, 256 };
static const int KBCH_N[6] = { 32208, 38688, 43040, 48408, 51648, 53840 }, NBCH_N[6] = { 32400, 38880, 43200, 48600, 51840, 54000 };
static const int BCHT_N[6] = { 12, 12, 10, 12, 12, 10 };
static const int KBCH_S[6] = { 7032, 9552, 10632, 11712, 12432, 13152 }, NBCH_S[6] = { 7200, 9720, 10800, 11880, 12600, 13320 };
static const int ETA[4] = { 2, 4, 6, 8 };
static const int DXT[8] = { 3, 6, 6, 12, 12, 24, 24, 6 }, DYT[8] = { 4, 2, 4, 2, 4, 2, 4, 16 };
#define M_TI ((1 << 19) + (1 << 15))
#ifndef T2_BCH
#define T2_BCH 2
#endif
#ifndef T2_LDPCN
#define T2_LDPCN 1
#endif
#ifndef T2_FFT8_ALL
#define T2_FFT8_ALL 0   /* 1 = Radix-8 auch für 2K/8K/32K (auf dem A9 langsamer als fft16) */
#endif
#ifndef T2FFT_PROF
#define T2FFT_PROF 0
#endif

static void *al64(size_t n)
{
    void *p = NULL;
    if (posix_memalign(&p, 64, (n + 63) & ~(size_t)63)) { fprintf(stderr, "dvbt2: kein Speicher (%zu)\n", n); abort(); }
    memset(p, 0, n);
    return p;
}
static uint8_t REV[256];
static void init_rev(void)
{
    if (REV[1]) return;
    for (int i = 0; i < 256; i++) { int r = 0; for (int b = 0; b < 8; b++) if (i >> b & 1) r |= 0x80 >> b; REV[i] = (uint8_t)r; }
}
static inline int getbit(const uint8_t *p, int i) { return p[i >> 3] >> (7 - (i & 7)) & 1; }
static inline void setbit(uint8_t *p, int i, int v) { if (v) p[i >> 3] |= (uint8_t)(0x80 >> (i & 7)); else p[i >> 3] &= (uint8_t)~(0x80 >> (i & 7)); }
static inline uint32_t iq32(int i, int q) { return (uint16_t)(int16_t)i | (uint32_t)(uint16_t)(int16_t)q << 16; }
static inline int16_t sat16(double x) { long v = lrint(x); return (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v); }

/* ================================================================ Parameter (Tabellen 46–51, 58, 66, 67) */
static int fft_idx(int n) { switch (n) { case 1024: return 0; case 2048: return 1; case 4096: return 2; case 8192: return 3; case 16384: return 4; case 32768: return 5; } return -1; }
static const int KTOT_N[6] = { 853, 1705, 3409, 6817, 13633, 27265 }, KTOT_E[6] = { 0, 0, 0, 6913, 13921, 27841 }, KEXT[6] = { 0, 0, 0, 48, 144, 288 };
static const int NP2T[6] = { 16, 8, 4, 2, 1, 1 }, CP2T[6] = { 558, 1118, 2236, 4472, 8944, 22432 };
static const int KMOD[6] = { 1632, 1632, 3264, 6528, 13056, 0 }, NCPG[6] = { 1, 2, 3, 4, 5, 6 };
/* Tab. 48/49/50 (ohne TR) [fft][ext][pp-1]; 0 = nicht verwendet */
static const int CDATA_T[6][2][8] = {
    {{764,768,798,804,818,0,0,0},{0}}, {{1522,1532,1596,1602,1632,0,1646,0},{0}}, {{3084,3092,3228,3234,3298,0,3328,0},{0}},
    {{6208,6214,6494,6498,6634,0,6698,6698},{6296,6298,6584,6588,6728,0,6788,6788}},
    {{12418,12436,12988,13002,13272,13288,13416,13406},{12678,12698,13262,13276,13552,13568,13698,13688}},
    {{0,24886,0,26022,0,26592,26836,26812},{0,25412,0,26572,0,27152,27404,27376}} };
static const int NFC_T[6][2][8] = {
    {{568,710,710,780,780,0,0,0},{0}}, {{1136,1420,1420,1562,1562,0,1632,0},{0}}, {{2272,2840,2840,3124,3124,0,3266,0},{0}},
    {{4544,5680,5680,6248,6248,0,6532,0},{4608,5760,5760,6336,6336,0,6624,0}},
    {{9088,11360,11360,12496,12496,13064,13064,0},{9280,11600,11600,12760,12760,13340,13340,0}},
    {{0,22720,0,24992,0,26128,0,0},{0,23200,0,25520,0,26680,0,0}} };
static const int CFC_T[6][2][8] = {
    {{402,654,490,707,544,0,0,0},{0}}, {{804,1309,980,1415,1088,0,1396,0},{0}}, {{1609,2619,1961,2831,2177,0,2792,0},{0}},
    {{3218,5238,3922,5662,4354,0,5585,0},{3264,5312,3978,5742,4416,0,5664,0}},
    {{6437,10476,7845,11324,8709,11801,11170,0},{6573,10697,8011,11563,8893,12051,11406,0}},
    {{0,20952,0,22649,0,23603,0,0},{0,21395,0,23127,0,24102,0,0}} };
/* Tab. 58: zulässige Pilotmuster (SISO) als Bitmaske (Bit pp-1) je [fft][gi] */
#define P(x) (1 << ((x) - 1))
static const int PPOK[6][7] = {   /* gi: 1/32 1/16 1/8 1/4 1/128 19/128 19/256 */
    { 0, P(4)|P(5), P(2)|P(3), P(1), 0, 0, 0 },
    { P(7)|P(4), P(4)|P(5), P(2)|P(3), P(1), 0, 0, 0 },
    { P(7)|P(4), P(4)|P(5), P(2)|P(3), P(1), 0, 0, 0 },
    { P(7)|P(4), P(8)|P(4)|P(5), P(2)|P(3)|P(8), P(1)|P(8), P(7), P(2)|P(3)|P(8), P(8)|P(4)|P(5) },
    { P(7)|P(4)|P(6), P(2)|P(8)|P(4)|P(5), P(2)|P(3)|P(8), P(1)|P(8), P(7), P(2)|P(3)|P(8), P(2)|P(8)|P(4)|P(5) },
    { P(4)|P(6), P(2)|P(8)|P(4), P(2)|P(8), 0, P(7), P(2)|P(8), P(2)|P(8)|P(4) } };
#undef P
static void bw_T(int bw10, double *T_us)
{
    switch (bw10) {
    case 17: *T_us = 71.0 / 131.0; break;
    case 50: *T_us = 7.0 / 40.0; break;
    case 60: *T_us = 7.0 / 48.0; break;
    case 70: *T_us = 1.0 / 8.0; break;
    case 100: *T_us = 7.0 / 80.0; break;
    default: *T_us = 7.0 / 64.0; break;
    }
}
/* Frame-Closing-Symbol (Tab. 64): bei SISO, wenn Dx·Dy ≥ 1/GI, außer PP8 */
static int uses_fc(int pp, int gi) { return pp != 8 && (long)DXT[pp - 1] * DYT[pp - 1] * GI_NUM[gi] >= GI_DEN[gi]; }

/* L1-post-Länge (Bits ohne CRC) für 1 PLP, 1 RF, keine FEF/AUX: konfigurierbar 191 + dynamisch 127 */
#define L1POST_INFO 318

static int l1post_sizes(int l1mod, int np2, int *ksig, int *nblk, int *npost, int *npunc)
{
    int kpost_ex = L1POST_INFO + 32, eta = l1mod == T2_L1_BPSK ? 1 : l1mod == T2_L1_QPSK ? 2 : l1mod == T2_L1_16QAM ? 4 : 6;
    int nb = (kpost_ex + 7031) / 7032;
    int kpad = (kpost_ex + nb - 1) / nb * nb - kpost_ex;
    int ks = (kpost_ex + kpad) / nb;
    int nl1mult = np2 == 1 ? 2 * eta : np2 * eta;
    int npt = 6 * (7032 - ks) / 5; if (nl1mult - 1 > npt) npt = nl1mult - 1;
    int npost_t = ks + 168 + 9000 - npt;
    int np = np2 == 1 ? (npost_t + 2 * eta - 1) / (2 * eta) * 2 * eta : (npost_t + eta * np2 - 1) / (eta * np2) * eta * np2;
    *ksig = ks; *nblk = nb; *npost = np; *npunc = npt - (np - npost_t);
    return eta;
}

int t2_cfg_resolve(t2_cfg_t *c, t2_info_t *I, char *err, size_t en)
{
    memset(I, 0, sizeof *I);
    int fi = fft_idx(c->fft);
    if (fi < 0) { snprintf(err, en, "--fft: 1k|2k|4k|8k|16k|32k"); return -1; }
    if (c->ext && fi < 3) { snprintf(err, en, "Extended-Modus nur bei 8K/16K/32K"); return -1; }
    if (c->mod < 0 || c->mod > 3 || c->rate < 0 || c->rate > 5 || c->gi < 0 || c->gi > 6 || c->pp < 1 || c->pp > 8 ||
        c->l1mod < 0 || c->l1mod > 3) { snprintf(err, en, "ungültiger Parameter"); return -1; }
    if (!(PPOK[fi][c->gi] >> (c->pp - 1) & 1)) {
        snprintf(err, en, "PP%d ist bei %dK und GI %s nicht zulässig (EN 302 755 Tab. 58)", c->pp, c->fft / 1024, t2_gi_name[c->gi]);
        return -1;
    }
    if (c->bw10 != 17 && c->bw10 != 50 && c->bw10 != 60 && c->bw10 != 70 && c->bw10 != 80 && c->bw10 != 100) {
        snprintf(err, en, "--bw: 1.7|5|6|7|8|10"); return -1;
    }
    bw_T(c->bw10, &I->T_us);
    I->fs = 1e6 / I->T_us;
    I->N = c->fft; I->guard = c->fft / GI_DEN[c->gi] * GI_NUM[c->gi];
    I->ktotal = c->ext ? KTOT_E[fi] : KTOT_N[fi]; I->kext = KEXT[fi];
    I->np2 = NP2T[fi]; I->cp2 = CP2T[fi];
    I->cdata = CDATA_T[fi][c->ext][c->pp - 1];
    I->fc = uses_fc(c->pp, c->gi);
    I->nfc = I->fc ? NFC_T[fi][c->ext][c->pp - 1] : 0; I->cfc = I->fc ? CFC_T[fi][c->ext][c->pp - 1] : 0;
    if (!I->cdata || (I->fc && (!I->nfc || !I->cfc))) { snprintf(err, en, "Kombination FFT/PP ohne Tabellenwert"); return -1; }
    /* Rahmenlänge */
    double ts_us = (I->N + I->guard) * I->T_us, tp1 = 2048 * I->T_us;
    int lfmax = (int)floor((250000.0 - tp1) / ts_us + 1e-9);
    if (fi == 5 && (lfmax & 1)) lfmax--;
    int lfmin = I->np2 + (fi == 5 ? 3 : 7);
    if (c->ldata <= 0) c->ldata = lfmax - I->np2;
    if (c->ldata > 4095) c->ldata = 4095;
    I->ldata = c->ldata; I->lf = I->np2 + c->ldata;
    if (I->lf > lfmax) { snprintf(err, en, "--ldata %d: Rahmen > 250 ms (max. %d)", c->ldata, lfmax - I->np2); return -1; }
    if (I->lf < lfmin) { snprintf(err, en, "--ldata %d: mindestens %d", c->ldata, lfmin - I->np2); return -1; }
    if (fi == 5 && (I->lf & 1)) { snprintf(err, en, "32K: NP2+Ldata muss gerade sein"); return -1; }
    I->tf_s = (tp1 + I->lf * ts_us) * 1e-6;
    I->ctot = I->np2 * I->cp2 + (I->fc ? (I->ldata - 1) * I->cdata + I->cfc : I->ldata * I->cdata);
    /* FEC */
    I->eta = ETA[c->mod];
    I->nldpc = c->fec16k ? 16200 : 64800;
    I->kbch = c->fec16k ? KBCH_S[c->rate] : KBCH_N[c->rate];
    I->nbch = c->fec16k ? NBCH_S[c->rate] : NBCH_N[c->rate];
    I->ncells = I->nldpc / I->eta;
    /* L1 */
    int ks, nb, np, npu;
    int eta1 = l1post_sizes(c->l1mod, I->np2, &ks, &nb, &np, &npu);
    I->l1post_info = L1POST_INFO; I->npost = np; I->npunc_post = npu;
    I->dl1pre = 1840; I->dl1post = np / eta1 * nb;
    if ((I->dl1pre + I->dl1post) / I->np2 > I->cp2) { snprintf(err, en, "L1 passt nicht in P2"); return -1; }
    I->dplp = I->ctot - I->dl1pre - I->dl1post;
    int nmax = I->dplp / I->ncells; if (nmax > 1023) nmax = 1023;
    if (c->nfec <= 0) c->nfec = nmax;
    if (c->nfec > nmax || c->nfec < 1) { snprintf(err, en, "--nfec %d: 1…%d", c->nfec, nmax); return -1; }
    I->nfec = c->nfec; I->ndummy = I->dplp - I->nfec * I->ncells;
    if (I->ndummy * 2 > I->ctot) { snprintf(err, en, "--nfec %d: Dummy-Zellen > 50 %% (Abschn. 8.3.2)", c->nfec); return -1; }
    int tmax = M_TI / I->ncells;
    if (c->nti <= 0) c->nti = (I->nfec + tmax - 1) / tmax;
    if (c->nti > 255 || c->nti > I->nfec) { snprintf(err, en, "--nti %d unzulässig", c->nti); return -1; }
    I->nti = c->nti; I->nfec_ti_max = (I->nfec + c->nti - 1) / c->nti;
    if (I->nfec_ti_max > tmax) { snprintf(err, en, "--nti %d: TI-Speicher überschritten (min. %d)", c->nti, (I->nfec + tmax - 1) / tmax); return -1; }
    if (c->nt2 <= 0) c->nt2 = 2;
    if (c->nt2 < 2 || c->nt2 > 255) { snprintf(err, en, "--t2frames: 2…255"); return -1; }
    I->nt2 = c->nt2;
    I->ts_bps = (double)I->nfec * (I->kbch - 80) * 188.0 / 187.0 / I->tf_s;
    /* S2-Feld 1 (Tab. 19a) */
    int t2gi = c->gi == T2_GI_1_128 || c->gi == T2_GI_19_128 || c->gi == T2_GI_19_256;
    static const int s2f[6] = { 3, 0, 2, 1, 4, 5 };
    I->s2field1 = s2f[fi] + (t2gi && (fi == 3 || fi == 5) ? (fi == 3 ? 5 : 2) : 0);
    I->ifft_scale = 0;
    return 0;
}

/* ================================================================ Trägerbelegung je Symbolart */
enum { CT_DATA = 0, CT_SP, CT_CP, CT_P2, CT_RES, CT_FC };
/* kind: 0 = P2, 1 = Datensymbol (Phase l mod Dy über l), 2 = Frame Closing */
static void carrier_map(const t2_cfg_t *c, const t2_info_t *I, int kind, int l, uint8_t *ty)
{
    const int K = I->ktotal, fi = fft_idx(c->fft), dx = DXT[c->pp - 1], dy = DYT[c->pp - 1];
    const int kx = c->ext ? I->kext : 0;
    memset(ty, CT_DATA, (size_t)K);
    if (kind == 0) {
        int step = fi == 5 ? 6 : 3;
        for (int k = 0; k < K; k += step) ty[k] = CT_P2;
        if (c->ext) for (int i = 0; i < I->kext; i++) { ty[i] = CT_P2; ty[K - 1 - i] = CT_P2; }
        const uint16_t *r; int nr;
        switch (fi) {
        case 0: r = t2_p2res_1k; nr = 10; break; case 1: r = t2_p2res_2k; nr = 18; break; case 2: r = t2_p2res_4k; nr = 36; break;
        case 3: r = t2_p2res_8k; nr = 72; break; case 4: r = t2_p2res_16k; nr = 144; break; default: r = t2_p2res_32k; nr = 288; break;
        }
        for (int i = 0; i < nr; i++) ty[r[i] + kx] = CT_RES;
        return;
    }
    if (kind == 2) {
        for (int k = 0; k < K; k += dx) ty[k] = CT_FC;
        if ((fi == 0 && (c->pp == 4 || c->pp == 5)) || (fi == 1 && c->pp == 7)) ty[K - 2] = CT_FC;
        ty[0] = CT_SP; ty[K - 1] = CT_SP;
        return;
    }
    /* Continual Pilots: Gruppen CP1..CPn (Tab. 61), k = k32 mod Kmod; Extended: Tab. G.2 */
    for (int g = 1; g <= NCPG[fi]; g++)
        for (int i = t2_cp_idx[c->pp - 1][g - 1]; i < t2_cp_idx[c->pp - 1][g]; i++) {
            int k = KMOD[fi] ? t2_cp_val[i] % KMOD[fi] : t2_cp_val[i];
            if (k < K) ty[k] = CT_CP;
        }
    if (c->ext) {
        const uint16_t *v = fi == 3 ? t2_cpext_8k_val : fi == 4 ? t2_cpext_16k_val : t2_cpext_32k_val;
        const uint16_t *ix = fi == 3 ? t2_cpext_8k_idx : fi == 4 ? t2_cpext_16k_idx : t2_cpext_32k_idx;
        for (int i = ix[c->pp - 1]; i < ix[c->pp]; i++) ty[v[i]] = CT_CP;
    }
    for (int k = 0; k < K; k++) {
        int r = (k - kx) % (dx * dy); if (r < 0) r += dx * dy;
        if (r == dx * (l % dy)) ty[k] = CT_SP;
    }
    ty[0] = CT_SP; ty[K - 1] = CT_SP;
}

/* ================================================================ BCH (Tab. 7a/7b) */
typedef struct { int kbch, deg; uint8_t g[200]; uint32_t (*tab)[256][6]; uint32_t (*t5)[256][5]; } bch_t;
static void polymul(uint8_t *a, int *da, const uint16_t *bt, int nb)
{
    int db = 0; uint8_t bc[20] = { 0 };
    for (int i = 0; i < nb; i++) { bc[bt[i]] = 1; if (bt[i] > db) db = bt[i]; }
    uint8_t r[256] = { 0 };
    for (int i = 0; i <= *da; i++) if (a[i]) for (int j = 0; j <= db; j++) r[i + j] ^= bc[j];
    *da += db; memcpy(a, r, (size_t)*da + 1);
}
static void bch_init(bch_t *b, int kbch, int t, int shortf)
{
    static const uint16_t gN[12][14] = {
        {0,2,3,5,16},{0,1,4,5,6,8,16},{0,2,3,4,5,7,8,9,10,11,16},{0,2,4,6,9,11,12,14,16},
        {0,1,2,3,5,8,9,10,11,12,16},{0,2,4,5,7,8,9,10,12,13,14,15,16},{0,2,5,6,8,9,10,11,13,15,16},
        {0,1,2,5,6,8,9,12,13,14,16},{0,5,7,9,10,11,16},{0,1,2,5,7,8,10,12,13,14,16},
        {0,2,3,5,9,11,12,13,16},{0,1,5,6,7,9,11,12,16}};
    static const int gNl[12] = {5,7,11,9,11,13,11,11,7,11,9,9};
    static const uint16_t gS[12][14] = {
        {0,1,3,5,14},{0,6,8,11,14},{0,1,2,6,9,10,14},{0,4,7,8,10,12,14},{0,2,4,6,8,9,11,13,14},
        {0,3,7,8,9,13,14},{0,2,5,6,7,10,11,13,14},{0,5,8,9,10,11,14},{0,1,2,3,9,10,14},
        {0,3,6,9,11,12,14},{0,4,11,12,14},{0,1,2,3,5,6,7,8,10,13,14}};
    static const int gSl[12] = {5,5,7,7,9,7,9,7,7,7,5,11};
    memset(b->g, 0, sizeof b->g); b->g[0] = 1; int dg = 0;
    for (int k = 0; k < t; k++) { if (shortf) polymul(b->g, &dg, gS[k], gSl[k]); else polymul(b->g, &dg, gN[k], gNl[k]); }
    b->deg = dg; b->kbch = kbch;
    b->tab = malloc(sizeof(uint32_t[4][256][6]));
    uint8_t reg[200];
    for (int k = 0; k < 4; k++)
        for (int v = 0; v < 256; v++) {
            memset(reg, 0, sizeof reg);
            for (int i = 0; i < 8 + 8 * k; i++) {
                int bit = i < 8 ? (v >> (7 - i)) & 1 : 0, fb = bit ^ reg[dg - 1];
                for (int j = dg - 1; j > 0; j--) reg[j] = (uint8_t)(reg[j - 1] ^ (b->g[j] & fb));
                reg[0] = (uint8_t)(b->g[0] & fb);
            }
            uint32_t *w = b->tab[k][v]; memset(w, 0, 24);
            for (int j = 0; j < dg; j++) if (reg[dg - 1 - j]) w[j >> 5] |= 0x80000000u >> (j & 31);
        }
    b->t5 = malloc(sizeof(uint32_t[4][256][5]));       /* kompakt (20 statt 24 Byte je Eintrag) für ≤ 160 Paritätsbits */
    for (int k = 0; k < 4; k++) for (int v = 0; v < 256; v++) memcpy(b->t5[k][v], b->tab[k][v], 20);
}
static void bch_free(bch_t *b) { free(b->tab); free(b->t5); b->tab = NULL; b->t5 = NULL; }
/* Variante für ≤ 160 Paritätsbits (t = 10: Raten 2/3, 5/6 normal): 5 statt 6 Registerwörter */
static void bch_encode5(const bch_t *b, uint8_t *cw)
{
    uint32_t r0 = 0, r1 = 0, r2 = 0, r3 = 0, r4 = 0;
    const uint32_t (*T0)[5] = b->t5[0], (*T1)[5] = b->t5[1], (*T2)[5] = b->t5[2], (*T3)[5] = b->t5[3];
    int n = b->kbch / 8, i = 0;
#if T2_BCH == 2
    for (; i + 2 <= n; i += 2) {
        const uint32_t *a = T1[(r0 >> 24) ^ cw[i]], *bb = T0[((r0 >> 16) & 0xFF) ^ cw[i + 1]];
        r0 = (r0 << 16 | r1 >> 16) ^ a[0] ^ bb[0];
        r1 = (r1 << 16 | r2 >> 16) ^ a[1] ^ bb[1];
        r2 = (r2 << 16 | r3 >> 16) ^ a[2] ^ bb[2];
        r3 = (r3 << 16 | r4 >> 16) ^ a[3] ^ bb[3];
        r4 = (r4 << 16) ^ a[4] ^ bb[4];
    }
#endif
    for (; i + 4 <= n; i += 4) {
        const uint32_t *a = T3[(r0 >> 24) ^ cw[i]], *bb = T2[((r0 >> 16) & 0xFF) ^ cw[i + 1]],
                       *cc = T1[((r0 >> 8) & 0xFF) ^ cw[i + 2]], *d = T0[(r0 & 0xFF) ^ cw[i + 3]];
        r0 = r1 ^ a[0] ^ bb[0] ^ cc[0] ^ d[0];
        r1 = r2 ^ a[1] ^ bb[1] ^ cc[1] ^ d[1];
        r2 = r3 ^ a[2] ^ bb[2] ^ cc[2] ^ d[2];
        r3 = r4 ^ a[3] ^ bb[3] ^ cc[3] ^ d[3];
        r4 = a[4] ^ bb[4] ^ cc[4] ^ d[4];
    }
    for (; i < n; i++) {
        const uint32_t *a = T0[(r0 >> 24) ^ cw[i]];
        r0 = ((r0 << 8) | (r1 >> 24)) ^ a[0]; r1 = ((r1 << 8) | (r2 >> 24)) ^ a[1];
        r2 = ((r2 << 8) | (r3 >> 24)) ^ a[2]; r3 = ((r3 << 8) | (r4 >> 24)) ^ a[3]; r4 = (r4 << 8) ^ a[4];
    }
    uint32_t r[5] = { r0, r1, r2, r3, r4 };
    uint8_t *p = cw + n;
    for (int k = 0; k < b->deg / 8; k++) p[k] = (uint8_t)(r[k >> 2] >> (24 - 8 * (k & 3)));
}
/* cw[0 .. kbch/8) → Parität nach cw[kbch/8 ..) */
static void bch_encode(const bch_t *b, uint8_t *cw)
{
    if (b->deg <= 160) { bch_encode5(b, cw); return; }
    uint32_t r0 = 0, r1 = 0, r2 = 0, r3 = 0, r4 = 0, r5 = 0;
    const uint32_t (*T0)[6] = b->tab[0], (*T1)[6] = b->tab[1], (*T2)[6] = b->tab[2], (*T3)[6] = b->tab[3];
    int n = b->kbch / 8, i = 0;
    for (; i + 4 <= n; i += 4) {
        const uint32_t *a = T3[(r0 >> 24) ^ cw[i]], *bb = T2[((r0 >> 16) & 0xFF) ^ cw[i + 1]],
                       *cc = T1[((r0 >> 8) & 0xFF) ^ cw[i + 2]], *d = T0[(r0 & 0xFF) ^ cw[i + 3]];
        r0 = r1 ^ a[0] ^ bb[0] ^ cc[0] ^ d[0];
        r1 = r2 ^ a[1] ^ bb[1] ^ cc[1] ^ d[1];
        r2 = r3 ^ a[2] ^ bb[2] ^ cc[2] ^ d[2];
        r3 = r4 ^ a[3] ^ bb[3] ^ cc[3] ^ d[3];
        r4 = r5 ^ a[4] ^ bb[4] ^ cc[4] ^ d[4];
        r5 = a[5] ^ bb[5] ^ cc[5] ^ d[5];
    }
    for (; i < n; i++) {
        const uint32_t *a = T0[(r0 >> 24) ^ cw[i]];
        r0 = ((r0 << 8) | (r1 >> 24)) ^ a[0]; r1 = ((r1 << 8) | (r2 >> 24)) ^ a[1];
        r2 = ((r2 << 8) | (r3 >> 24)) ^ a[2]; r3 = ((r3 << 8) | (r4 >> 24)) ^ a[3];
        r4 = ((r4 << 8) | (r5 >> 24)) ^ a[4]; r5 = (r5 << 8) ^ a[5];
    }
    uint32_t r[6] = { r0, r1, r2, r3, r4, r5 };
    uint8_t *p = cw + n;
    for (int k = 0; k < b->deg / 8; k++) p[k] = (uint8_t)(r[k >> 2] >> (24 - 8 * (k & 3)));
}
/* bitweise Referenz: in/out 0/1-Bytes, K Infobits → K + deg */
static void bch_ref(const bch_t *b, const uint8_t *in, int K, uint8_t *out)
{
    int D = b->deg; uint8_t reg[200] = { 0 };
    memmove(out, in, (size_t)K);
    for (int i = 0; i < K; i++) {
        int fb = in[i] ^ reg[D - 1];
        for (int j = D - 1; j > 0; j--) reg[j] = (uint8_t)(reg[j - 1] ^ (b->g[j] & fb));
        reg[0] = (uint8_t)(b->g[0] & fb);
    }
    for (int j = 0; j < D; j++) out[K + j] = reg[D - 1 - j];
}

/* ================================================================ LDPC (quasizyklisch, 360er-Rotationen) */
#define LW 12
typedef struct { int kldpc, nldpc, M, q, rows; const uint16_t *addr, *row; uint16_t *lc, *ls; uint32_t *P; } ldpc_t;
static int ldpc_table(int rate, int shortf, const uint16_t **addr, const uint16_t **row, int *nrow)
{
#define SET(a) do { *addr = a##_addr; *row = a##_row; *nrow = (int)(sizeof(a##_row) / sizeof(uint16_t)) - 1; } while (0)
    if (!shortf) switch (rate) {
        case T2_R1_2: SET(ldpc_n_1_2); break; case T2_R3_5: SET(ldpc_n_3_5); break; case T2_R2_3: SET(ldpc_t2_n_2_3); break;
        case T2_R3_4: SET(ldpc_n_3_4); break; case T2_R4_5: SET(ldpc_n_4_5); break; case T2_R5_6: SET(ldpc_n_5_6); break;
        default: return -1;
    } else switch (rate) {
        case -1: SET(ldpc_s_1_4); break;   /* L1-pre */
        case T2_R1_2: SET(ldpc_s_1_2); break; case T2_R3_5: SET(ldpc_t2_s_3_5); break; case T2_R2_3: SET(ldpc_s_2_3); break;
        case T2_R3_4: SET(ldpc_s_3_4); break; case T2_R4_5: SET(ldpc_s_4_5); break; case T2_R5_6: SET(ldpc_s_5_6); break;
        default: return -1;
    }
#undef SET
    return 0;
}
static int ldpc_init(ldpc_t *L, int rate, int shortf, int kldpc)
{
    memset(L, 0, sizeof *L);
    if (ldpc_table(rate, shortf, &L->addr, &L->row, &L->rows) < 0) return -1;
    L->kldpc = kldpc; L->nldpc = shortf ? 16200 : 64800; L->M = L->nldpc - kldpc; L->q = L->M / 360;
    if (L->rows * 360 != kldpc || L->q * 360 != L->M) return -1;
    int na = L->row[L->rows];
    L->lc = malloc((size_t)na * 2); L->ls = malloc((size_t)na * 2);
    for (int a = 0; a < na; a++) { int x = L->addr[a]; L->lc[a] = (uint16_t)(x % L->q); L->ls[a] = (uint16_t)((360 - x / L->q) % 360); }
    L->P = al64((size_t)L->q * LW * 4);
    return 0;
}
static void ldpc_free(ldpc_t *L) { free(L->lc); free(L->ls); free(L->P); }
/* cw: gepackt; Infobits cw[0..kldpc/8). interleaved = 1: Parität gleich paritätsverschachtelt (Gl. 6.1.3:
   u[K+360t+s] = λ[K+Q·s+t]), sonst natürliche Reihenfolge. */
static void ldpc_encode(ldpc_t *L, uint8_t *cw, int interleaved)
{
    const int q = L->q; uint32_t *P = L->P;
    memset(P, 0, (size_t)q * LW * 4);
    uint32_t D[2 * LW + 2];
    for (int g = 0; g < L->rows; g++) {
        const uint8_t *b = cw + 45 * g; uint32_t u[LW];
        for (int w = 0; w < 11; w++)
            u[w] = (uint32_t)REV[b[4 * w]] | (uint32_t)REV[b[4 * w + 1]] << 8 | (uint32_t)REV[b[4 * w + 2]] << 16 | (uint32_t)REV[b[4 * w + 3]] << 24;
        u[11] = REV[b[44]];
        for (int w = 0; w < 11; w++) D[w] = u[w];
        D[11] = u[11] | (u[0] << 8);
        for (int w = 1; w < 12; w++) D[11 + w] = (u[w - 1] >> 24) | (u[w] << 8);
        D[23] = D[24] = D[25] = 0;
        for (int a = L->row[g]; a < L->row[g + 1]; a++) {
            uint32_t *dst = P + (size_t)L->lc[a] * LW; int st = L->ls[a], wi = st >> 5, sh = st & 31;
            const uint32_t *src = D + wi;
#if T2_NEON && T2_LDPCN
            const int32x4_t rs = vdupq_n_s32(-sh), ls = vdupq_n_s32(32 - sh);
            for (int w = 0; w < LW; w += 4) {
                uint32x4_t x = vorrq_u32(vshlq_u32(vld1q_u32(src + w), rs), vshlq_u32(vld1q_u32(src + w + 1), ls));
                vst1q_u32(dst + w, veorq_u32(vld1q_u32(dst + w), x));
            }
#else
            if (sh == 0) for (int w = 0; w < LW; w++) dst[w] ^= src[w];
            else for (int w = 0; w < LW; w++) dst[w] ^= (src[w] >> sh) | (src[w + 1] << (32 - sh));
#endif
        }
    }
    for (int c = 1; c < q; c++) { uint32_t *a = P + (size_t)c * LW, *pr = a - LW; for (int w = 0; w < LW; w++) a[w] ^= pr[w]; }
    const uint32_t *T = P + (size_t)(q - 1) * LW; uint32_t E[LW], carry = 0;
    for (int w = 0; w < LW; w++) {
        uint32_t x = T[w]; if (w == 11) x &= 0xFF;
        x ^= x << 1; x ^= x << 2; x ^= x << 4; x ^= x << 8; x ^= x << 16;
        if (carry) x = ~x;
        E[w] = (x << 1) | (carry & 1); carry = x >> 31;
    }
    uint8_t *o = cw + L->kldpc / 8;
    if (interleaved) {
        for (int c = 0; c < q; c++) {
            const uint32_t *a = P + (size_t)c * LW;
            for (int w = 0; w < 11; w++) {
                uint32_t x = a[w] ^ E[w];
                o[0] = REV[x & 0xFF]; o[1] = REV[(x >> 8) & 0xFF]; o[2] = REV[(x >> 16) & 0xFF]; o[3] = REV[x >> 24]; o += 4;
            }
            *o++ = REV[(a[11] ^ E[11]) & 0xFF];
        }
        return;
    }
    for (int c = 0; c < q; c++) { uint32_t *a = P + (size_t)c * LW; for (int w = 0; w < LW; w++) a[w] ^= E[w]; }
    unsigned acc = 0; int nb = 0;
    for (int j = 0; j < 360; j++) {
        int wj = j >> 5, bj = j & 31; const uint32_t *col = P + wj;
        for (int c = 0; c < q; c++) { acc = (acc << 1) | ((col[(size_t)c * LW] >> bj) & 1); if (++nb == 8) { *o++ = (uint8_t)acc; acc = 0; nb = 0; } }
    }
}
/* bitweise Referenz (0/1-Bytes), natürliche Reihenfolge */
static void ldpc_ref(const ldpc_t *L, const uint8_t *in, uint8_t *out)
{
    int kl = L->kldpc, M = L->M, q = L->q;
    memmove(out, in, (size_t)kl);
    uint8_t *p = out + kl; memset(p, 0, (size_t)M);
    for (int i = 0; i < kl; i++) {
        if (!in[i]) continue;
        int g = i / 360, off = i % 360;
        for (int c = L->row[g]; c < L->row[g + 1]; c++) p[(L->addr[c] + off * q) % M] ^= 1;
    }
    for (int i = 1; i < M; i++) p[i] ^= p[i - 1];
}

/* ================================================================ Bit-Interleaver + Demux (6.1.3, 6.2.1) */
typedef struct { int nc, nr, eta, ncells, two; uint8_t tc[16]; uint8_t perm[16]; } bitil_t;
static const uint8_t DM16A[8] = {7,1,4,2,5,3,6,0}, DM16B[8] = {0,5,1,2,4,7,3,6};
static const uint8_t DM64A[12] = {11,7,3,10,6,2,9,5,1,8,4,0}, DM64B[12] = {2,7,6,9,0,3,1,8,4,11,5,10};
static const uint8_t DM256A[16] = {15,1,13,3,8,11,9,5,10,6,4,7,12,2,14,0}, DM256B[16] = {2,11,3,4,0,9,1,8,10,13,7,14,6,15,5,12};
static const uint8_t DM256C[16] = {7,2,9,0,4,6,13,3,14,10,15,5,8,12,11,1}, DM256S[8] = {7,3,1,5,2,6,4,0};
static const uint8_t *demux_table(int mod, int rate, int shortf)
{
    switch (mod) {
    case T2_16QAM: return (rate == T2_R3_5 && !shortf) ? DM16B : DM16A;
    case T2_64QAM: return (rate == T2_R3_5 && !shortf) ? DM64B : DM64A;
    case T2_256QAM: return shortf ? DM256S : rate == T2_R3_5 ? DM256B : rate == T2_R2_3 ? DM256C : DM256A;
    }
    return NULL;
}
static void bitil_init(bitil_t *B, int mod, int rate, int shortf)
{
    static const uint8_t tN16[8] = {0,0,2,4,4,5,7,7}, tS16[8] = {0,0,0,1,7,20,20,21};
    static const uint8_t tN64[12] = {0,0,2,2,3,4,4,5,5,7,8,9}, tS64[12] = {0,0,0,2,2,2,3,3,3,6,7,7};
    static const uint8_t tN256[16] = {0,2,2,2,2,3,7,15,16,20,22,22,27,27,28,32}, tS256[8] = {0,0,0,1,7,20,20,21};
    memset(B, 0, sizeof *B);
    int nldpc = shortf ? 16200 : 64800;
    B->eta = ETA[mod]; B->ncells = nldpc / B->eta;
    if (mod == T2_QPSK) { B->nc = 2; return; }
    const uint8_t *tc = mod == T2_16QAM ? (shortf ? tS16 : tN16) : mod == T2_64QAM ? (shortf ? tS64 : tN64) : (shortf ? tS256 : tN256);
    B->nc = (mod == T2_256QAM && shortf) ? 8 : 2 * B->eta; B->nr = nldpc / B->nc; B->two = B->nc == 2 * B->eta;
    memcpy(B->tc, tc, (size_t)B->nc);
    const uint8_t *dm = demux_table(mod, rate, shortf);
    for (int c = 0; c < B->nc; c++) B->perm[dm[c]] = (uint8_t)c;   /* Ausgangsbit e kommt aus Spalte perm[e] */
}
static inline uint32_t getbits32f(const uint8_t *cw, int bitpos)
{
    uint64_t v; memcpy(&v, cw + (bitpos >> 3), 8); v = __builtin_bswap64(v);
    return (uint32_t)(v >> (32 - (bitpos & 7)));
}
#define TSTAGE(j, m) for (int k = 0; k < 32; k = (k + j + 1) & ~j) { uint32_t t_ = (A[k] ^ (A[k + j] >> j)) & (m); A[k] ^= t_; A[k + j] ^= t_ << j; }
static inline void transpose32u(uint32_t *A) { TSTAGE(16, 0x0000FFFFu) TSTAGE(8, 0x00FF00FFu) TSTAGE(4, 0x0F0F0F0Fu) TSTAGE(2, 0x33333333u) TSTAGE(1, 0x55555555u) }

#if T2_NEON
/* 8×8-Bittransposition je 64-Bit-Hälfte: Byte i Bit j → Byte j Bit i (Hacker's Delight 7-3) */
static inline uint64x2_t tr8q(uint64x2_t x)
{
    uint64x2_t t;
    t = vandq_u64(veorq_u64(x, vshrq_n_u64(x, 7)), vdupq_n_u64(0x00AA00AA00AA00AAull));  x = veorq_u64(veorq_u64(x, t), vshlq_n_u64(t, 7));
    t = vandq_u64(veorq_u64(x, vshrq_n_u64(x, 14)), vdupq_n_u64(0x0000CCCC0000CCCCull)); x = veorq_u64(veorq_u64(x, t), vshlq_n_u64(t, 14));
    t = vandq_u64(veorq_u64(x, vshrq_n_u64(x, 28)), vdupq_n_u64(0x00000000F0F0F0F0ull)); x = veorq_u64(veorq_u64(x, t), vshlq_n_u64(t, 28));
    return x;
}
/* wie bitil_run, Transposition 16 Spalten × 32 Zeilen mit NEON (vld4 zerlegt die Spaltenwörter in Zeilengruppen-Bytes) */
static void bitil_run_neon(const bitil_t *B, const uint8_t *cw, uint8_t *lab)
{
    const int nc = B->nc, nr = B->nr, eta = B->eta, two = B->two;
    uint32_t W[16] __attribute__((aligned(16)));
    memset(W, 0, sizeof W);
    const int16x8_t shx = vdupq_n_s16((int16_t)-(16 - nc)), she = vdupq_n_s16((int16_t)-eta);
    const uint16x8_t msk = vdupq_n_u16((uint16_t)((1u << eta) - 1));
    uint8_t tmp[64] __attribute__((aligned(16)));
    uint8_t *o = lab;
    int widx[16];
    for (int e = 0; e < nc; e++) widx[e] = (e & ~7) + 7 - (e & 7);   /* Spalten je 8er-Gruppe umgekehrt ablegen */
    /* ab Zeile 32 (kein Umlauf mehr): Spaltenwörter direkt mit NEON laden – 8 Byte ab Bitposition, Bytes umdrehen,
       64-Bit-Shift; die Bitlage innerhalb des Bytes ist je Spalte konstant (Zeilenblöcke sind Vielfache von 32) */
    static const uint8_t zero8[16] __attribute__((aligned(16))) = { 0 };
    const uint8_t *src[16]; int64x2_t shv[8];
    {
        int64_t shs[16];
        for (int w = 0; w < 16; w++) { src[w] = zero8; shs[w] = 0; }
        for (int e = 0; e < nc; e++) {
            int c = B->perm[e], off = c * nr - B->tc[c];
            src[widx[e]] = cw + (off >> 3); shs[widx[e]] = -(32 - (off & 7));
        }
        for (int k = 0; k < 8; k++) shv[k] = vcombine_s64(vcreate_s64((uint64_t)shs[2 * k]), vcreate_s64((uint64_t)shs[2 * k + 1]));
    }
    for (int r0 = 0; r0 < nr; r0 += 32) {
        const int nrow = nr - r0 < 32 ? nr - r0 : 32;
        uint8x16x4_t v;
        if (r0 >= 32) {
            const int bo = r0 >> 3;
            uint32x2_t wv[8];
            for (int k = 0; k < 8; k++) {
                const uint8_t *s0 = src[2 * k] == zero8 ? zero8 : src[2 * k] + bo, *s1 = src[2 * k + 1] == zero8 ? zero8 : src[2 * k + 1] + bo;
                uint64x2_t x = vreinterpretq_u64_u8(vcombine_u8(vrev64_u8(vld1_u8(s0)), vrev64_u8(vld1_u8(s1))));
                wv[k] = vmovn_u64(vshlq_u64(x, shv[k]));
            }
            uint8x16_t q0 = vreinterpretq_u8_u32(vcombine_u32(wv[0], wv[1])), q1 = vreinterpretq_u8_u32(vcombine_u32(wv[2], wv[3]));
            uint8x16_t q2 = vreinterpretq_u8_u32(vcombine_u32(wv[4], wv[5])), q3 = vreinterpretq_u8_u32(vcombine_u32(wv[6], wv[7]));
            uint8x16x2_t a = vuzpq_u8(q0, q1), b = vuzpq_u8(q2, q3);
            uint8x16x2_t c02 = vuzpq_u8(a.val[0], b.val[0]), c13 = vuzpq_u8(a.val[1], b.val[1]);
            v.val[0] = c02.val[0]; v.val[2] = c02.val[1]; v.val[1] = c13.val[0]; v.val[3] = c13.val[1];
        } else {
        for (int e = 0; e < nc; e++) {
            int c = B->perm[e], sft = r0 - B->tc[c], base = c * nr; uint32_t a;
            if (sft >= 0) a = getbits32f(cw, base + sft);
            else {
                int t = -sft; a = getbits32f(cw, base + nr - t);
                if (t < 32) a = (a & ~(0xFFFFFFFFu >> t)) | (getbits32f(cw, base) >> t);
            }
            W[widx[e]] = a;
        }
        v = vld4q_u8((const uint8_t *)W);
        }
        uint8_t *dst = nrow == 32 ? o : tmp;
        for (int g = 0; g < 4; g++) {
            uint8x16_t by = g == 0 ? v.val[3] : g == 1 ? v.val[2] : g == 2 ? v.val[1] : v.val[0];
            uint8x16_t y = vrev64q_u8(vreinterpretq_u8_u64(tr8q(vreinterpretq_u64_u8(by))));
            uint8x8x2_t z = vzip_u8(vget_high_u8(y), vget_low_u8(y));
            uint16x8_t x = vshlq_u16(vreinterpretq_u16_u8(vcombine_u8(z.val[0], z.val[1])), shx);
            if (two) {
                uint8x8x2_t ab; ab.val[0] = vmovn_u16(vshlq_u16(x, she)); ab.val[1] = vmovn_u16(vandq_u16(x, msk));
                vst2_u8(dst + 16 * g, ab);
            } else vst1_u8(dst + 8 * g, vmovn_u16(x));
        }
        if (nrow < 32) memcpy(o, tmp, (size_t)nrow * (two ? 2 : 1));
        o += nrow * (two ? 2 : 1);
    }
}
#endif
/* cw: Nldpc Bit, paritätsverschachtelt, gepackt (+8 Byte Reserve) → lab[ncells] (Zellwort, y0 = MSB) */
static void bitil_run(const bitil_t *B, const uint8_t *cw, uint8_t *lab)
{
    if (B->nc == 2) {   /* QPSK: kein Interleaver, Zelle = 2 aufeinanderfolgende Bits */
        for (int i = 0; i < B->ncells / 4; i++) { uint8_t b = cw[i]; lab[0] = b >> 6; lab[1] = (b >> 4) & 3; lab[2] = (b >> 2) & 3; lab[3] = b & 3; lab += 4; }
        return;
    }
#if T2_NEON
    bitil_run_neon(B, cw, lab); return;
#endif
    const int nc = B->nc, nr = B->nr, eta = B->eta;
    const uint32_t mask = (1u << eta) - 1;
    uint32_t A[32];
    uint8_t *o = lab;
    for (int r0 = 0; r0 < nr; r0 += 32) {
        int nrow = nr - r0 < 32 ? nr - r0 : 32;
        /* Spalten in Ausgangsbit-Reihenfolge laden: Zeile k, Bit (31-e) = Bit e des Demux-Worts */
        for (int e = 0; e < nc; e++) {
            int c = B->perm[e], s = r0 - B->tc[c], base = c * nr;
            if (s >= 0) A[e] = getbits32f(cw, base + s);
            else {
                int t = -s; uint32_t hi = getbits32f(cw, base + nr - t);
                if (t < 32) hi = (hi & ~(0xFFFFFFFFu >> t)) | (getbits32f(cw, base) >> t);
                A[e] = hi;
            }
        }
        for (int e = nc; e < 32; e++) A[e] = 0;
        transpose32u(A);
        if (B->two)
            for (int k = 0; k < nrow; k++) { uint32_t x = A[k] >> (32 - nc); o[0] = (uint8_t)(x >> eta); o[1] = (uint8_t)(x & mask); o += 2; }
        else
            for (int k = 0; k < nrow; k++) *o++ = (uint8_t)(A[k] >> 24);
    }
}
/* bitweise Referenz (0/1-Bytes, natürliche LDPC-Reihenfolge) → Zellwörter */
static void bitil_ref(const bitil_t *B, int mod, int rate, int shortf, int kldpc, const uint8_t *bits, uint8_t *lab)
{
    int n = shortf ? 16200 : 64800, q = (n - kldpc) / 360;
    uint8_t *u = malloc((size_t)n), *v = malloc((size_t)n);
    for (int i = 0; i < n; i++) u[i] = bits[i];
    if (mod != T2_QPSK) {
        for (int t = 0; t < q; t++) for (int s = 0; s < 360; s++) u[kldpc + 360 * t + s] = bits[kldpc + q * s + t];
        for (int i = 0; i < n; i++) { int c = i / B->nr, r = (i + B->tc[c]) % B->nr; v[r * B->nc + c] = u[i]; }
    } else memcpy(v, u, (size_t)n);
    int nsub = mod == T2_QPSK ? 2 : B->nc, eta = ETA[mod];
    const uint8_t *dm = demux_table(mod, rate, shortf);
    for (int w = 0; w < n / nsub; w++) {
        int b[16];
        for (int i = 0; i < nsub; i++) b[mod == T2_QPSK ? i : dm[i]] = v[w * nsub + i];
        int ncw = nsub / eta;
        for (int cidx = 0; cidx < ncw; cidx++) { int x = 0; for (int i = 0; i < eta; i++) x = x << 1 | b[cidx * eta + i]; lab[w * ncw + cidx] = (uint8_t)x; }
    }
    free(u); free(v);
}

/* ================================================================ Konstellationen (Tab. 14/15, 6.3) */
/* Achswert aus Bits (b0 = Vorzeichen, Rest Gray): ungerade Werte ±1 … ±(2^m − 1) */
static int axis_val(int bits, int m)
{
    int sign = bits >> (m - 1) & 1, g = bits & ((1 << (m - 1)) - 1), b = g;
    for (int s = 1; s < 8; s <<= 1) b ^= b >> s;
    int mag = 2 * ((1 << (m - 1)) - 1 - b) + 1;
    return sign ? -mag : mag;
}
/* Zellwort v (y0 = MSB) → normierter Punkt; mod 0..3 = QPSK…256, -1 = BPSK */
static void const_point(int mod, int v, double *re, double *im)
{
    if (mod < 0) { *re = (v & 1) ? -1 : 1; *im = 0; return; }
    int eta = ETA[mod], m = eta / 2, bi = 0, bq = 0;
    for (int i = 0; i < m; i++) { bi = bi << 1 | (v >> (eta - 1 - 2 * i) & 1); bq = bq << 1 | (v >> (eta - 2 - 2 * i) & 1); }
    static const double nrm[4] = { 1.4142135623730951, 3.1622776601683795, 6.48074069840786, 13.038404810405298 };
    *re = axis_val(bi, m) / nrm[mod]; *im = axis_val(bq, m) / nrm[mod];
}
static double rot_angle(int mod)
{
    static const double deg[3] = { 29.0, 16.8, 8.6 };
    return mod == T2_256QAM ? atan(1.0 / 16.0) : deg[mod] * M_PI / 180.0;
}

/* ================================================================ Struktur */
struct t2_frame { uint32_t *bins; int fidx; };   /* LF × N IFFT-Bins (I | Q<<16) */

typedef struct { uint16_t *dbin; uint16_t *pbin; uint32_t *pval[2]; int nd, np; } symmap_t;

struct t2_tx {
    t2_cfg_t c; t2_info_t I;
    int A;                                   /* Amplitude der Einheitszelle */
    /* FEC */
    bch_t bch; ldpc_t ldpc; bitil_t bil;
    uint8_t *bbscr; uint8_t crctab[256];
    uint8_t pkt[188]; int ppos;
    uint8_t *cw, *labb, *lab;
    uint16_t *L0inv; int nd_ci;
    uint32_t mtab[256];                      /* Re | Im<<16 je Zellwort (rotiert) */
    int16_t mre[256], mim[256];
    uint16_t *tim, *tile;                    /* TI-Speicher (ein TI-Block, 2-Byte-Codes), 8-Zeilen-Kachel */
    int *ci_p;                               /* Zell-Interleaver-Verschiebung P(r) */
    int *fec_ti;                             /* FEC-Blöcke je TI-Block */
    /* Rahmen */
    uint32_t *l1pre, **l1post;               /* L1-Zellen (pre: 1840, post je FRAME_IDX) */
    uint32_t *dummy;                         /* Dummy-Zellen (BB-PRBS, je Rahmen gleich) */
    int *ndata, *off;                        /* Datenzellen und Offset je Symbol */
    uint16_t *H[3][2];                       /* FI-Gather-Tabellen [P2/Daten/FC][l gerade/ungerade] */
    uint32_t *xtmp; int sl, sp, scap, addr; const uint32_t *curpost;   /* laufendes Symbol (x = Zellen vor dem FI) */
    int fidx;
    /* Ausgabe */
    symmap_t sm_p2, *sm_data, sm_fc; int ndphase;
    uint8_t pn[2624];
    fft16_t *fft;                            /* fft16 (2K/8K/32K) */
    t2fft_t *f8;                             /* Standard: Radix-8-IFFT (dvbt2_fft.c) */
    int xmode;                               /* Prüfpfad (--dump) */
    uint8_t ctab[256] __attribute__((aligned(16)));   /* NEON-Tabellen für codes2cells */
    int16_t p1[2048 * 2];
    int nstage;
    /* Prüfung/Messung */
    FILE *dump[6];
    int prof_on; double prof[8];
};

const t2_info_t *t2_tx_info(const t2_tx_t *t) { return &t->I; }
int t2_frame_samples(const t2_tx_t *t) { return 2048 + t->I.lf * (t->I.N + t->I.guard); }
const char *t2_ifft_backend(const t2_tx_t *t) { return t->f8 ? t2fft_backend() : fft16_backend(); }

/* ================================================================ BBFRAME (5.1, HEM) */
static void bb_init(t2_tx_t *t)
{
    for (int v = 0; v < 256; v++) {     /* CRC-8, g = x^8+x^7+x^6+x^4+x^2+1 (Anhang F) */
        uint8_t r = (uint8_t)v;
        for (int b = 0; b < 8; b++) r = (uint8_t)((r & 0x80) ? (r << 1) ^ 0xD5 : (r << 1));
        t->crctab[v] = r;
    }
    int kb = t->I.kbch;
    t->bbscr = al64((size_t)kb / 8 + 8);
    unsigned reg = 0x4A80;               /* 1 + x^14 + x^15, Startwert 100101010000000 */
    for (int i = 0; i < kb; i++) {
        unsigned out = ((reg >> 1) ^ reg) & 1;
        if (out) t->bbscr[i >> 3] |= (uint8_t)(0x80 >> (i & 7));
        reg = (reg >> 1) | (out << 14);
    }
    t->ppos = 188;
}
static void bbframe(t2_tx_t *t, t2_pkt_fn fn, void *ctx, uint8_t *cw)
{
    const int dfb = (t->I.kbch - 80) / 8;
    uint8_t *df = cw + 10;
    int fill = 0, syncd = -1;
    while (fill < dfb) {
        if (t->ppos == 188) {
            if (!fn || !fn(ctx, t->pkt)) { memcpy(t->pkt, "\x47\x1f\xff\x10", 4); memset(t->pkt + 4, 0xff, 184); }
            t->ppos = 1;                 /* Sync-Byte wird in HEM nicht übertragen */
            if (syncd < 0) syncd = fill * 8;
        }
        int take = 188 - t->ppos; if (take > dfb - fill) take = dfb - fill;
        memcpy(df + fill, t->pkt + t->ppos, (size_t)take);
        fill += take; t->ppos += take;
    }
    if (syncd < 0) syncd = 0xFFFF;
    int dfl = t->I.kbch - 80;
    cw[0] = 0xF0;                        /* TS, SIS, CCM, ISSYI=0, NPD=0, EXT=00 */
    cw[1] = 0;
    cw[2] = cw[3] = 0;                   /* HEM: ISSY (2 MSB) = 0 */
    cw[4] = (uint8_t)(dfl >> 8); cw[5] = (uint8_t)dfl;
    cw[6] = 0;                           /* ISSY (1 LSB) = 0 */
    cw[7] = (uint8_t)(syncd >> 8); cw[8] = (uint8_t)syncd;
    uint8_t r = 0; for (int i = 0; i < 9; i++) r = t->crctab[r ^ cw[i]];
    cw[9] = r ^ 0x01;                    /* CRC-8 ⊕ MODE (HEM = 1) */
    const int n = t->I.kbch / 8;
    uint32_t *w = (uint32_t *)cw; const uint32_t *s = (const uint32_t *)t->bbscr;
    int i = 0;
    for (; i + 4 <= n; i += 4) w[i >> 2] ^= s[i >> 2];
    for (; i < n; i++) cw[i] ^= t->bbscr[i];
}

/* ================================================================ Zell-Interleaver (6.4) */
static void cellil_init(t2_tx_t *t)
{
    const int n = t->I.ncells;
    int nd = 0; while ((1 << nd) < n) nd++;
    t->nd_ci = nd;
    uint16_t *L0 = malloc((size_t)n * 2);
    unsigned S = 0; int q = 0;
    for (int i = 0; i < (1 << nd); i++) {
        if (i < 2) S = 0;
        else if (i == 2) S = 1;
        else {
            unsigned fb;
            switch (nd) {
            case 11: fb = (S ^ (S >> 3)) & 1; break;
            case 12: fb = (S ^ (S >> 2)) & 1; break;
            case 13: fb = (S ^ (S >> 1) ^ (S >> 4) ^ (S >> 6)) & 1; break;
            case 14: fb = (S ^ (S >> 1) ^ (S >> 4) ^ (S >> 5) ^ (S >> 9) ^ (S >> 11)) & 1; break;
            default: fb = (S ^ (S >> 1) ^ (S >> 2) ^ (S >> 12)) & 1; break;
            }
            S = (S >> 1) | (fb << (nd - 2));
        }
        unsigned v = S | (unsigned)(i & 1) << (nd - 1);
        if ((int)v < n) L0[q++] = (uint16_t)v;
    }
    t->L0inv = malloc((size_t)n * 2);
    for (int i = 0; i < n; i++) t->L0inv[L0[i]] = (uint16_t)i;
    free(L0);
}
static int ci_shift(const t2_tx_t *t, int r)      /* P(r): bitumgekehrter Zähler, Werte ≥ Ncells übersprungen */
{
    int k = 0, p = 0;
    for (int i = 0; i <= r; i++) {
        do { p = 0; for (int j = 0; j < t->nd_ci; j++) if (k >> j & 1) p |= 1 << (t->nd_ci - 1 - j); k++; } while (p >= t->I.ncells);
    }
    return p;
}
/* Zell-Interleaver (6.4) mit verschobener Abbildung: statt der fertigen Zelle g_q (rotiert, Q-Verzögerung) wird der
   2-Byte-Code (Zellwort q) << 8 | (Zellwort q−1 bzw. q ohne Rotation) an die Stelle L_r(q) geschrieben; die Zellwerte
   entstehen erst beim Auslesen des Zeit-Interleavers (Tabellen mre/mim). Halbiert den TI-Speicherverkehr. */
static void map_cellil(const t2_tx_t *t, const uint8_t *lab, uint16_t *dst, int shift)
{
    const int n = t->I.ncells; const uint16_t *Li = t->L0inv;
    const int dq = t->c.rot ? 1 : 0;
    uint16_t *d = dst;
    for (int j = n - shift; j < n; j++) { int q = Li[j]; *d++ = (uint16_t)(lab[q] << 8 | lab[q - dq]); }
    for (int j = 0; j < n - shift; j++) { int q = Li[j]; *d++ = (uint16_t)(lab[q] << 8 | lab[q - dq]); }
}
static inline uint32_t code2cell(const t2_tx_t *t, unsigned c) { return (uint16_t)t->mre[c >> 8] | (uint32_t)(uint16_t)t->mim[c & 255] << 16; }
/* n Codes → Zellen. NEON: Re/Im-Tabellen (je 64 Einträge, Low/High-Byte getrennt) über vtbl4/vtbx4 */
static void codes2cells(const t2_tx_t *t, const uint16_t *src, uint32_t *dst, int n)
{
    int i = 0;
#if T2_NEON
    if (t->I.eta <= 6) {
        const uint8x8x4_t *T = (const uint8x8x4_t *)t->ctab;     /* [ReL0 ReL1 ReH0 ReH1 ImL0 ImL1 ImH0 ImH1] */
        const uint8x8_t k32 = vdup_n_u8(32);
        for (; i + 8 <= n; i += 8) {
            uint16x8_t c = vld1q_u16(src + i);
            uint8x8_t hi = vshrn_n_u16(c, 8), lo = vmovn_u16(c), hi2 = vsub_u8(hi, k32), lo2 = vsub_u8(lo, k32);
            uint8x8_t rl = vtbx4_u8(vtbl4_u8(T[0], hi), T[1], hi2), rh = vtbx4_u8(vtbl4_u8(T[2], hi), T[3], hi2);
            uint8x8_t il = vtbx4_u8(vtbl4_u8(T[4], lo), T[5], lo2), ih = vtbx4_u8(vtbl4_u8(T[6], lo), T[7], lo2);
            uint8x8x2_t r = vzip_u8(rl, rh), m = vzip_u8(il, ih);
            uint16x8x2_t o = {{ vreinterpretq_u16_u8(vcombine_u8(r.val[0], r.val[1])), vreinterpretq_u16_u8(vcombine_u8(m.val[0], m.val[1])) }};
            vst2q_u16((uint16_t *)(dst + i), o);
        }
    }
#endif
    for (; i < n; i++) dst[i] = code2cell(t, src[i]);
}

/* ================================================================ L1-Signalisierung (7.2, 7.3) */
typedef struct { uint8_t b[8192]; int n; } bits_t;
static void put(bits_t *B, uint64_t v, int w) { for (int i = w - 1; i >= 0; i--) B->b[B->n++] = (uint8_t)(v >> i & 1); }
static void crc32_append(bits_t *B)
{
    uint32_t crc = 0xFFFFFFFFu;
    for (int i = 0; i < B->n; i++) { int b = B->b[i] ^ (int)(crc >> 31); crc <<= 1; if (b) crc ^= 0x04C11DB7u; }
    put(B, crc, 32);
}
static void l1pre_bits(const t2_tx_t *t, bits_t *B)
{
    const t2_cfg_t *c = &t->c; const t2_info_t *I = &t->I;
    B->n = 0;
    put(B, 0x00, 8);                 /* TYPE: TS */
    put(B, c->ext, 1);               /* BWT_EXT */
    put(B, 0, 3);                    /* S1: T2_SISO */
    put(B, (unsigned)I->s2field1 << 1, 4);   /* S2: Feld 1 + nicht gemischt */
    put(B, 0, 1);                    /* L1_REPETITION_FLAG */
    put(B, (unsigned)c->gi, 3);
    put(B, 0, 4);                    /* PAPR: keine */
    put(B, (unsigned)c->l1mod, 4);
    put(B, 0, 2);                    /* L1_COD 1/2 */
    put(B, 0, 2);                    /* L1_FEC_TYPE LDPC 16K */
    put(B, (unsigned)I->dl1post, 18);
    put(B, (unsigned)I->l1post_info, 18);
    put(B, (unsigned)(c->pp - 1), 4);
    put(B, 0, 8);                    /* TX_ID_AVAILABILITY */
    put(B, (unsigned)c->cell_id, 16);
    put(B, (unsigned)c->network_id, 16);
    put(B, (unsigned)c->t2_system_id, 16);
    put(B, (unsigned)I->nt2, 8);
    put(B, (unsigned)I->ldata, 12);
    put(B, 0, 3);                    /* REGEN_FLAG */
    put(B, 0, 1);                    /* L1_POST_EXTENSION */
    put(B, 1, 3);                    /* NUM_RF */
    put(B, 0, 3);                    /* CURRENT_RF_IDX */
    put(B, 0, 4);                    /* T2_VERSION 1.1.1 */
    put(B, 0, 1); put(B, 0, 1); put(B, 0, 4);   /* L1_POST_SCRAMBLED, T2_BASE_LITE, RESERVED */
    crc32_append(B);
}
static void l1post_bits(const t2_tx_t *t, int fidx, bits_t *B)
{
    const t2_cfg_t *c = &t->c; const t2_info_t *I = &t->I;
    B->n = 0;
    /* konfigurierbar */
    put(B, 1, 15); put(B, 1, 8); put(B, 0, 4); put(B, 0, 8);         /* SUB_SLICES, NUM_PLP, NUM_AUX, AUX_CONFIG_RFU */
    put(B, 0, 3); put(B, c->freq_hz, 32);                             /* RF_IDX, FREQUENCY */
    put(B, 0, 8); put(B, 1, 3); put(B, 3, 5);                         /* PLP_ID, PLP_TYPE 1, PAYLOAD TS */
    put(B, 0, 1); put(B, 0, 3); put(B, 0, 8);                         /* FF_FLAG, FIRST_RF_IDX, FIRST_FRAME_IDX */
    put(B, (unsigned)c->plp_group_id, 8);
    put(B, (unsigned)c->rate, 3); put(B, (unsigned)c->mod, 3); put(B, (unsigned)c->rot, 1);
    put(B, c->fec16k ? 0 : 1, 2);                                     /* PLP_FEC_TYPE */
    put(B, (unsigned)I->nfec, 10);                                    /* PLP_NUM_BLOCKS_MAX */
    put(B, 1, 8);                                                     /* FRAME_INTERVAL */
    put(B, (unsigned)I->nti, 8); put(B, 0, 1);                        /* TIME_IL_LENGTH, TIME_IL_TYPE */
    put(B, 0, 1); put(B, 0, 1); put(B, 0, 11);                        /* IN_BAND_A/B, RESERVED_1 */
    put(B, 0, 2); put(B, 0, 1); put(B, 0, 1);                         /* PLP_MODE, STATIC_FLAG, STATIC_PADDING_FLAG (V1.1.1: 0) */
    put(B, 0, 2); put(B, 0, 30);                                      /* FEF_LENGTH_MSB, RESERVED_2 */
    /* dynamisch */
    put(B, (unsigned)fidx, 8); put(B, 0, 22); put(B, 0, 22); put(B, 0, 8); put(B, 0, 3); put(B, 0, 8);
    put(B, 0, 8); put(B, 0, 22); put(B, (unsigned)I->nfec, 10); put(B, 0, 8);   /* PLP_ID, PLP_START, PLP_NUM_BLOCKS, RES_2 */
    put(B, 0, 8);                                                     /* RESERVED_3 */
    crc32_append(B);
}
/* Verkürzung (7.3.2.2), BCH, LDPC, Punktierung (7.3.2.5), Entfernen der Füllbits → out (0/1), Rückgabe Länge */
static int l1_encode(const uint8_t *info, int ksig, int pre, int l1mod, int npunc, uint8_t *out)
{
    static const uint8_t PS_PRE[9] = {7,3,6,5,2,4,1,8,0};
    static const uint8_t PS_POST[3][20] = {
        {18,17,16,15,14,13,12,11,4,10,9,8,3,2,7,6,5,1,19,0},
        {18,17,16,15,14,13,12,11,4,10,9,8,7,3,2,1,6,5,19,0},
        {18,17,16,4,15,14,13,12,3,11,10,9,2,8,7,1,6,5,19,0}};
    static const uint8_t PP_PRE[36] = {27,13,29,32,5,0,11,21,33,20,25,28,18,35,8,3,9,31,22,24,7,14,17,4,2,26,16,34,19,10,12,23,1,6,30,15};
    static const uint8_t PP_POST[3][25] = {
        {6,4,18,9,13,8,15,20,5,17,2,24,10,22,12,3,16,23,1,14,0,21,19,7,11},
        {6,4,13,9,18,8,15,20,5,17,2,22,24,7,12,1,16,23,14,0,21,10,19,11,3},
        {6,15,13,10,3,17,21,8,5,19,2,23,16,24,7,18,1,12,20,0,4,14,9,11,22}};
    const int kbch = pre ? 3072 : 7032, kldpc = pre ? 3240 : 7200, ngroup = kldpc / 360;
    const int mi = l1mod <= T2_L1_QPSK ? 0 : l1mod == T2_L1_16QAM ? 1 : 2;
    const uint8_t *ps = pre ? PS_PRE : PS_POST[mi], *pp = pre ? PP_PRE : PP_POST[mi];
    uint8_t padm[7200] = { 0 };
    int npad = (ksig > 0 && ksig <= 360) ? ngroup - 1 : (kbch - ksig) / 360;
    for (int j = 0; j < npad; j++) {
        int g = ps[j], len = g == ngroup - 1 ? 360 - (kldpc - kbch) : 360;
        memset(padm + 360 * g, 1, (size_t)len);
    }
    {
        int g = ps[npad], len = g == ngroup - 1 ? 360 - (kldpc - kbch) : 360;
        int extra = npad == ngroup - 1 ? 360 - ksig : kbch - ksig - 360 * npad;
        if (extra > len) extra = len;
        if (extra > 0) memset(padm + 360 * g + len - extra, 1, (size_t)extra);
    }
    uint8_t m[16200]; int k = 0;
    for (int i = 0; i < kbch; i++) m[i] = padm[i] ? 0 : info[k++];
    if (k != ksig) { fprintf(stderr, "dvbt2: L1-Verkürzung inkonsistent (%d/%d)\n", k, ksig); abort(); }
    bch_t bch; bch_init(&bch, kbch, 12, 1);
    uint8_t cwb[16200];
    bch_ref(&bch, m, kbch, cwb); bch_free(&bch);
    ldpc_t L; ldpc_init(&L, pre ? -1 : T2_R1_2, 1, kldpc);
    uint8_t full[16200]; ldpc_ref(&L, cwb, full);
    const int q = L.q, M = L.M; ldpc_free(&L);
    uint8_t punc[12960] = { 0 };
    int ng = npunc / 360;
    for (int j = 0; j < ng; j++) for (int s = 0; s < 360; s++) punc[pp[j] + q * s] = 1;
    for (int s = 0; s < npunc - 360 * ng; s++) punc[pp[ng] + q * s] = 1;
    int n = 0;
    for (int i = 0; i < kbch; i++) if (!padm[i]) out[n++] = full[i];
    for (int i = kbch; i < kldpc; i++) out[n++] = full[i];
    for (int i = 0; i < M; i++) if (!punc[i]) out[n++] = full[kldpc + i];
    return n;
}
static void l1_cells(t2_tx_t *t, const uint8_t *bits, int nbits, int l1mod, uint32_t *cells)
{
    const int A = t->A;
    if (l1mod == T2_L1_BPSK) { for (int i = 0; i < nbits; i++) cells[i] = iq32(bits[i] ? -A : A, 0); return; }
    int mod = l1mod == T2_L1_QPSK ? T2_QPSK : l1mod == T2_L1_16QAM ? T2_16QAM : T2_64QAM, eta = ETA[mod];
    uint8_t v[16200];
    if (mod != T2_QPSK) {   /* Bit-Interleaver Tab. 44: spaltenweise schreiben, zeilenweise lesen */
        int nc = 2 * eta, nr = nbits / nc;
        for (int i = 0; i < nbits; i++) v[(i % nr) * nc + i / nr] = bits[i];
    } else memcpy(v, bits, (size_t)nbits);
    int nsub = mod == T2_QPSK ? 2 : 2 * eta;
    const uint8_t *dm = mod == T2_16QAM ? DM16A : DM64A;
    int nc = 0;
    for (int w = 0; w < nbits / nsub; w++) {
        int b[12];
        for (int i = 0; i < nsub; i++) b[mod == T2_QPSK ? i : dm[i]] = v[w * nsub + i];
        for (int ci = 0; ci < nsub / eta; ci++) {
            int x = 0; for (int i = 0; i < eta; i++) x = x << 1 | b[ci * eta + i];
            double re, im; const_point(mod, x, &re, &im);
            cells[nc++] = iq32((int)lrint(A * re), (int)lrint(A * im));
        }
    }
}
static void l1_init(t2_tx_t *t)
{
    const t2_info_t *I = &t->I;
    bits_t *B = malloc(sizeof *B);
    uint8_t *cb = malloc(16200);
    l1pre_bits(t, B);
    int n = l1_encode(B->b, 200, 1, T2_L1_BPSK, 11488, cb);
    if (n != 1840) { fprintf(stderr, "dvbt2: L1-pre %d Bits\n", n); abort(); }
    t->l1pre = al64(1840 * 4);
    l1_cells(t, cb, n, T2_L1_BPSK, t->l1pre);
    int ks, nb, np, npu;
    l1post_sizes(t->c.l1mod, I->np2, &ks, &nb, &np, &npu);
    t->l1post = calloc((size_t)I->nt2, sizeof *t->l1post);
    for (int f = 0; f < I->nt2; f++) {
        l1post_bits(t, f, B);
        if (B->n != L1POST_INFO + 32) { fprintf(stderr, "dvbt2: L1-post %d Bits\n", B->n); abort(); }
        int kpost = ks * nb; while (B->n < kpost) B->b[B->n++] = 0;     /* L1_PADDING */
        t->l1post[f] = al64((size_t)I->dl1post * 4);
        int nc = np / (I->dl1post / nb ? np / (I->dl1post / nb) : 1);
        (void)nc;
        for (int k = 0; k < nb; k++) {
            int m = l1_encode(B->b + k * ks, ks, 0, t->c.l1mod, npu, cb);
            if (m != np) { fprintf(stderr, "dvbt2: L1-post-Block %d Bits (erwartet %d)\n", m, np); abort(); }
            l1_cells(t, cb, m, t->c.l1mod, t->l1post[f] + (size_t)k * (I->dl1post / nb));
        }
    }
    free(B); free(cb);
}

/* ================================================================ Frequenz-Interleaver (8.5) */
static void fi_perm(int N, int ndata, int odd, uint16_t *G)
{
    static const uint8_t P1K[2][9] = {{8,7,6,5,0,1,2,3,4},{6,8,7,4,1,0,5,2,3}};
    /* Tab. 53: R'-Bit i (Spalte von rechts nach links 0..Nr-2) → R-Bit; hier als Ziel je Quellbit (R'[j] → R[perm[j]]) */
    static const uint8_t T1K[2][9]   = {{8,7,6,5,0,4,3,2,1},{6,7,4,1,0,5,2,3,8}};
    static const uint8_t T2K[2][10]  = {{4,3,9,6,2,8,1,5,7,0},{6,9,4,8,5,1,0,7,2,3}};
    static const uint8_t T4K[2][11]  = {{6,3,0,9,4,2,1,8,5,10,7},{5,9,1,4,3,0,8,10,7,2,6}};
    static const uint8_t T8K[2][12]  = {{7,1,4,2,9,6,8,10,0,3,11,5},{11,4,9,3,1,2,5,0,6,7,10,8}};
    static const uint8_t T16K[2][13] = {{9,7,6,10,12,5,1,11,0,2,3,4,8},{6,8,10,12,2,0,4,1,11,3,5,9,7}};
    static const uint8_t T32K[14]    = {7,13,3,4,9,2,12,11,1,8,10,0,5,6};
    (void)P1K; (void)T1K;
    int nr = 0; while ((1 << nr) < N) nr++;
    const uint8_t *pm;
    switch (N) {
    case 1024: {   /* Tab. 53(a): R'-Bitpositionen 8..0 → R (H0): 4 3 2 1 0 5 6 7 8; (H1): 3 2 5 0 1 4 7 8 6 */
        static const uint8_t a0[9] = {8,7,6,5,0,1,2,3,4}, a1[9] = {6,8,7,4,1,0,5,2,3};
        pm = odd ? a1 : a0; break; }
    case 2048: pm = T2K[odd]; break;
    case 4096: pm = T4K[odd]; break;
    case 8192: pm = T8K[odd]; break;
    case 16384: pm = T16K[odd]; break;
    default: pm = T32K; break;
    }
    unsigned Rp = 0; int p = 0;
    uint16_t *Hs = malloc((size_t)N * 2);
    for (int i = 0; i < N; i++) {
        if (i < 2) Rp = 0;
        else if (i == 2) Rp = 1;
        else {
            unsigned fb;
            switch (N) {
            case 1024: fb = (Rp ^ (Rp >> 4)) & 1; break;
            case 2048: fb = (Rp ^ (Rp >> 3)) & 1; break;
            case 4096: fb = (Rp ^ (Rp >> 2)) & 1; break;
            case 8192: fb = (Rp ^ (Rp >> 1) ^ (Rp >> 4) ^ (Rp >> 6)) & 1; break;
            case 16384: fb = (Rp ^ (Rp >> 1) ^ (Rp >> 4) ^ (Rp >> 5) ^ (Rp >> 9) ^ (Rp >> 11)) & 1; break;
            default: fb = (Rp ^ (Rp >> 1) ^ (Rp >> 2) ^ (Rp >> 12)) & 1; break;
            }
            Rp = (Rp >> 1) | (fb << (nr - 2));
        }
        unsigned R = 0;
        for (int j = 0; j < nr - 1; j++) if (Rp >> j & 1) R |= 1u << pm[j];
        unsigned h = (unsigned)(i & 1) << (nr - 1) | R;
        if ((int)h < ndata) Hs[p++] = (uint16_t)h;
    }
    if (p != ndata) { fprintf(stderr, "dvbt2: FI %d/%d\n", p, ndata); abort(); }
    if (N == 32768 && !odd) for (int q = 0; q < ndata; q++) G[Hs[q]] = (uint16_t)q;   /* a[H(p)] = x[p] → Gather H⁻¹ */
    else memcpy(G, Hs, (size_t)ndata * 2);                                             /* a[p] = x[H(p)] */
    free(Hs);
}

/* ================================================================ Ausgabe: Trägertabellen, P1, IFFT */
static void prbs_wk(uint8_t *w, int n)   /* X^11 + X^2 + 1, Start alle Einsen */
{
    unsigned sr = 0x7FF;
    for (int i = 0; i < n; i++) { w[i] = sr & 1; unsigned b = (sr ^ (sr >> 2)) & 1; sr >>= 1; if (b) sr |= 0x400; }
}
static void symmap_build(t2_tx_t *t, symmap_t *s, int kind, int l, const uint8_t *wk)
{
    const t2_cfg_t *c = &t->c; const t2_info_t *I = &t->I;
    const int K = I->ktotal, N = I->N, kc = (K - 1) / 2, fi = fft_idx(c->fft), off = c->ext ? 0 : I->kext;
    uint8_t *ty = malloc((size_t)K);
    carrier_map(c, I, kind, l, ty);
    double asp = c->pp <= 2 ? 4.0 / 3 : c->pp <= 4 ? 7.0 / 4 : 7.0 / 3;
    double acp = fi <= 1 ? 4.0 / 3 : fi == 2 ? 4.0 * sqrt(2.0) / 3 : 8.0 / 3;
    double ap2 = fi == 5 ? sqrt(37.0) / 5 : sqrt(31.0) / 5;
    int nd = 0, np = 0;
    for (int k = 0; k < K; k++) if (ty[k] == CT_DATA) nd++; else np++;
    s->nd = nd; s->np = np;
    s->dbin = malloc((size_t)nd * 2); s->pbin = malloc((size_t)np * 2);
    s->pval[0] = malloc((size_t)np * 4); s->pval[1] = malloc((size_t)np * 4);
    nd = np = 0;
    for (int k = 0; k < K; k++) {
        int bin = (k - kc + N) % N;
        if (ty[k] == CT_DATA) { s->dbin[nd++] = (uint16_t)bin; continue; }
        double a = ty[k] == CT_SP || ty[k] == CT_FC ? asp : ty[k] == CT_CP ? acp : ty[k] == CT_P2 ? ap2 : 0;
        int v = (int)lrint(t->A * a) * (wk[k + off] ? -1 : 1);
        s->pbin[np] = (uint16_t)bin; s->pval[0][np] = iq32(v, 0); s->pval[1][np] = iq32(-v, 0); np++;
    }
    free(ty);
}
static void symmap_free(symmap_t *s) { free(s->dbin); free(s->pbin); free(s->pval[0]); free(s->pval[1]); }

static void p1_init(t2_tx_t *t, double g)
{
    int seq[384], k = 0;
    const char *hx[3] = { t2_p1_s1[0], t2_p1_s2[t->I.s2field1 << 1], t2_p1_s1[0] };
    for (int part = 0; part < 3; part++)
        for (const char *p = hx[part]; *p; p++) { int v = (*p <= '9') ? *p - '0' : *p - 'A' + 10; for (int b = 3; b >= 0; b--) seq[k++] = v >> b & 1; }
    double mss[384]; int prev = 1; unsigned sr = 0x4E46;   /* PRBS 5.2.4, Start 100111001000110 */
    for (int i = 0; i < 384; i++) {
        int d = seq[i] ? -prev : prev; prev = d;
        unsigned out = ((sr >> 1) ^ sr) & 1; sr = (sr >> 1) | (out << 14);
        mss[i] = d * (out ? -1.0 : 1.0);
    }
    double ar[1024], ai[1024], br[1024], bi[1024];
    for (int n = 0; n < 1024; n++) {
        double sr_ = 0, si_ = 0;
        for (int i = 0; i < 384; i++) { double ph = 2 * M_PI * (t2_p1_carriers[i] - 426) * n / 1024.0; sr_ += mss[i] * cos(ph); si_ += mss[i] * sin(ph); }
        ar[n] = sr_ / sqrt(384.0); ai[n] = si_ / sqrt(384.0);
        double c = cos(2 * M_PI * n / 1024.0), s = sin(2 * M_PI * n / 1024.0);
        br[n] = ar[n] * c - ai[n] * s; bi[n] = ar[n] * s + ai[n] * c;
    }
    int16_t *o = t->p1;
    for (int n = 0; n < 542; n++) { *o++ = sat16(g * br[n]); *o++ = sat16(g * bi[n]); }
    for (int n = 0; n < 1024; n++) { *o++ = sat16(g * ar[n]); *o++ = sat16(g * ai[n]); }
    for (int n = 542; n < 1024; n++) { *o++ = sat16(g * br[n]); *o++ = sat16(g * bi[n]); }
}

void t2_render_p1(t2_tx_t *t, int16_t *out) { memcpy(out, t->p1, sizeof t->p1); }

void t2_render_symbol(t2_tx_t *t, const t2_frame_t *f, int l, int16_t *out)
{
    const int16_t *v = (const int16_t *)(f->bins + (size_t)l * t->I.N);
    if (t->f8) t2fft_ifft(t->f8, v, out, t->I.guard);
    else fft16_ifft_ofdm(t->fft, v, out, t->I.guard);
}

/* ================================================================ Rahmen kodieren (Thread A) */
static void dump_ldpc_natural(t2_tx_t *t, const uint8_t *cw)
{
    const int n = t->I.nldpc, k = t->I.nbch, q = (n - k) / 360;
    uint8_t *o = calloc((size_t)n / 8, 1);
    memcpy(o, cw, (size_t)k / 8);
    for (int i = 0; i < n - k; i++) {           /* natürlich λ[K+Q·s+t] = u[K+360t+s] */
        int src = i;
        if (t->c.mod != T2_QPSK) { int s = i / q, tt = i % q; src = 360 * tt + s; }
        setbit(o, k + i, getbit(cw, k + src));
    }
    fwrite(o, 1, (size_t)n / 8, t->dump[0]); free(o);
}

static double tnow(const t2_tx_t *t) { return t->prof_on ? dvb_now() : 0; }

static inline int sym_kind(const t2_info_t *I, int l) { return l < I->np2 ? 0 : (I->fc && l == I->lf - 1) ? 2 : 1; }
static void sym_flush(t2_tx_t *t, t2_frame_t *f);
/* Symbol t->sl beginnen: x = Zellen vor dem Frequenz-Interleaver; P2: L1-pre/L1-post im Zickzack vorbelegen (8.3.5) */
static void sym_begin(t2_tx_t *t, t2_frame_t *f)
{
    const t2_info_t *I = &t->I;
    if (t->sl >= I->lf) return;
    const int l = t->sl, kind = sym_kind(I, l), nd = t->ndata[l];
    int p = 0;
    if (kind == 0) {
        uint32_t *x = t->xtmp;
        const int a = I->dl1pre / I->np2, b = I->dl1post / I->np2;
        for (int i = 0; i < a; i++) x[p++] = t->l1pre[i * I->np2 + l];
        for (int i = 0; i < b; i++) x[p++] = t->curpost[i * I->np2 + l];
    }
    t->sp = p;
    t->scap = kind == 2 ? I->cfc : nd;
    if (t->sp == t->scap) sym_flush(t, f);
}
/* Symbol fertig: FC-Restzellen 0 (8.3.9), Frequenz-Interleaver (8.5) als Gather in Trägerreihenfolge + Piloten (9.2),
   direkt in die IFFT-Bins. x wird zufällig gelesen, die Bins aufsteigend geschrieben – zufällige Schreibzugriffe kosten auf
   dem A9 etwa das Doppelte (gemessen: 43 gegen 22 ns je Zelle). */
static void sym_flush(t2_tx_t *t, t2_frame_t *f)
{
    const t2_info_t *I = &t->I;
    const int l = t->sl, kind = sym_kind(I, l), nd = t->ndata[l];
    double tf0 = tnow(t);
    const symmap_t *s = kind == 0 ? &t->sm_p2 : kind == 2 ? &t->sm_fc : &t->sm_data[l % t->ndphase];
    const uint16_t *G = t->H[kind][l & 1], *db = s->dbin;
    uint32_t *v = f->bins + (size_t)l * I->N;
    int j = 0;
    uint32_t *x = t->xtmp;
    while (t->sp < nd) x[t->sp++] = 0;
    if (t->dump[4]) fwrite(x, 4, (size_t)nd, t->dump[4]);
    if (t->dump[5]) for (j = 0; j < nd; j++) fwrite(&x[G[j]], 4, 1, t->dump[5]);
    for (j = 0; j + 4 <= nd; j += 4) {
        uint32_t a0 = x[G[j]], a1 = x[G[j + 1]], a2 = x[G[j + 2]], a3 = x[G[j + 3]];
        v[db[j]] = a0; v[db[j + 1]] = a1; v[db[j + 2]] = a2; v[db[j + 3]] = a3;
    }
    for (; j < nd; j++) v[db[j]] = x[G[j]];
    const uint16_t *pb = s->pbin; const uint32_t *pv = s->pval[t->pn[l]];
    for (int i = 0; i < s->np; i++) v[pb[i]] = pv[i];
    if (t->prof_on) t->prof[7] += tnow(t) - tf0;
    t->sl++;
    sym_begin(t, f);
}
/* Strom-Zellen (Dummy) anhängen */
static void push_cells(t2_tx_t *t, t2_frame_t *f, const uint32_t *src, int n)
{
    while (n > 0) {
        if (t->sl >= t->I.lf) { fprintf(stderr, "dvbt2: Rahmenüberlauf\n"); abort(); }
        int take = t->scap - t->sp; if (take > n) take = n;
        memcpy(t->xtmp + t->sp, src, (size_t)take * 4);
        t->sp += take; src += take; n -= take; t->addr += take;
        if (t->sp == t->scap) sym_flush(t, f);
    }
}
/* PLP-Codes aus dem Zeit-Interleaver anhängen */
static void push_codes(t2_tx_t *t, t2_frame_t *f, const uint16_t *src, int n)
{
    while (n > 0) {
        if (t->sl >= t->I.lf) { fprintf(stderr, "dvbt2: Rahmenüberlauf\n"); abort(); }
        int take = t->scap - t->sp; if (take > n) take = n;
        codes2cells(t, src, t->xtmp + t->sp, take);
        t->sp += take; src += take; n -= take; t->addr += take;
        if (t->sp == t->scap) sym_flush(t, f);
    }
}

/* Zeit-Interleaver zeilenweise lesen (6.5.3, Nr = Ncells/5, Nc = 5·NFEC_TI), in 8-Zeilen-Kacheln direkt in die Symbole */
static void ti_drain(t2_tx_t *t, t2_frame_t *f, int nb)
{
    const int nr = t->I.ncells / 5, ncol = 5 * nb;
    const uint16_t *mem = t->tim; uint16_t *tile = t->tile;
    for (int r = 0; r < nr; r += 8) {
        const int rows = nr - r < 8 ? nr - r : 8;
        if (rows == 8)
            for (int c = 0; c < ncol; c++) {
                const uint16_t *s = mem + (size_t)c * nr + r;
                __builtin_prefetch(s + 8 * nr);
                tile[c] = s[0]; tile[c + ncol] = s[1]; tile[c + 2 * ncol] = s[2]; tile[c + 3 * ncol] = s[3];
                tile[c + 4 * ncol] = s[4]; tile[c + 5 * ncol] = s[5]; tile[c + 6 * ncol] = s[6]; tile[c + 7 * ncol] = s[7];
            }
        else
            for (int k = 0; k < rows; k++) for (int c = 0; c < ncol; c++) tile[k * ncol + c] = mem[(size_t)c * nr + r + k];
        if (t->dump[3]) for (int i = 0; i < rows * ncol; i++) { uint32_t v = code2cell(t, tile[i]); fwrite(&v, 4, 1, t->dump[3]); }
        push_codes(t, f, tile, rows * ncol);
    }
}

void t2_encode_frame(t2_tx_t *t, t2_pkt_fn fn, void *ctx, t2_frame_t *f)
{
    const t2_info_t *I = &t->I;
    const int nc = I->ncells;
    double a = 0, b;
    t->sl = 0; t->addr = 0; t->curpost = t->l1post[t->fidx];
    sym_begin(t, f);
    for (int s = 0; s < I->nti; s++) {
        const int nb = t->fec_ti[s];
        for (int r = 0; r < nb; r++) {
            a = tnow(t); bbframe(t, fn, ctx, t->cw);
            b = tnow(t); if (t->prof_on) t->prof[0] += b - a; a = b;
            bch_encode(&t->bch, t->cw);
            b = tnow(t); if (t->prof_on) t->prof[1] += b - a; a = b;
            ldpc_encode(&t->ldpc, t->cw, t->c.mod != T2_QPSK);
            b = tnow(t); if (t->prof_on) t->prof[2] += b - a; a = b;
            if (t->dump[0]) dump_ldpc_natural(t, t->cw);
            bitil_run(&t->bil, t->cw, t->lab);
            t->lab[-1] = t->lab[nc - 1];
            b = tnow(t); if (t->prof_on) t->prof[3] += b - a; a = b;
            if (t->dump[1]) fwrite(t->lab, 1, (size_t)nc, t->dump[1]);
            if (t->dump[2]) {
                for (int q = 0; q < nc; q++) {
                    uint32_t v = (uint16_t)t->mre[t->lab[q]] | (uint32_t)(uint16_t)t->mim[t->lab[t->c.rot ? q - 1 : q]] << 16;
                    fwrite(&v, 4, 1, t->dump[2]);
                }
            }
            map_cellil(t, t->lab, t->tim + (size_t)r * nc, t->ci_p[r]);
            b = tnow(t); if (t->prof_on) t->prof[4] += b - a; a = b;
        }
        ti_drain(t, f, nb);
        b = tnow(t); if (t->prof_on) t->prof[5] += b - a;
    }
    a = tnow(t);
    push_cells(t, f, t->dummy, I->ndummy);                     /* Dummy-Zellen (8.3.8) */
    if (t->sl != I->lf || t->addr != I->dplp) { fprintf(stderr, "dvbt2: Rahmen inkonsistent (l %d, Adr. %d/%d)\n", t->sl, t->addr, I->dplp); abort(); }
    f->fidx = t->fidx;
    t->fidx = (t->fidx + 1) % I->nt2;
    if (t->prof_on) t->prof[6] += tnow(t) - a;
}
int t2_frame_index(const t2_frame_t *f) { return f->fidx; }

t2_frame_t *t2_frame_alloc(t2_tx_t *t)
{
    t2_frame_t *f = calloc(1, sizeof *f);
    f->bins = al64((size_t)t->I.lf * t->I.N * 4 + 64);   /* Bins außerhalb des Bandes bleiben 0 */
    return f;
}
void t2_frame_free(t2_frame_t *f) { if (f) { free(f->bins); free(f); } }

/* ================================================================ Anlegen */
t2_tx_t *t2_tx_create(const t2_cfg_t *cfg, double rms)
{
    init_rev();
    t2_tx_t *t = calloc(1, sizeof *t);
    t->c = *cfg;
    char err[200];
    if (t2_cfg_resolve(&t->c, &t->I, err, sizeof err)) { fprintf(stderr, "%s\n", err); free(t); return NULL; }
    t2_info_t *I = &t->I;
    /* IFFT */
    if (T2_FFT8_ALL || I->N == 1024 || I->N == 4096 || I->N == 16384) {   /* fft16 kann nur 2·4^k; Radix-8 für 1K/4K/16K */
        t->f8 = t2fft_create(I->N);
        t->nstage = t2fft_shift_bits(t->f8);
    } else { t->fft = fft16_create(I->N); t->nstage = fft16_shift_bits(t->fft); }
    if (!t->fft && !t->f8) { fprintf(stderr, "dvbt2: IFFT %d nicht verfügbar\n", I->N); free(t); return NULL; }
    I->ifft_scale = t->nstage;
    /* Pegel: mittlere Leistung je Symbol ≈ A²·2^-2S·27·K/25 (Normierung 5/√(27K)) */
    t->A = (int)lrint(rms * pow(2.0, t->nstage) / sqrt(27.0 * I->ktotal / 25.0));
    double g = t->A * pow(2.0, -t->nstage) / (5.0 / sqrt(27.0 * I->ktotal));
    /* FEC */
    bch_init(&t->bch, I->kbch, t->c.fec16k ? 12 : BCHT_N[t->c.rate], t->c.fec16k);
    if (ldpc_init(&t->ldpc, t->c.rate, t->c.fec16k, I->nbch) < 0) { fprintf(stderr, "dvbt2: LDPC-Tabelle\n"); return NULL; }
    bitil_init(&t->bil, t->c.mod, t->c.rate, t->c.fec16k);
    bb_init(t);
    t->cw = al64((size_t)I->nldpc / 8 + 64);
    t->labb = al64((size_t)I->ncells + 64); t->lab = t->labb + 16;
    cellil_init(t);
    double ang = t->c.rot ? rot_angle(t->c.mod) : 0, ca = cos(ang), sa = sin(ang);
    for (int v = 0; v < (1 << I->eta); v++) {
        double re, im; const_point(t->c.mod, v, &re, &im);
        t->mre[v] = (int16_t)lrint(t->A * (re * ca - im * sa)); t->mim[v] = (int16_t)lrint(t->A * (re * sa + im * ca));
        t->mtab[v] = iq32(t->mre[v], t->mim[v]);
    }
    for (int v = 0; v < 64 && v < (1 << I->eta); v++) {
        int h = v >> 5, k = v & 31;
        t->ctab[0 + 32 * h + k] = (uint8_t)t->mre[v]; t->ctab[64 + 32 * h + k] = (uint8_t)((uint16_t)t->mre[v] >> 8);
        t->ctab[128 + 32 * h + k] = (uint8_t)t->mim[v]; t->ctab[192 + 32 * h + k] = (uint8_t)((uint16_t)t->mim[v] >> 8);
    }
    t->fec_ti = malloc((size_t)I->nti * sizeof(int));
    for (int s = 0; s < I->nti; s++) t->fec_ti[s] = I->nfec / I->nti + (s >= I->nti - I->nfec % I->nti ? 1 : 0);
    t->tim = al64((size_t)I->nfec_ti_max * I->ncells * 2 + 64);
    t->tile = al64((size_t)8 * 5 * I->nfec_ti_max * 2 + 64);
    t->ci_p = malloc((size_t)I->nfec_ti_max * sizeof(int));
    for (int r = 0; r < I->nfec_ti_max; r++) t->ci_p[r] = ci_shift(t, r);
    /* L1, Dummy */
    l1_init(t);
    t->dummy = al64((size_t)(I->ndummy + 1) * 4);
    { unsigned reg = 0x4A80; for (int i = 0; i < I->ndummy; i++) { unsigned out = ((reg >> 1) ^ reg) & 1; reg = (reg >> 1) | (out << 14); t->dummy[i] = iq32(out ? -t->A : t->A, 0); } }
    /* Symbole */
    t->ndata = malloc((size_t)I->lf * sizeof(int)); t->off = malloc((size_t)(I->lf + 1) * sizeof(int));
    int o = 0;
    for (int l = 0; l < I->lf; l++) {
        t->ndata[l] = l < I->np2 ? I->cp2 : (I->fc && l == I->lf - 1) ? I->nfc : I->cdata;
        t->off[l] = o; o += t->ndata[l];
    }
    t->off[I->lf] = o;
    if (o != I->np2 * I->cp2 + (I->fc ? (I->ldata - 1) * I->cdata + I->nfc : I->ldata * I->cdata)) abort();
    I->ctot = o;   /* Zellen im Rahmenpuffer (inkl. unmodulierter FC-Zellen) */
    t->xtmp = al64((size_t)(I->N + 64) * 4);
    int nds[3] = { I->cp2, I->cdata, I->fc ? I->nfc : 0 };
    for (int k = 0; k < 3; k++) for (int odd = 0; odd < 2; odd++) {
        if (!nds[k]) continue;
        t->H[k][odd] = malloc((size_t)nds[k] * 2);
        fi_perm(I->N, nds[k], odd, t->H[k][odd]);
    }
    /* Trägertabellen + Prüfung der Zellzahlen gegen Tab. 47–49 */
    uint8_t *wk = malloc(32768);
    prbs_wk(wk, 32768);
    for (int l = 0; l < I->lf && l < 2624; l++) t->pn[l] = t2_pn_seq[l >> 3] >> (7 - (l & 7)) & 1;
    symmap_build(t, &t->sm_p2, 0, 0, wk);
    t->ndphase = DYT[t->c.pp - 1];
    t->sm_data = calloc((size_t)t->ndphase, sizeof *t->sm_data);
    for (int ph = 0; ph < t->ndphase; ph++) symmap_build(t, &t->sm_data[ph], 1, ph, wk);
    if (I->fc) symmap_build(t, &t->sm_fc, 2, 0, wk);
    free(wk);
    int bad = t->sm_p2.nd != I->cp2 || (I->fc && t->sm_fc.nd != I->nfc);
    for (int ph = 0; ph < t->ndphase; ph++) bad |= t->sm_data[ph].nd != I->cdata;
    if (bad) {
        fprintf(stderr, "dvbt2: Trägerzählung weicht von Tab. 47–49 ab (P2 %d/%d, Daten %d/%d, FC %d/%d)\n",
                t->sm_p2.nd, I->cp2, t->sm_data[0].nd, I->cdata, I->fc ? t->sm_fc.nd : 0, I->nfc);
        return NULL;
    }
    p1_init(t, g);
    return t;
}

void t2_tx_free(t2_tx_t *t)
{
    if (!t) return;
    for (int i = 0; i < 6; i++) if (t->dump[i]) fclose(t->dump[i]);
    bch_free(&t->bch); ldpc_free(&t->ldpc);
    free(t->bbscr); free(t->cw); free(t->labb); free(t->L0inv); free(t->tim); free(t->tile); free(t->ci_p); free(t->fec_ti);
    free(t->l1pre); for (int f = 0; f < t->I.nt2; f++) free(t->l1post[f]); free(t->l1post); free(t->dummy);
    free(t->ndata); free(t->off); free(t->xtmp);
    for (int k = 0; k < 3; k++) for (int o = 0; o < 2; o++) free(t->H[k][o]);
    symmap_free(&t->sm_p2); for (int ph = 0; ph < t->ndphase; ph++) symmap_free(&t->sm_data[ph]); free(t->sm_data);
    if (t->I.fc) symmap_free(&t->sm_fc);

    fft16_free(t->fft); t2fft_free(t->f8);
    free(t);
}

int t2_set_dump(t2_tx_t *t, const char *prefix)
{
    static const char *ext[6] = { "ldpc", "lab", "map", "ti", "frm", "fi" };
    char fn[512];
    for (int i = 0; i < 6; i++) {
        snprintf(fn, sizeof fn, "%s.%s", prefix, ext[i]);
        if (!(t->dump[i] = fopen(fn, "wb"))) { perror(fn); return -1; }
    }
    t->xmode = 1;
    return 0;
}

void t2_profile(t2_tx_t *t, int on, double pr[8])
{
    t->prof_on = on;
    if (pr) memcpy(pr, t->prof, sizeof t->prof);
}


/* IFFT-Mikrobenchmark: Radix-8 (dvbt2_fft.c) gegen fft16 (bzw. DIF + fft16 bei 1K/4K/16K), gleiche Eingangsdaten */
void t2_bench_ifft(int N, double secs)
{
    int16_t *in = al64((size_t)N * 4), *out = al64((size_t)(N + N / 4) * 4 + 64);
    uint32_t rs = 7;
    for (int k = 0; k < N * 85 / 100; k++) { int b = (k - N * 85 / 200 + N) % N; rs = rs * 1103515245u + 12345u; in[2 * b] = (int16_t)((rs >> 16) % 12000 - 6000); in[2 * b + 1] = (int16_t)((rs >> 3) % 12000 - 6000); }
    t2fft_t *f = t2fft_create(N);
    long n = 0; double t0 = dvb_now(), t1;
    do { t2fft_ifft(f, in, out, N / 8); n++; t1 = dvb_now(); } while (t1 - t0 < secs);
    double r8 = (t1 - t0) / n * 1e6;
#if T2FFT_PROF
    { extern double t2fft_stage_s[8]; for (int k = 0; k < 8; k++) fprintf(stderr, "stage %d: %.1f us\n", k, t2fft_stage_s[k] / n * 1e6); }
#endif
    double r4 = 0;
    if (N == 2048 || N == 8192 || N == 32768) {
        fft16_t *g = fft16_create(N); n = 0; t0 = dvb_now();
        do { fft16_ifft_ofdm(g, in, out, N / 8); n++; t1 = dvb_now(); } while (t1 - t0 < secs);
        r4 = (t1 - t0) / n * 1e6; fft16_free(g);
    }
    fprintf(stderr, "{\"bench_ifft\":%d,\"radix8_us\":%.1f,\"fft16_us\":%.1f,\"backend\":\"%s\"}\n", N, r8, r4, t2fft_backend());
    t2fft_free(f); free(in); free(out);
}

/* ================================================================ Selbsttest */
int t2_selftest(int verbose)
{
    init_rev();
    int fail = 0; uint32_t rs = 12345;
    for (int shortf = 0; shortf < 2; shortf++)
        for (int mod = 0; mod < 4; mod++)
            for (int rate = 0; rate < 6; rate++) {
                int nldpc = shortf ? 16200 : 64800, kbch = shortf ? KBCH_S[rate] : KBCH_N[rate], nbch = shortf ? NBCH_S[rate] : NBCH_N[rate];
                bch_t bc; bch_init(&bc, kbch, shortf ? 12 : BCHT_N[rate], shortf);
                ldpc_t L; if (ldpc_init(&L, rate, shortf, nbch) < 0) { fail++; continue; }
                bitil_t B; bitil_init(&B, mod, rate, shortf);
                uint8_t *cw = al64((size_t)nldpc / 8 + 64), *bits = malloc((size_t)nldpc), *ref = malloc((size_t)nldpc);
                uint8_t *lab = malloc((size_t)B.ncells + 8), *lab2 = malloc((size_t)B.ncells + 8);
                for (int i = 0; i < kbch; i++) { rs = rs * 1103515245u + 12345u; bits[i] = rs >> 30 & 1; setbit(cw, i, bits[i]); }
                bch_encode(&bc, cw); bch_ref(&bc, bits, kbch, ref);
                ldpc_ref(&L, ref, ref);
                ldpc_encode(&L, cw, 0);
                int e1 = 0; for (int i = 0; i < nldpc; i++) e1 += getbit(cw, i) != ref[i];
                memset(cw, 0, (size_t)nldpc / 8); for (int i = 0; i < kbch; i++) setbit(cw, i, bits[i]);
                bch_encode(&bc, cw); ldpc_encode(&L, cw, mod != T2_QPSK);
                bitil_run(&B, cw, lab); bitil_ref(&B, mod, rate, shortf, nbch, ref, lab2);
                int e2 = memcmp(lab, lab2, (size_t)B.ncells) != 0;
                if (e1 || e2) fail++;
                if (verbose && (e1 || e2 || (mod == 2 && rate == 2)))
                    fprintf(stderr, "selftest %s %s %s: BCH+LDPC %d Fehler, Bit-IL %s\n", shortf ? "16200" : "64800", t2_mod_name[mod], t2_rate_name[rate], e1, e2 ? "FEHLER" : "ok");
                free(cw); free(bits); free(ref); free(lab); free(lab2); ldpc_free(&L); bch_free(&bc);
            }
    if (verbose) fprintf(stderr, "selftest FEC/Bit-IL: %s\n", fail ? "FEHLER" : "ok (48 Kombinationen)");
    /* IFFT-Genauigkeit: Radix-8-Festkomma gegen double-DFT (Stichprobe), 64-QAM-artige Zellen, RMS ≈ −12 dBFS */
    for (int N = 1024; N <= 32768; N *= 2) {
        t2fft_t *f = t2fft_create(N);
        int S = t2fft_shift_bits(f), K = N * 85 / 100;
        double A = 32768 * pow(10, -12 / 20.0) * pow(2, S) / sqrt((double)K);
        int16_t *in = al64((size_t)N * 4), *out = al64((size_t)N * 4 + 64);
        double *xr = calloc((size_t)N, 8), *xi = calloc((size_t)N, 8);
        for (int k = 0; k < K; k++) {
            int b = (k - K / 2 + N) % N; rs = rs * 1103515245u + 12345u;
            double re, im; const_point(T2_64QAM, (int)(rs >> 20) & 63, &re, &im);
            in[2 * b] = (int16_t)lrint(A * re); in[2 * b + 1] = (int16_t)lrint(A * im); xr[b] = in[2 * b]; xi[b] = in[2 * b + 1];
        }
        t2fft_ifft(f, in, out, 0);
        double ps = 0, pe = 0;
        for (int n = 0; n < N; n += 61) {
            double sr = 0, si = 0;
            for (int k = 0; k < N; k++) { if (!xr[k] && !xi[k]) continue; double ph = 2 * M_PI * (double)k * n / N; sr += xr[k] * cos(ph) - xi[k] * sin(ph); si += xr[k] * sin(ph) + xi[k] * cos(ph); }
            sr /= pow(2, S); si /= pow(2, S);
            ps += sr * sr + si * si; pe += (out[2 * n] - sr) * (out[2 * n] - sr) + (out[2 * n + 1] - si) * (out[2 * n + 1] - si);
        }
        double snr = 10 * log10(ps / pe);
        if (snr < 50) fail++;
        if (verbose) fprintf(stderr, "selftest IFFT %5d (%s): SNR %.1f dB gegen double\n", N, t2fft_backend(), snr);
        free(in); free(out); free(xr); free(xi); t2fft_free(f);
    }
    return fail;
}
