/*
 * isdbt.c – ISDB-T-Senderbausteine (ARIB STD-B31 / ABNT NBR 15601-1), synchrone Modulation.
 *
 * Schichtcodierer je Schicht (Kap. 3.4–3.11 der Norm):
 *   TSP 188 → RS(204,188) → Energieverwürfelung (PRBS 1+x^14+x^15, Rücksetzen je OFDM-Rahmen, Sync-Byte
 *   unverwürfelt) → Byte-Interleaver I=12, M=17 → Faltungscode K=7 + Punktierung → seriell/parallel (b0 zuerst)
 *   → Bitverschachtelung (Verzögerung 120·b/(m−1) Zellen + Laufzeitausgleich auf 2 Symbole) → Zeitverschachtelung
 *   (Zelle i im Segment: I·m_i Symbole, m_i = 5i mod 96, + Laufzeitausgleich auf ganze Rahmen).
 *
 * Ausrichtung TS ↔ OFDM-Rahmen (so wie Empfänger sie erwarten, geprüft mit gr-isdbt): Eine 204-Byte-Einheit
 * beginnt mit dem Byte NACH dem Sync-Byte ([b1..b203, 0x47]); am Rahmenbeginn (nach Rückrechnung aller
 * Sender-/Empfänger-Laufzeiten) steht der Beginn einer Einheit, Zweig 0 des Byte-Interleavers und der
 * Rücksetzpunkt der Verwürfelung.
 *
 * Rahmen: 204 Symbole, verstreute Piloten k mod 12 = 3·(n mod 4), CP am oberen Bandrand, TMCC (DBPSK,
 * 204 Bit mit Differenzmengen-Code (273,191) verkürzt auf (184,102)), AC1 (DBPSK, Füllbits 1),
 * Frequenzverschachtelung: Intersegment (ohne Teilempfangssegment), Rotation, Zufallspermutation.
 */
#include "isdbt.h"
#include "isdbt_tables.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>

int isdbt_cps(int mode) { return 96 << (mode - 1); }
int isdbt_nfft(int mode) { return 1024 << mode; }
int isdbt_bits(int mod) { return 2 * (mod + 1); }

int isdbt_ti_code(int mode, int I)
{
    static const int tab[3][4] = {{0, 4, 8, 16}, {0, 2, 4, 8}, {0, 1, 2, 4}};
    if (mode < 1 || mode > 3) return -1;
    for (int i = 0; i < 4; i++) if (tab[mode - 1][i] == I) return i;
    return -1;
}

int isdbt_ti_adjust(int I) { return I ? (204 - (95 * I) % 204) % 204 : 0; }

int isdbt_layer_bytes_per_symbol(const isdbt_cfg_t *c, int l)
{
    const isdbt_layer_cfg_t *L = &c->L[l];
    long bits = (long)isdbt_cps(c->mode) * L->nseg * isdbt_bits(L->mod) * dvb_coderate_num[L->cr];
    long den = (long)dvb_coderate_den[L->cr] * 8;
    if (bits % den) return -1;
    return (int)(bits / den);
}

double isdbt_layer_bitrate(const isdbt_cfg_t *c, int l)
{
    if (l >= c->nlayers) return 0;
    int nfft = isdbt_nfft(c->mode), g = nfft >> (5 - c->guard);
    double tframe = 204.0 * (nfft + g) / ISDBT_FS;
    return isdbt_layer_bytes_per_symbol(c, l) * 188.0 * 8.0 / tframe;   /* N = Bytes/Symbol TSP je Rahmen */
}

int isdbt_cfg_check(const isdbt_cfg_t *c, char *err, int n)
{
    if (c->mode < 1 || c->mode > 3) { snprintf(err, n, "Modus muss 1, 2 oder 3 sein"); return -1; }
    if (c->nlayers < 1 || c->nlayers > 2) { snprintf(err, n, "1 oder 2 Schichten"); return -1; }
    int tot = 0;
    for (int l = 0; l < c->nlayers; l++) {
        const isdbt_layer_cfg_t *L = &c->L[l];
        if (L->mod < 0 || L->mod > 2 || L->cr < 0 || L->cr > 4) { snprintf(err, n, "Schicht %c: Modulation/Coderate", 'A' + l); return -1; }
        if (isdbt_ti_code(c->mode, L->ti) < 0) { snprintf(err, n, "Schicht %c: Zeitverschachtelung I=%d in Modus %d unzulässig", 'A' + l, L->ti, c->mode); return -1; }
        if (L->nseg < 1) { snprintf(err, n, "Schicht %c: Segmente", 'A' + l); return -1; }
        if (isdbt_layer_bytes_per_symbol(c, l) < 0) { snprintf(err, n, "Schicht %c: Bytes je Symbol nicht ganzzahlig", 'A' + l); return -1; }
        tot += L->nseg;
    }
    if (tot != 13) { snprintf(err, n, "Segmentsumme %d ≠ 13", tot); return -1; }
    return 0;
}

