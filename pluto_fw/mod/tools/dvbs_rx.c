/*
 * dvbs_rx – DVB-S-Prüfempfänger (Bitebene): weiche QPSK-Entscheidungen (int8 I,Q je Symbol) → Phasen-/Punktier-
 * Synchronisation (4 Drehungen × Punktierphase) → Viterbi K=7 (Hard/Soft, Rückverfolgung 96) → Faltungs-Deinterleaver →
 * RS(204,188)-Prüfung (Syndrom) → Entwürfelung → TS. Eigene Implementierung, unabhängig vom Senderzweig der Bibliothek.
 *   dvbs_rx --rate 3/4 -i soft.s8 -o out.ts
 */
#include "../lib/dvbmod.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int parity(unsigned x) { x ^= x >> 4; x ^= x >> 2; x ^= x >> 1; return x & 1; }
static const uint8_t px[5] = {0x01, 0x01, 0x05, 0x15, 0x51}, py[5] = {0x01, 0x03, 0x03, 0x0B, 0x2F};
static const int per[5] = {1, 2, 3, 5, 7};

/* Depunktierung: serielle weiche Bits (Reihenfolge wie gesendet) → Paare (X,Y) je Eingangsbit, fehlende = 0 */
static size_t depuncture(const int8_t *s, size_t n, int cr, int phase, int8_t *xy)
{
    size_t k = 0, o = 0; int p = phase;
    while (k < n) {
        int kx = (px[cr] >> p) & 1, ky = (py[cr] >> p) & 1;
        if (k + kx + ky > n) break;
        xy[2 * o] = kx ? s[k++] : 0;
        xy[2 * o + 1] = ky ? s[k++] : 0;
        o++; p = (p + 1) % per[cr];
    }
    return o;
}

/* Viterbi (64 Zustände, Metrik = Korrelation), Ausgabe Bits (je Byte ein Bit) */
static size_t viterbi(const int8_t *xy, size_t n, uint8_t *out)
{
    static int outX[64][2], outY[64][2];
    for (int s = 0; s < 64; s++)
        for (int u = 0; u < 2; u++) {
            unsigned w = (u << 6) | s;
            outX[s][u] = parity(w & 0171) ? -1 : 1; outY[s][u] = parity(w & 0133) ? -1 : 1;
        }
    int *pm = calloc(64, sizeof(int)), *nm = malloc(64 * sizeof(int));
    uint8_t *dec = malloc(n * 64);   /* Entscheidungen: Vorgängerbit */
    for (int s = 1; s < 64; s++) pm[s] = -1000000;
    for (size_t t = 0; t < n; t++) {
        int x = xy[2 * t], y = xy[2 * t + 1];
        for (int ns = 0; ns < 64; ns++) nm[ns] = -0x3fffffff;
        for (int s = 0; s < 64; s++)
            for (int u = 0; u < 2; u++) {
                int nsx = ((s >> 1) | (u << 5)) & 63;            /* neuer Zustand: u wird jüngstes Bit (Bit 5) */
                int m = pm[s] + outX[s][u] * x + outY[s][u] * y;
                if (m > nm[nsx]) { nm[nsx] = m; dec[t * 64 + nsx] = (uint8_t)(s & 1); }
            }
        int *tmp = pm; pm = nm; nm = tmp;
        if ((t & 255) == 0) { int mx = pm[0]; for (int s = 1; s < 64; s++) if (pm[s] > mx) mx = pm[s]; for (int s = 0; s < 64; s++) pm[s] -= mx; }
    }
    int bs = 0; for (int s = 1; s < 64; s++) if (pm[s] > pm[bs]) bs = s;
    for (size_t t = n; t-- > 0;) {
        out[t] = (uint8_t)((bs >> 5) & 1);
        int prevlsb = dec[t * 64 + bs];
        bs = ((bs << 1) | prevlsb) & 63;
    }
    free(pm); free(nm); free(dec);
    return n;
}

