/*
 * dvbmod_selftest – Bausteinprüfung ohne Signalweg: PRBS-Sequenz, RS-Syndrom, Interleaver-Verzögerung,
 * Faltungscoder-Bitzahl, Pilot-/Datenträgerzählung, Symbol-Interleaver-Permutation, IFFT-Genauigkeit.
 * Gibt Zwischenergebnisse als Hex/Zahlen aus, die tests/selftest_check.py mit numpy gegenrechnet.
 */
#include "../lib/dvbmod.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>


/* ---- Mikrobenchmark je Stufe (argv[1] == "micro") ---- */
static void microbench(void)
{
    const int NP = 4000;
    uint8_t pkt[204], il[204], punct[8192], yp[6048], y[6048];
    dvb_rs_init();
    dvb_scrambler_t scr; dvb_scrambler_init(&scr);
    dvb_convint_t ci; dvb_convint_init(&ci);
    dvb_convcode_t cc; dvb_convcode_init(&cc, CR_7_8);
    ts_input_t *ts = ts_input_open("prbs", 0, 0, 0);
    double t0, t1; volatile uint32_t sink = 0;
    t0 = dvb_now(); for (int i = 0; i < NP; i++) { ts_input_packet(ts, pkt); sink += pkt[7]; } t1 = dvb_now();
    printf("micro_prbs_pkt_us=%.2f\n", 1e6 * (t1 - t0) / NP);
    t0 = dvb_now(); for (int i = 0; i < NP; i++) { dvb_scramble_packet(&scr, pkt); sink += pkt[7]; } t1 = dvb_now();
    printf("micro_scramble_pkt_us=%.2f\n", 1e6 * (t1 - t0) / NP);
    t0 = dvb_now(); for (int i = 0; i < NP; i++) { dvb_rs_encode(pkt, pkt); sink += pkt[200]; } t1 = dvb_now();
    printf("micro_rs_pkt_us=%.2f\n", 1e6 * (t1 - t0) / NP);
    t0 = dvb_now(); for (int i = 0; i < NP; i++) { dvb_convint_packet(&ci, pkt, il); sink += il[7]; } t1 = dvb_now();
    printf("micro_convint_pkt_us=%.2f\n", 1e6 * (t1 - t0) / NP);
    t0 = dvb_now(); for (int i = 0; i < NP; i++) { sink += (uint32_t)dvb_convcode_bytes(&cc, il, 204, punct); } t1 = dvb_now();
    printf("micro_convcode78_pkt_us=%.2f\n", 1e6 * (t1 - t0) / NP);
    dvbt_inner_t in; dvbt_inner_init(&in, DVBT_8K, DVBT_64QAM, 6000);
    dvbt_frame_t fr; dvbt_frame_init(&fr, DVBT_8K, GI_1_32, DVBT_64QAM, CR_7_8, 0, 6000);
    int16_t *re = calloc(8192, 4), *out = malloc(8448 * 4);
    fft16_t *f = fft16_create(8192);
    const int NS = 300;
    t0 = dvb_now(); for (int i = 0; i < NS; i++) { dvbt_bit_interleave(&in, punct, yp); sink += yp[5]; } t1 = dvb_now();
    printf("micro_bit_il_sym_us=%.1f\n", 1e6 * (t1 - t0) / NS);
    t0 = dvb_now(); for (int i = 0; i < NS; i++) { dvbt_symbol_interleave(&in, yp, y, i & 1); sink += y[5]; } t1 = dvb_now();
    printf("micro_sym_il_sym_us=%.1f\n", 1e6 * (t1 - t0) / NS);
    t0 = dvb_now(); for (int i = 0; i < NS; i++) { dvbt_frame_build(&fr, &in, y, re); sink += re[5]; } t1 = dvb_now();
    printf("micro_frame_sym_us=%.1f\n", 1e6 * (t1 - t0) / NS);
    t0 = dvb_now(); for (int i = 0; i < NS; i++) { fft16_ifft_ofdm(f, re, out, 256); sink += out[5]; } t1 = dvb_now();
    printf("micro_ifft8k_sym_us=%.1f\n", 1e6 * (t1 - t0) / NS);
    printf("micro_sink=%u\n", sink);
    ts_input_close(ts); dvbt_inner_free(&in); dvbt_frame_free(&fr); fft16_free(f); free(re); free(out);
}
int main(int argc, char **argv)
{
    int fail = 0;
    if (argc > 1 && !strcmp(argv[1], "micro")) { microbench(); return 0; }
    /* 1. Energieverwürfelung: erste PRBS-Bytes 03 F6 08 34 30 B8 A3 93 */
    dvb_scrambler_t s; dvb_scrambler_init(&s);
    printf("prbs_first8="); for (int i = 1; i <= 8; i++) printf("%02x", s.seq[i]); printf("\n");
    static const uint8_t ref[8] = {0x03, 0xF6, 0x08, 0x34, 0x30, 0xB8, 0xA3, 0x93};
    if (memcmp(s.seq + 1, ref, 8)) { printf("FAIL prbs\n"); fail++; }
    /* Sync-Bytes der Pakete 2..8 unverändert, Periode 1503 */
    for (int p = 1; p < 8; p++) if (s.seq[p * 188]) { printf("FAIL sync byte %d\n", p); fail++; }

    /* 2. RS: zufälliges Paket codieren, Syndrom prüfen, ein Byte stören → Syndrom ≠ 0 */
    dvb_rs_init();
    uint8_t pkt[204]; for (int i = 0; i < 188; i++) pkt[i] = (uint8_t)(i * 37 + 11);
    dvb_rs_encode(pkt, pkt);
    printf("rs_parity="); for (int i = 188; i < 204; i++) printf("%02x", pkt[i]); printf("\n");
    if (!dvb_rs_check(pkt)) { printf("FAIL rs syndrome\n"); fail++; }
    pkt[5] ^= 1; if (dvb_rs_check(pkt)) { printf("FAIL rs detect\n"); fail++; } pkt[5] ^= 1;
    /* Vektor für Python: Nullpaket (0x47, 0x1F, 0xFF, 0x10, 0xFF...) */
    uint8_t np[204]; memcpy(np, "\x47\x1f\xff\x10", 4); memset(np + 4, 0xff, 184); dvb_rs_encode(np, np);
    printf("rs_null_parity="); for (int i = 188; i < 204; i++) printf("%02x", np[i]); printf("\n");

    /* 3. Interleaver: Byte i verzögert um (i mod 12) Pakete */
    dvb_convint_t ci; dvb_convint_init(&ci);
    uint8_t ipk[204], opk[204]; int ok = 1;
    for (int p = 0; p < 30; p++) {
        for (int i = 0; i < 204; i++) ipk[i] = (uint8_t)(p * 7 + i);
        dvb_convint_packet(&ci, ipk, opk);
        if (p >= 12) for (int i = 0; i < 204; i++) { if (opk[i] != (uint8_t)((p - i % 12) * 7 + i)) ok = 0; }
    }
    printf("convint_delay_ok=%d\n", ok); if (!ok) fail++;

    /* 4. Faltungscoder: Bitzahl je Coderate, Ausgabe für einen Testvektor */
    for (int cr = 0; cr < 5; cr++) {
        dvb_convcode_t cc; dvb_convcode_init(&cc, cr);
        uint8_t src[105], dst[105 * 2 + 4]; for (int i = 0; i < 105; i++) src[i] = (uint8_t)(i * 29 + 3);
        size_t n = dvb_convcode_bytes(&cc, src, 105, dst);
        int bits = (int)n * 8 + cc.accn;
        int expect = 105 * 8 * dvb_coderate_den[cr] / dvb_coderate_num[cr];
        printf("cc_%s_bits=%d expect=%d out=", dvb_coderate_name[cr], bits, expect);
        for (size_t i = 0; i < n && i < 16; i++) printf("%02x", dst[i]); printf("\n");
        if (bits != expect) { printf("FAIL cc bits\n"); fail++; }
    }

    /* 5. Rahmen: Träger zählen, H(q) ausgeben (Anfang), w_k ausgeben */
    for (int mode = 0; mode < 2; mode++) {
        dvbt_frame_t f; int r = dvbt_frame_init(&f, mode, GI_1_32, DVBT_64QAM, CR_2_3, 0, 1000);
        printf("frame_%s_init=%d ndata=%d npil=%d,%d,%d,%d ntps=%d meanP=%.3f\n", mode ? "8k" : "2k", r, f.ndata,
               f.npil[0], f.npil[1], f.npil[2], f.npil[3], f.ntps, f.mean_cell_power);
        if (r) fail++;
        printf("wk_%s_first32=", mode ? "8k" : "2k"); for (int k = 0; k < 32; k++) printf("%d", f.wk[k]); printf("\n");
        dvbt_inner_t in; dvbt_inner_init(&in, mode, DVBT_64QAM, 1000);
        printf("H_%s_first16=", mode ? "8k" : "2k"); for (int q = 0; q < 16; q++) printf("%d,", in.H[q]); printf("\n");
        /* Permutation prüfen: jede Zelle genau einmal */
        uint8_t *seen = calloc(in.ncells, 1); int dup = 0;
        for (int q = 0; q < in.ncells; q++) { if (seen[in.H[q]]) dup++; seen[in.H[q]] = 1; }
        printf("H_%s_perm_ok=%d\n", mode ? "8k" : "2k", dup == 0); if (dup) fail++;
        free(seen);
        /* TPS-Bits Rahmen 0 */
        f.frame = 0; dvbt_tps_generate(&f);
        printf("tps_%s_frame0=", mode ? "8k" : "2k"); for (int i = 0; i < 68; i++) printf("%d", f.tps_bits[i]); printf("\n");
        dvbt_inner_free(&in); dvbt_frame_free(&f);
    }
    /* Mapper-Tabelle 64-QAM */
    dvbt_inner_t in; dvbt_inner_init(&in, DVBT_8K, DVBT_64QAM, 1000);
    printf("map64="); for (int w = 0; w < 64; w++) printf("%d/%d,", in.map_re[w], in.map_im[w]); printf("\n");
    dvbt_inner_free(&in);

    /* 6. IFFT: Einzelträger und Vergleich mit Gleitkomma-DFT auf Zufallsvektor */
    for (int n = 2048; n <= 8192; n *= 4) {
        fft16_t *f = fft16_create(n);
        int16_t *iq = calloc(n, 4), *out = malloc((n + n / 4) * 4);
        int16_t *re = calloc(n, 2), *im = calloc(n, 2);
        unsigned seed = 12345;
        double *fr = malloc(n * sizeof(double)), *fi = malloc(n * sizeof(double));
        for (int k = 0; k < n; k++) {
            seed = seed * 1103515245u + 12345u; re[k] = (int16_t)(((int)(seed >> 16) & 0x3FFF) - 8192);
            seed = seed * 1103515245u + 12345u; im[k] = (int16_t)(((int)(seed >> 16) & 0x3FFF) - 8192);
        }
        int guard = n / 4;
        for (int k = 0; k < n; k++) { iq[2 * k] = re[k]; iq[2 * k + 1] = im[k]; }
        fft16_ifft_ofdm(f, iq, out, guard);
        /* Referenz: x[t] = Σ X[k] e^{+j2πkt/n} / 2^S */
        double S = (double)(1 << fft16_shift_bits(f)), err = 0, pw = 0;
        for (int t = 0; t < n; t += 97) {
            double sr = 0, si = 0;
            for (int k = 0; k < n; k++) { double a = 2 * M_PI * (double)k * t / n; double c = cos(a), s_ = sin(a); sr += re[k] * c - im[k] * s_; si += re[k] * s_ + im[k] * c; }
            sr /= S; si /= S;
            double dr = out[2 * (guard + t)] - sr, di = out[2 * (guard + t) + 1] - si;
            err += dr * dr + di * di; pw += sr * sr + si * si;
        }
        int gok = memcmp(out, out + 2 * n, guard * 4) == 0;
        uint32_t crc = 0; for (int i = 0; i < 2 * (n + guard); i++) crc = crc * 16777619u ^ (uint16_t)out[i];
        printf("ifft_%d_crc=%08x\n", n, crc);
        printf("ifft_%d_snr_db=%.1f guard_ok=%d\n", n, 10 * log10(pw / (err + 1e-12)), gok);
        if (10 * log10(pw / (err + 1e-12)) < 55 || !gok) { printf("FAIL ifft\n"); fail++; }
        (void)fr; (void)fi; free(fr); free(fi); free(re); free(im); free(iq); free(out); fft16_free(f);
    }
    printf(fail ? "SELFTEST FAIL (%d)\n" : "SELFTEST OK\n", fail);
    return fail != 0;
}