/* ============================================================ Schichtcodierer */
static void prbs_frame_table(uint8_t *t, int N)
{
    uint16_t reg = 0x00A9;          /* r1..r15 = 1 0 0 1 0 1 0 1 0 0 0 0 0 0 0, r1 = Bit 0 */
    for (int u = 0; u < N; u++)
        for (int i = 0; i < 204; i++) {
            uint8_t b = 0;
            for (int k = 0; k < 8; k++) {
                int fb = ((reg >> 13) ^ (reg >> 14)) & 1;
                reg = (uint16_t)(((reg << 1) | fb) & 0x7FFF);
                b = (uint8_t)((b << 1) | fb);
            }
            t[u * 204 + i] = (i == 203) ? 0 : b;   /* Platz des Sync-Bytes: Generator läuft, Ausgang gesperrt */
        }
}

static uint32_t pow2_at_least(uint32_t x) { uint32_t p = 1; while (p < x) p <<= 1; return p; }

int isdbt_layer_init(isdbt_layer_t *L, const isdbt_cfg_t *c, int l, int bit_syms)
{
    memset(L, 0, sizeof *L);
    const isdbt_layer_cfg_t *lc = &c->L[l];
    L->l = l; L->m = isdbt_bits(lc->mod); L->cps = isdbt_cps(c->mode); L->nseg = lc->nseg;
    L->cells = L->cps * L->nseg; L->bps = isdbt_layer_bytes_per_symbol(c, l); L->N = L->bps;
    L->I = lc->ti; L->D = isdbt_ti_adjust(lc->ti); L->Gm = L->cells / 96;
    if (L->bps <= 0) return -1;
    /* Energieverwürfelung */
    L->prbs = (uint8_t *)malloc((size_t)L->N * 204);
    prbs_frame_table(L->prbs, L->N);
    /* TS-Ausrichtung: Der Empfänger sieht am Rahmenbeginn den Faltungscoder-Eingang von bit_syms Symbolen
       vor dem Rahmenbeginn (Zeitverschachtelung inkl. Ausgleich = ganze Rahmen). Dort muss eine Einheit
       beginnen; die Einheit, die er dort aus dem Deinterleaver bekommt (11 Einheiten früher am
       Interleaver-Eingang), ist die erste des Verwürfelungsrahmens. */
    long fb = (long)L->N * 204;
    long ptag = ((-(long)bit_syms * L->bps) % fb + fb) % fb;
    const char *sh = getenv("ISDBT_TS_SHIFT");          /* nur Experimente */
    if (sh) ptag = ((ptag + atol(sh)) % fb + fb) % fb;
    L->q = (int)(ptag % 204);
    long ktag = (ptag - L->q) / 204;                    /* Einheitenindex am Interleaver-Ausgang */
    long u0 = ((ktag - 11) % L->N + L->N) % L->N;       /* erste Rahmeneinheit am Interleaver-Eingang */
    L->frame_pos = (int)((L->N - u0) % L->N);
    dvb_convint_init(&L->ci);
    L->bytes = (uint8_t *)calloc((size_t)L->bps + 3 * 204, 1);
    L->nbytes = L->q;                                   /* Vorlauf bis zur ersten Einheitsgrenze (Nullbytes) */
    dvb_convcode_init(&L->cc, lc->cr);
    L->punct = (uint8_t *)calloc((size_t)L->cells * L->m / 8 + 16, 1);
    /* Bitverschachtelung: b_k um 120·k/(m−1) Zellen verzögert, Ausgleich X → Summe Sender+Empfänger = bit_syms Symbole */
    L->X = bit_syms * L->cells - 120;
    if (L->X < 0) return -1;
    for (int b = 0; b < L->m; b++) L->dl[b] = 120 * b / (L->m - 1);
    L->bmask = pow2_at_least((uint32_t)(L->X + 121 + L->cells)) - 1;   /* ganzes Symbol schreiben, ohne Ungelesenes zu überschreiben */
    L->bring = (uint8_t *)calloc(L->bmask + 1, 1);
    L->wtmp = (uint8_t *)calloc(L->cells, 1);
    /* Speicherreihenfolge für die Zeitverschachtelung: Zellen mit gleichem m_i zusammenhängend */
    L->store_of = (uint16_t *)malloc(L->cells * sizeof(uint16_t));
    const int per = L->cps / 96;
    for (int c2 = 0; c2 < L->cells; c2++) {
        int seg = c2 / L->cps, j = c2 % L->cps, mi = (5 * j) % 96;
        L->store_of[c2] = (uint16_t)(mi * L->Gm + seg * per + j / 96);
    }
    L->tdepth = L->I * 95 + L->D + 1;
    L->ti = (uint8_t *)calloc((size_t)L->tdepth * L->cells, 1);
    L->out = (uint8_t *)calloc(L->cells, 1);
    /* Anlaufzustand (Interleaver-Historie, Vorlauf-Bytes) pseudozufällig statt null: sonst trügen in den ersten
       ~I·95+D Symbolen alle Zellen denselben Konstellationspunkt → Impulse, Übersteuerung, Spektrumsspritzer */
    uint32_t r = 0x9E3779B9u ^ (uint32_t)l;
#define RNDFILL(ptr, n) for (size_t i_ = 0; i_ < (size_t)(n); i_++) { r = r * 1664525u + 1013904223u; ((uint8_t *)(ptr))[i_] = (uint8_t)(r >> 24); }
    RNDFILL(L->bytes, L->q);
    RNDFILL(L->ci.hist, sizeof L->ci.hist);
    RNDFILL(L->bring, L->bmask + 1);
    RNDFILL(L->ti, (size_t)L->tdepth * L->cells);
#undef RNDFILL
    for (uint32_t i = 0; i <= L->bmask; i++) L->bring[i] &= (uint8_t)((1u << L->m) - 1);
    for (size_t i = 0; i < (size_t)L->tdepth * L->cells; i++) L->ti[i] &= (uint8_t)((1u << L->m) - 1);
    return 0;
}

