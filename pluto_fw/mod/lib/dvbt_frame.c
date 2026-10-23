/*
 * dvbt_frame.c – OFDM-Rahmenaufbau: verstreute/kontinuierliche Piloten, TPS (DBPSK + BCH),
 * Trägerbelegung (EN 300 744 V1.6.1 Kap. 4.5, 4.6).
 */
#include "dvbmod.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

/* Tabelle 7: kontinuierliche Piloten 2K (45); 8K = 2K-Menge + 1704·j, j = 0..3 (177 Träger) */
static const uint16_t cp_2k[45] = {
    0, 48, 54, 87, 141, 156, 192, 201, 255, 279, 282, 333, 432, 450, 483, 525, 531, 618, 636, 714,
    759, 765, 780, 804, 873, 888, 918, 939, 942, 969, 984, 1050, 1101, 1107, 1110, 1137, 1140, 1146,
    1206, 1269, 1323, 1377, 1491, 1683, 1704};
/* Tabelle 8: TPS-Träger 2K (17); 8K = 2K-Menge + 1704·j (68 Träger) */
static const uint16_t tps_2k[17] = {
    34, 50, 209, 346, 413, 569, 595, 688, 790, 901, 1073, 1219, 1262, 1286, 1469, 1594, 1687};

static inline uint16_t bin_of(const dvbt_frame_t *f, int k) { return (uint16_t)((k - f->K / 2 + f->nfft) % f->nfft); }

int dvbt_frame_init(dvbt_frame_t *f, int mode, int guard, int constellation, int coderate, int cell_id, int A)
{
    memset(f, 0, sizeof *f);
    f->mode = mode; f->guard = guard;
    f->nfft = (mode == DVBT_8K) ? 8192 : 2048;
    f->K = (mode == DVBT_8K) ? 6817 : 1705;
    f->guard_len = f->nfft >> (5 - guard);     /* 1/32,1/16,1/8,1/4 */
    f->constellation = constellation; f->coderate_hp = coderate; f->coderate_lp = 0; f->cell_id = cell_id;
    f->A = A;
    const int K = f->K, nrep = (mode == DVBT_8K) ? 4 : 1;

    /* PRBS w_k: x^11 + x^2 + 1, Start alle Einsen, Ausgang = LSB, ein Schritt je Träger */
    f->wk = (uint8_t *)malloc(K);
    unsigned reg = 0x7FF;
    for (int k = 0; k < K; k++) {
        f->wk[k] = (uint8_t)(reg & 1);
        unsigned nb = ((reg >> 2) ^ reg) & 1;
        reg = (reg >> 1) | (nb << 10);
    }
    uint8_t *is_cp = (uint8_t *)calloc(K, 1), *is_tps = (uint8_t *)calloc(K, 1);
    for (int j = 0; j < nrep; j++) {
        for (int i = 0; i < 45; i++) is_cp[cp_2k[i] + 1704 * j] = 1;
        for (int i = 0; i < 17; i++) is_tps[tps_2k[i] + 1704 * j] = 1;
    }
    f->ntps = 0;
    for (int k = 0; k < K; k++) if (is_tps[k]) f->tps_bin[f->ntps++] = bin_of(f, k);
    int ncells = (mode == DVBT_8K) ? 6048 : 1512;
    double ptot = 0;
    for (int r = 0; r < 4; r++) {
        f->data_bin[r] = (uint16_t *)malloc(ncells * sizeof(uint16_t));
        f->pil_bin[r] = (uint16_t *)malloc(K * sizeof(uint16_t));
        f->pil_val[r] = (int16_t *)malloc(K * sizeof(int16_t));
        f->pil_iq[r] = (uint32_t *)malloc(K * sizeof(uint32_t));
        int nd = 0, np = 0;
        for (int k = 0; k < K; k++) {
            int sp = (k % 12) == 3 * r;
            if (sp || is_cp[k]) {
                f->pil_bin[r][np] = bin_of(f, k);
                f->pil_val[r][np] = (int16_t)lrint((f->wk[k] ? -4.0 : 4.0) * A / 3.0);
                f->pil_iq[r][np] = (uint16_t)f->pil_val[r][np];
                np++;
            } else if (is_tps[k]) {
                /* TPS: eigene Liste */
            } else {
                if (nd >= ncells) { free(is_cp); free(is_tps); return -2; }
                f->data_bin[r][nd++] = bin_of(f, k);
            }
        }
        if (nd != ncells) { free(is_cp); free(is_tps); return -3; }
        f->npil[r] = np;
        ptot += nd + np * 16.0 / 9.0 + f->ntps;
    }
    f->ndata = ncells;
    f->mean_cell_power = ptot / 4.0;
    free(is_cp); free(is_tps);
    /* TPS-Startzustand (Symbol 0 setzt ihn neu) */
    for (int i = 0; i < f->ntps; i++) f->tps_sign[i] = 1;
    return 0;
}