int main(int argc, char **argv)
{
    const char *in = NULL, *outp = NULL; int cr = CR_3_4; long maxsym = 4000000;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-i") && i + 1 < argc) in = argv[++i];
        else if (!strcmp(argv[i], "-o") && i + 1 < argc) outp = argv[++i];
        else if (!strcmp(argv[i], "--max") && i + 1 < argc) maxsym = atol(argv[++i]);
        else if (!strcmp(argv[i], "--rate") && i + 1 < argc) { cr = -1; for (int k = 0; k < 5; k++) if (!strcmp(argv[i + 1], dvb_coderate_name[k])) cr = k; i++; }
    }
    if (!in || !outp || cr < 0) { fprintf(stderr, "dvbs_rx --rate R -i soft.s8 -o out.ts [--max SYMBOLE]\n"); return 2; }
    FILE *f = fopen(in, "rb"); if (!f) { perror(in); return 1; }
    int8_t *iq = malloc(2 * maxsym); size_t ns = fread(iq, 2, maxsym, f) ; fclose(f);
    dvb_rs_init();
    /* Synchronisation an einem Probeabschnitt: 4 Drehungen × Punktierphase × Bit-Versatz (0/1) */
    const size_t probe = ns < 60000 ? ns : 60000;
    int best_rot = 0, best_ph = 0, best_off = 0, best_score = -1;
    int8_t *ser = malloc(2 * ns + 2), *xy = malloc(4 * ns + 8); uint8_t *bits = malloc(2 * ns + 8);
    for (int rot = 0; rot < 4; rot++)
        for (int off = 0; off < 2; off++)
            for (int ph = 0; ph < per[cr]; ph++) {
                size_t m = 0;
                for (size_t k = 0; k < probe; k++) {
                    int I = iq[2 * k], Q = iq[2 * k + 1], a, b;
                    switch (rot) { case 0: a = I; b = Q; break; case 1: a = Q; b = -I; break; case 2: a = -I; b = -Q; break; default: a = -Q; b = I; }
                    ser[m++] = (int8_t)a; ser[m++] = (int8_t)b;
                }
                size_t no = depuncture(ser + off, m - off, cr, ph, xy);
                viterbi(xy, no, bits);
                /* Bytes bilden und Sync-Raster 204 (0x47/0xB8, unverzögert in Zweig 0) suchen */
                size_t nby = no / 8 - 1; int score = 0;
                uint8_t *by = malloc(nby + 1);
                for (int bo = 0; bo < 8; bo++) {
                    for (size_t q = 0; q < nby; q++) { uint8_t v = 0; for (int j = 0; j < 8; j++) v = (uint8_t)((v << 1) | bits[8 * q + j + bo]); by[q] = v; }
                    for (size_t s0 = 0; s0 < 204 && nby > 204 * 20; s0++) {
                        int h = 0; for (int r = 0; r < 20; r++) { uint8_t v = by[s0 + 204 * r]; h += (v == 0x47 || v == 0xB8); }
                        if (h > score) score = h;
                    }
                }
                free(by);
                if (score > best_score) { best_score = score; best_rot = rot; best_ph = ph; best_off = off; }
            }
    fprintf(stderr, "{\"sync\":{\"rotation\":%d,\"punct_phase\":%d,\"bit_offset\":%d,\"sync_hits_of_20\":%d}", best_rot, best_ph, best_off, best_score);
    /* voller Lauf; Bit-Versatz im Byte-Raster: alle 8 Lagen nach dem Viterbi prüfen */
    size_t m = 0;
    for (size_t k = 0; k < ns; k++) {
        int I = iq[2 * k], Q = iq[2 * k + 1], a, b;
        switch (best_rot) { case 0: a = I; b = Q; break; case 1: a = Q; b = -I; break; case 2: a = -I; b = -Q; break; default: a = -Q; b = I; }
        ser[m++] = (int8_t)a; ser[m++] = (int8_t)b;
    }
    size_t no = depuncture(ser + best_off, m - best_off, cr, best_ph, xy);
    viterbi(xy, no, bits);
    int bbo = 0, bs0 = 0, bh = -1; size_t nby = (no - 8) / 8;
    uint8_t *by = malloc(nby + 1);
    for (int bo = 0; bo < 8; bo++) {
        for (size_t q = 0; q < nby; q++) { uint8_t v = 0; for (int j = 0; j < 8; j++) v = (uint8_t)((v << 1) | bits[8 * q + j + bo]); by[q] = v; }
        for (int s0 = 0; s0 < 204; s0++) { int h = 0; for (int r = 0; r < 64 && s0 + 204 * r < (int)nby; r++) { uint8_t v = by[s0 + 204 * r]; h += (v == 0x47 || v == 0xB8); } if (h > bh) { bh = h; bbo = bo; bs0 = s0; } }
    }
    for (size_t q = 0; q < nby; q++) { uint8_t v = 0; for (int j = 0; j < 8; j++) v = (uint8_t)((v << 1) | bits[8 * q + j + bbo]); by[q] = v; }
    {   /* 180°-Mehrdeutigkeit: der Code ist transparent → invertierte Daten (0x47↔0xB8). Mehrheit 0xB8 → invertieren */
        int n47 = 0, nb8 = 0;
        for (size_t r = 0; bs0 + 204 * r < nby; r++) { uint8_t v = by[bs0 + 204 * r]; n47 += (v == 0x47); nb8 += (v == 0xB8); }
        if (nb8 > n47) { for (size_t q = 0; q < nby; q++) by[q] = (uint8_t)~by[q]; fprintf(stderr, ",\"inverted\":true"); }
    }
    /* Deinterleaver: out[p][i] = in[p + (i mod 12)·? ] – Zweig j hat Verzögerung (11−j) Pakete beim Empfänger */
    uint8_t *il = by + bs0; size_t npk = (nby - bs0) / 204;
    long ok = 0, bad = 0; FILE *fo = fopen(outp, "wb");
    dvb_scrambler_t sc; dvb_scrambler_init(&sc); int started = 0; long b8 = 0;
    for (size_t p = 11; p < npk; p++) {
        uint8_t pk[204];
        for (int i = 0; i < 204; i++) { int j = i % 12; pk[i] = il[(p - 11 + j) * 204 + i]; }
        /* Hinweis: Senderzweig j verzögert um j Pakete → Empfänger um (11−j); Paket p−11 vollständig */
        if (dvb_rs_check(pk)) ok++; else bad++;
        if (!started) { if (pk[0] != 0xB8) continue; started = 1; sc.pkt = 0; }
        if (pk[0] == 0xB8) { b8++; sc.pkt = 0; }
        dvb_scramble_packet(&sc, pk);          /* XOR ist selbstinvers; setzt Sync auf 0xB8 bei Gruppenstart */
        pk[0] = 0x47;
        fwrite(pk, 1, 188, fo);
    }
    fclose(fo);
    fprintf(stderr, ",\"viterbi_bits\":%zu,\"byte_bitoffset\":%d,\"sync_hits_of_64\":%d,\"rs_ok\":%ld,\"rs_bad\":%ld,\"b8\":%ld}\n", no, bbo, bh, ok, bad, b8);
    return bad ? 1 : 0;
}