void isdbt_layer_free(isdbt_layer_t *L)
{
    free(L->prbs); free(L->bytes); free(L->punct); free(L->bring); free(L->store_of); free(L->ti); free(L->out); free(L->wtmp);
    memset(L, 0, sizeof *L);
}

/* Testhilfe: ISDBT_DUMP=präfix schreibt je Schicht den Faltungscoder-Eingang (_cc?.bin) und die punktierten Bits (_pu?.bin) */
static void dump(const isdbt_layer_t *L, int what, const void *p, size_t n)
{
    static FILE *f[2][2]; static int init;
    if (!init) {
        init = 1; const char *d = getenv("ISDBT_DUMP");
        if (d) { char fn[512]; for (int k = 0; k < 2; k++) for (int w = 0; w < 2; w++) {
            snprintf(fn, sizeof fn, "%s_%s%c.bin", d, w ? "pu" : "cc", 'a' + k); f[w][k] = fopen(fn, "wb"); } }
    }
    if (f[what][L->l]) fwrite(p, 1, n, f[what][L->l]);
}

static void unpack_words(const uint8_t *p, int m, int n, uint8_t *o)
{
    if (m == 2) for (int c = 0; c < n; c += 4, p++) { o[c] = *p >> 6; o[c + 1] = (*p >> 4) & 3; o[c + 2] = (*p >> 2) & 3; o[c + 3] = *p & 3; }
    else if (m == 4) for (int c = 0; c < n; c += 2, p++) { o[c] = *p >> 4; o[c + 1] = *p & 15; }
    else for (int c = 0; c < n; c += 4, p += 3) {
        uint32_t v = (uint32_t)p[0] << 16 | (uint32_t)p[1] << 8 | p[2];
        o[c] = (uint8_t)(v >> 18); o[c + 1] = (uint8_t)((v >> 12) & 63); o[c + 2] = (uint8_t)((v >> 6) & 63); o[c + 3] = (uint8_t)(v & 63);
    }
}