void dvbt_frame_free(dvbt_frame_t *f)
{
    free(f->wk);
    for (int r = 0; r < 4; r++) { free(f->data_bin[r]); free(f->pil_bin[r]); free(f->pil_val[r]); free(f->pil_iq[r]); }
    memset(f, 0, sizeof *f);
}

static void set_bits(uint8_t *s, int first, int nbits, unsigned val)
{
    for (int i = 0; i < nbits; i++) s[first + i] = (uint8_t)((val >> (nbits - 1 - i)) & 1);
}

/* BCH(67,53), verkürzt aus BCH(127,113), g(x) = x^14+x^9+x^8+x^6+x^5+x^4+x^2+x+1 */
static unsigned bch_parity(const uint8_t *s)  /* s[1..53] */
{
    unsigned reg = 0;
    for (int i = 1; i <= 53; i++) {             /* 60 führende Nullen ändern ein Nullregister nicht */
        unsigned fb = (s[i] ^ (reg >> 13)) & 1;
        reg = ((reg << 1) & 0x3FFF) ^ (fb ? 0x0377u : 0u);
    }
    return reg;
}

void dvbt_tps_generate(dvbt_frame_t *f)
{
    uint8_t *s = f->tps_bits;
    memset(s, 0, 68);
    s[0] = 0;                                                   /* Initialisierung: Modulation aus w_k */
    set_bits(s, 1, 16, (f->frame & 1) ? 0xCA11u : 0x35EEu);     /* Synchronwort */
    set_bits(s, 17, 6, f->cell_id ? 0x1Fu : 0x17u);             /* Längenkennung 23/31 Bit */
    set_bits(s, 23, 2, (unsigned)f->frame);                     /* Rahmennummer */
    set_bits(s, 25, 2, (unsigned)f->constellation);             /* 00 QPSK 01 16 10 64 */
    set_bits(s, 27, 3, (unsigned)f->hierarchy);                 /* 000 nicht-hierarchisch */
    set_bits(s, 30, 3, (unsigned)f->coderate_hp);               /* Tabelle 13 */
    set_bits(s, 33, 3, (unsigned)f->coderate_lp);               /* 000 bei nicht-hierarchisch */
    set_bits(s, 36, 2, (unsigned)f->guard);                     /* 00 1/32 .. 11 1/4 */
    set_bits(s, 38, 2, (unsigned)f->mode);                      /* 00 2K, 01 8K */
    set_bits(s, 40, 8, (f->frame & 1) ? (f->cell_id & 0xFF) : ((f->cell_id >> 8) & 0xFF));
    /* s48..s53 = 0 (kein Time-Slicing, kein MPE-FEC) */
    set_bits(s, 54, 14, bch_parity(s));
}

void dvbt_frame_build(dvbt_frame_t *f, const dvbt_inner_t *in, const uint8_t *y, int16_t *iq)
{
    const int r = f->symbol & 3;
    uint32_t *o = (uint32_t *)iq;
    const uint32_t *map = in->map_iq;
    const uint16_t *db = f->data_bin[r];
    const int nd = f->ndata;
    for (int i = 0; i < nd; i += 4) {
        o[db[i]] = map[y[i]]; o[db[i + 1]] = map[y[i + 1]]; o[db[i + 2]] = map[y[i + 2]]; o[db[i + 3]] = map[y[i + 3]];
    }
    const uint16_t *pb = f->pil_bin[r]; const uint32_t *pv = f->pil_iq[r];
    const int np = f->npil[r];
    for (int i = 0; i < np; i++) o[pb[i]] = pv[i];
    /* TPS */
    if (f->symbol == 0) {
        dvbt_tps_generate(f);
        for (int i = 0; i < f->ntps; i++) {
            int k = (f->tps_bin[i] + f->K / 2) % f->nfft;
            f->tps_sign[i] = f->wk[k] ? -1 : 1;
        }
    } else if (f->tps_bits[f->symbol]) {
        for (int i = 0; i < f->ntps; i++) f->tps_sign[i] = (int8_t)-f->tps_sign[i];
    }
    const int16_t A = (int16_t)f->A;
    for (int i = 0; i < f->ntps; i++) o[f->tps_bin[i]] = (uint16_t)(int16_t)(f->tps_sign[i] * A);
    if (++f->symbol == 68) { f->symbol = 0; f->frame = (f->frame + 1) & 3; }
}