static void or_masked(uint8_t *restrict o, const uint8_t *restrict s, uint8_t bm, int n)
{
    for (int i = 0; i < n; i++) o[i] |= s[i] & bm;
}

void isdbt_layer_symbol(isdbt_layer_t *L, isdbt_pkt_fn fn, void *ctx, int profile)
{
    double t0 = profile ? dvb_now() : 0;
    /* 1) Einheiten erzeugen, bis ein Symbol Eingangsbytes vorliegt */
    while (L->nbytes < L->bps) {
        uint8_t rs[RS_PKT], unit[RS_PKT];
        fn(ctx, L->l, rs);
        rs[0] = TS_SYNC;
        dvb_rs_encode(rs, rs);
        const uint8_t *p = L->prbs + (size_t)L->frame_pos * 204;
        for (int i = 0; i < 203; i++) unit[i] = rs[i + 1] ^ p[i];
        unit[203] = rs[0];
        if (++L->frame_pos == L->N) L->frame_pos = 0;
        dvb_convint_packet(&L->ci, unit, L->bytes + L->nbytes);
        L->nbytes += RS_PKT;
        L->packets++;
    }
    /* 2) Faltungscode + Punktierung: bps Bytes → cells·m Bits */
    dump(L, 0, L->bytes, (size_t)L->bps);
    dvb_convcode_bytes(&L->cc, L->bytes, (size_t)L->bps, L->punct);
    dump(L, 1, L->punct, (size_t)L->cells * L->m / 8);
    L->nbytes -= L->bps; memmove(L->bytes, L->bytes + L->bps, (size_t)L->nbytes);
    double t1 = profile ? dvb_now() : 0;
    /* 3) seriell/parallel (b0 = erstes Bit = MSB des Worts) in den Ring, dann je Bitebene ein
          zusammenhängender, vektorisierbarer Durchlauf mit Verzögerung X + dl[b], Ablage in die TI-Zeile */
    const int m = L->m, n = L->cells; const uint32_t R = L->bmask + 1;
    uint8_t *ring = L->bring, *w = L->wtmp;
    const uint8_t *pp = L->punct;
    {
        uint32_t pos = L->bt & L->bmask;
        if (pos + (uint32_t)n <= R) unpack_words(pp, m, n, ring + pos);
        else { unpack_words(pp, m, n, w); memcpy(ring + pos, w, R - pos); memcpy(ring, w + (R - pos), n - (R - pos)); }
    }
    memset(w, 0, (size_t)n);
    for (int b = 0; b < m; b++) {
        const uint8_t bm = (uint8_t)(1u << (m - 1 - b));
        uint32_t st = (L->bt - (uint32_t)(L->X + L->dl[b])) & L->bmask;
        int n1 = (st + (uint32_t)n <= R) ? n : (int)(R - st);
        or_masked(w, ring + st, bm, n1);
        if (n1 < n) or_masked(w + n1, ring, bm, n - n1);
    }
    uint8_t *row = L->ti + (size_t)L->tpos * L->cells;
    const uint16_t *so = L->store_of;
    for (int c = 0; c < n; c++) row[so[c]] = w[c];
    L->bt += (uint32_t)n;
    double t2 = profile ? dvb_now() : 0;
    /* 4) Zeitverschachtelung: Gruppe m_i liest die Zeile von I·m_i + D Symbolen zuvor */
    const int G = L->Gm;
    for (int mi = 0; mi < 96; mi++) {
        int src = L->tpos - L->I * mi - L->D;
        src %= L->tdepth; if (src < 0) src += L->tdepth;
        memcpy(L->out + mi * G, L->ti + (size_t)src * L->cells + mi * G, (size_t)G);
    }
    if (++L->tpos == L->tdepth) L->tpos = 0;
    if (profile) { double t3 = dvb_now(); L->t_fec += t1 - t0; L->t_bit += t2 - t1; L->t_ti += t3 - t2; }
}

/* ============================================================ TMCC */
static const uint8_t g_exp[] = {82, 77, 76, 71, 67, 66, 56, 52, 48, 40, 36, 34, 24, 22, 18, 10, 4, 0};

static void put_bits(uint8_t *s, int first, int n, unsigned v) { for (int i = 0; i < n; i++) s[first + i] = (uint8_t)((v >> (n - 1 - i)) & 1); }

void isdbt_tmcc_generate(const isdbt_cfg_t *c, int odd, uint8_t *s)
{
    memset(s, 0, 204);
    put_bits(s, 1, 16, odd ? 0xCA11u : 0x35EEu);            /* w0 = 0011010111101110, w1 = invertiert */
    put_bits(s, 17, 3, (unsigned)c->seg_type);              /* Segmenttyp: 000 synchron */
    put_bits(s, 20, 2, 0);                                  /* Systemkennung: ISDB-T */
    put_bits(s, 22, 4, 0xF);                                /* Umschalt-Countdown: keine Umschaltung */
    put_bits(s, 26, 1, 0);                                  /* Alarm-Startflag */
    uint8_t cur[40];
    put_bits(cur, 0, 1, (unsigned)c->partial);
    for (int l = 0; l < 3; l++) {
        unsigned mod = 7, cr = 7, ti = 7, ns = 15;
        if (l < c->nlayers) {
            mod = (unsigned)c->L[l].mod + 1;                /* 001 QPSK, 010 16QAM, 011 64QAM */
            cr = (unsigned)c->L[l].cr;                       /* 000 1/2 .. 100 7/8 */
            ti = (unsigned)isdbt_ti_code(c->mode, c->L[l].ti);
            ns = (unsigned)c->L[l].nseg;
        }
        put_bits(cur, 1 + 13 * l, 3, mod); put_bits(cur, 4 + 13 * l, 3, cr);
        put_bits(cur, 7 + 13 * l, 3, ti);  put_bits(cur, 10 + 13 * l, 4, ns);
    }
    memcpy(s + 27, cur, 40);                                /* aktuelle Parameter B27..B66 */
    memcpy(s + 67, cur, 40);                                /* nächste Parameter B67..B106 (gleich) */
    put_bits(s, 107, 3, 7);                                 /* Phasenkorrektur verbundener Segmente: unbenutzt */
    put_bits(s, 110, 12, 0xFFF);                            /* reserviert */
    /* Parität B122..B203: systematischer Code mit g(x) über B20..B121 (erstes Bit = höchster Grad) */
    uint8_t g[83] = {0};
    for (size_t i = 0; i < sizeof g_exp; i++) g[g_exp[i]] = 1;
    uint8_t reg[82] = {0};                                  /* reg[0] = Grad 81 */
    for (int i = 20; i <= 121; i++) {
        uint8_t fb = s[i] ^ reg[0];
        memmove(reg, reg + 1, 81); reg[81] = 0;
        if (fb) for (int k = 0; k < 82; k++) reg[k] ^= g[81 - k];
    }
    memcpy(s + 122, reg, 82);
}

/* ============================================================ OFDM-Rahmen */
static const int seg_order[13] = {11, 9, 7, 5, 3, 1, 0, 2, 4, 6, 8, 10, 12};   /* Frequenzreihenfolge → Segmentnummer */

static void build_map(uint32_t *map, int mod, int A)
{
    const int m = isdbt_bits(mod);
    const double norm = (mod == ISDBT_QPSK) ? sqrt(2.0) : (mod == ISDBT_16QAM) ? sqrt(10.0) : sqrt(42.0);
    for (int w = 0; w < (1 << m); w++) {
        int b[6];
        for (int k = 0; k < m; k++) b[k] = (w >> (m - 1 - k)) & 1;   /* b0 = MSB */
        int I, Q;
        if (mod == ISDBT_QPSK) { I = 1; Q = 1; }
        else if (mod == ISDBT_16QAM) { I = b[2] ? 1 : 3; Q = b[3] ? 1 : 3; }
        else { I = b[2] ? (b[4] ? 3 : 1) : (b[4] ? 5 : 7); Q = b[3] ? (b[5] ? 3 : 1) : (b[5] ? 5 : 7); }
        if (b[0]) I = -I;
        if (b[1]) Q = -Q;
        int16_t re = (int16_t)lrint(I * A / norm), im = (int16_t)lrint(Q * A / norm);
        map[w] = (uint16_t)re | ((uint32_t)(uint16_t)im << 16);
    }
}

int isdbt_frame_init(isdbt_frame_t *f, const isdbt_cfg_t *c, isdbt_layer_t *layers, int A)
{
    memset(f, 0, sizeof *f);
    f->cfg = *c; f->A = A; f->nlayers = c->nlayers;
    f->nfft = isdbt_nfft(c->mode); f->cps = isdbt_cps(c->mode);
    const int cpseg = 108 << (c->mode - 1);              /* Träger je Segment */
    f->K = 13 * cpseg + 1; f->guard_len = f->nfft >> (5 - c->guard);
    f->ndata = 13 * f->cps;
    const int K = f->K, cps = f->cps, nd = f->ndata;
    f->ntmcc = 13 << (c->mode - 1); f->nac = 26 << (c->mode - 1);
    /* PRBS W_i über alle Träger ab Träger 0 (x^11 + x^9 + 1, Start alle Einsen) */
    f->wk = (uint8_t *)malloc(K);
    unsigned reg = 0x7FF;
    for (int k = 0; k < K; k++) { f->wk[k] = (uint8_t)(reg & 1); unsigned nb = ((reg >> 2) ^ reg) & 1; reg = (reg >> 1) | (nb << 10); }
    uint8_t *kind = (uint8_t *)calloc(K, 1);              /* 1 TMCC, 2 AC */
    for (int i = 0; i < f->ntmcc; i++) { if (tmcc_carriers[i] % 12 % 3 == 0 || kind[tmcc_carriers[i]]) { free(kind); return -2; } kind[tmcc_carriers[i]] = 1; }
    for (int i = 0; i < f->nac; i++) { if (ac_carriers[i] % 12 % 3 == 0 || kind[ac_carriers[i]]) { free(kind); return -2; } kind[ac_carriers[i]] = 2; }
    for (int i = 0; i < f->ntmcc; i++) f->tmcc_bin[i] = (uint16_t)((tmcc_carriers[i] - K / 2 + f->nfft) % f->nfft);
    for (int i = 0; i < f->nac; i++) f->ac_bin[i] = (uint16_t)((ac_carriers[i] - K / 2 + f->nfft) % f->nfft);
    /* Permutation prüfen */
    const uint16_t *perm = (c->mode == 1) ? rand_perm_m1 : (c->mode == 2) ? rand_perm_m2 : rand_perm_m3;
    {
        uint8_t *seen = (uint8_t *)calloc(cps, 1);
        for (int i = 0; i < cps; i++) { if (perm[i] >= cps || seen[perm[i]]) { free(seen); free(kind); return -3; } seen[perm[i]] = 1; }
        free(seen);
    }
    /* Schichtlage im Datenstrom (Segmentnummernfolge) */
    int off = 0;
    for (int l = 0; l < c->nlayers; l++) {
        f->lay_off[l] = off; f->lay_cells[l] = c->L[l].nseg * cps; off += f->lay_cells[l];
        build_map(f->map_iq[l], c->L[l].mod, A);
    }
    /* natürlicher Index x (Schichten verkettet) → physischer Datenindex P nach Frequenzverschachtelung */
    int *Pof = (int *)malloc(nd * sizeof(int));
    const int s0 = c->partial ? 1 : 0, n = 13 - s0;
    for (int x = 0; x < nd; x++) {
        int y;
        if (x < s0 * cps) y = x;
        else { int xp = x - s0 * cps; y = (s0 + xp % n) * cps + xp / n; }      /* Intersegment */
        int s = y / cps, i = y % cps;
        int j = ((i - s) % cps + cps) % cps;                                      /* Rotation */
        Pof[x] = s * cps + perm[j];                                               /* Zufallspermutation */
    }
    int *phys = (int *)malloc(nd * sizeof(int));
    double ptot = 0;
    for (int r = 0; r < 4; r++) {
        f->data_bin[r] = (uint16_t *)malloc(nd * sizeof(uint16_t));
        f->pil_bin[r] = (uint16_t *)malloc(K * sizeof(uint16_t));
        f->pil_iq[r] = (uint32_t *)malloc(K * sizeof(uint32_t));
        int np = 0, dcount = 0;
        int segcount[13] = {0};
        for (int k = 0; k < K; k++) {
            uint16_t bin = (uint16_t)((k - K / 2 + f->nfft) % f->nfft);
            if (k == K - 1 || k % 12 == 3 * r) {
                f->pil_bin[r][np] = bin;
                f->pil_iq[r][np] = (uint16_t)(int16_t)lrint((f->wk[k] ? -4.0 : 4.0) * A / 3.0);
                np++;
            } else if (!kind[k]) {
                int fseg = k / cpseg;
                if (dcount / cps != fseg) { free(kind); free(Pof); free(phys); return -4; }
                phys[seg_order[fseg] * cps + dcount % cps] = bin;
                segcount[fseg]++; dcount++;
            }
        }
        if (dcount != nd) { free(kind); free(Pof); free(phys); return -5; }
        for (int s = 0; s < 13; s++) if (segcount[s] != cps) { free(kind); free(Pof); free(phys); return -6; }
        f->npil[r] = np;
        for (int l = 0; l < c->nlayers; l++)
            for (int cc = 0; cc < f->lay_cells[l]; cc++) {
                int x = f->lay_off[l] + cc;
                f->data_bin[r][f->lay_off[l] + layers[l].store_of[cc]] = (uint16_t)phys[Pof[x]];
            }
        ptot += nd + np * 16.0 / 9.0 + f->ntmcc + f->nac;
    }
    f->mean_cell_power = ptot / 4.0;
    free(kind); free(Pof); free(phys);
    for (int i = 0; i < f->ntmcc; i++) f->tmcc_sign[i] = 1;
    for (int i = 0; i < f->nac; i++) f->ac_sign[i] = 1;
    return 0;
}

void isdbt_frame_free(isdbt_frame_t *f)
{
    free(f->wk);
    for (int r = 0; r < 4; r++) { free(f->data_bin[r]); free(f->pil_bin[r]); free(f->pil_iq[r]); }
    memset(f, 0, sizeof *f);
}

void isdbt_frame_build(isdbt_frame_t *f, isdbt_layer_t *layers, int16_t *iq)
{
    const int r = f->symbol & 3;
    uint32_t *o = (uint32_t *)iq;
    for (int l = 0; l < f->nlayers; l++) {
        const uint16_t *db = f->data_bin[r] + f->lay_off[l];
        const uint32_t *map = f->map_iq[l];
        const uint8_t *y = layers[l].out;
        const int n = f->lay_cells[l];
        for (int i = 0; i < n; i += 4) {
            o[db[i]] = map[y[i]]; o[db[i + 1]] = map[y[i + 1]]; o[db[i + 2]] = map[y[i + 2]]; o[db[i + 3]] = map[y[i + 3]];
        }
    }
    const uint16_t *pb = f->pil_bin[r]; const uint32_t *pv = f->pil_iq[r];
    for (int i = 0; i < f->npil[r]; i++) o[pb[i]] = pv[i];
    /* TMCC / AC1: DBPSK, Referenz B0 = W_i des Trägers */
    if (f->symbol == 0) {
        isdbt_tmcc_generate(&f->cfg, f->frame & 1, f->tmcc_bits);
        for (int i = 0; i < f->ntmcc; i++) f->tmcc_sign[i] = f->wk[tmcc_carriers[i]] ? -1 : 1;
        for (int i = 0; i < f->nac; i++) f->ac_sign[i] = f->wk[ac_carriers[i]] ? -1 : 1;
    } else {
        if (f->tmcc_bits[f->symbol]) for (int i = 0; i < f->ntmcc; i++) f->tmcc_sign[i] = (int8_t)-f->tmcc_sign[i];
        for (int i = 0; i < f->nac; i++) f->ac_sign[i] = (int8_t)-f->ac_sign[i];   /* AC-Füllbits = 1 */
    }
    const int16_t A = (int16_t)f->A;
    for (int i = 0; i < f->ntmcc; i++) o[f->tmcc_bin[i]] = (uint16_t)(int16_t)(f->tmcc_sign[i] * A);
    for (int i = 0; i < f->nac; i++) o[f->ac_bin[i]] = (uint16_t)(int16_t)(f->ac_sign[i] * A);
    if (++f->symbol == 204) { f->symbol = 0; f->frame++; }
}
