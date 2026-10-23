/*
 * rrc_fir_gen – erzeugt RRC-Koeffizienten (α = 0,15, 2× Interpolation) im AD9361-filter_fir_config-Format
 * für den 1-Sample/Symbol-Betrieb von dvbc_mod. Format (Linux-Treiber ad9361.c, ad9361_parse_fir):
 *   "TX 3 GAIN 0 INT 2"  (Kanalmaske 3 = beide, GAIN 0/-6 dB, INT 1/2/4)
 *   "RX 3 GAIN -6 DEC 2" (optional; hier identische Taps, damit die Datei für beide Richtungen gültig ist)
 *   "RTX <6 Taktpfad-Raten>" / "RRX ..." / "BWTX <Hz>" / "BWRX <Hz>" (optional)
 *   Koeffizientenzeilen "tx,rx" als 16-Bit-Ganzzahlen (Q15), 16er-Vielfaches, max. 128.
 * Skalierung wie ADI (ad9361_baseband_auto_rate.c fir_128_2: Mitteltap 32767, Summe je Polyphase ≈ 32767):
 * größter Tap = 32767 → DC-Verstärkung je Phase ≈ 0,96 (−0,35 dB) bei α = 0,15; GAIN 0 dB.
 */
#include "lib/dvbmod.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

static double bessel_i0(double x)
{
    double s = 1, t = 1;
    for (int k = 1; k < 60; k++) { t *= (x / 2) / k; s += t * t; }
    return s;
}

/* Kaiser-Tiefpass für den AD9361-TX-FIR mit INT 1 (Kanalfilter, z. B. DVB-T 8 MHz: Durchlass 3,805 MHz,
   Sperre ab 4,2 MHz bei 9,142857 MS/s). Summe der Taps = 32767 → DC-Verstärkung 1 bei GAIN 0. */
static int lowpass_main(int taps, double fs, double fpass, double fstop, double atten, int with_rx, const char *out)
{
    double fc = 0.5 * (fpass + fstop) / fs;                 /* normiert auf fs */
    double beta = atten > 50 ? 0.1102 * (atten - 8.7) : (atten >= 21 ? 0.5842 * pow(atten - 21, 0.4) + 0.07886 * (atten - 21) : 0);
    double *h = malloc(taps * sizeof(double)), sum = 0, M = (taps - 1) / 2.0, i0b = bessel_i0(beta);
    for (int i = 0; i < taps; i++) {
        double t = i - M, x = 2.0 * i / (taps - 1) - 1;
        double sinc = fabs(t) < 1e-12 ? 2 * fc : sin(2 * M_PI * fc * t) / (M_PI * t);
        h[i] = sinc * bessel_i0(beta * sqrt(1 - x * x)) / i0b; sum += h[i];
    }
    int16_t *q = malloc(taps * sizeof(int16_t)); double qs = 0;
    for (int i = 0; i < taps; i++) { q[i] = (int16_t)lrint(h[i] / sum * 32767.0); qs += q[i]; }
    double pb_min = 1e9, pb_max = -1e9, sb_max = -1e9, at42 = 0, at6 = 0;
    for (int fi = 0; fi <= 2000; fi++) {
        double f = fs / 2 * fi / 2000.0, re = 0, im = 0;
        for (int i = 0; i < taps; i++) { re += q[i] * cos(2 * M_PI * f * i / fs); im -= q[i] * sin(2 * M_PI * f * i / fs); }
        double mag = 20 * log10(sqrt(re * re + im * im) / 32768.0 + 1e-12);
        if (f <= fpass) { if (mag < pb_min) pb_min = mag; if (mag > pb_max) pb_max = mag; }
        if (f >= fstop && mag > sb_max) sb_max = mag;
        if (fabs(f - 4.2e6 * fs / 9142857.142857) < fs / 4000) at42 = mag;
        if (fabs(f - 6.0e6 * fs / 9142857.142857) < fs / 4000) at6 = mag;
    }
    FILE *fp = out ? fopen(out, "w") : stdout;
    if (!fp) { perror(out); return 1; }
    fprintf(fp, "# Kanal-Tiefpass (Kaiser, beta %.2f) %d taps, INT 1, fs %.0f Hz, Durchlass %.0f Hz, Sperre ab %.0f Hz (rrc_fir_gen --lowpass)\n", beta, taps, fs, fpass, fstop);
    fprintf(fp, "# Summe der Taps %.0f (DC %.3f dB), Durchlass %.3f..%.3f dB, Sperrbereich max %.1f dB\n", qs, 20 * log10(qs / 32768.0), pb_min, pb_max, sb_max);
    fprintf(fp, "TX 3 GAIN 0 INT 1\n");
    if (with_rx) fprintf(fp, "RX 3 GAIN 0 DEC 1\n");
    for (int i = 0; i < taps; i++) fprintf(fp, with_rx ? "%d,%d\n" : "%d\n", q[i], q[i]);
    if (fp != stdout) fclose(fp);
    fprintf(stderr, "{\"mode\":\"lowpass\",\"taps\":%d,\"beta\":%.3f,\"tap_sum\":%.0f,\"passband_ripple_db\":[%.4f,%.4f],\"stopband_max_db\":%.2f,\"at_4p2MHz_db\":%.2f,\"at_6MHz_db\":%.2f}\n",
            taps, beta, qs, pb_min, pb_max, sb_max, at42, at6);
    free(h); free(q);
    return 0;
}

int main(int argc, char **argv)
{
    int taps = 128, interp = 2, gain_db = 0, with_rx = 1, kaiser = 0; double alpha = 0.15, sr = 6.9e6, beta = 0;
    const char *out = NULL;
    int lowpass = 0; double lp_fs = 64e6 / 7, lp_pass = 3.805e6, lp_stop = 4.2e6, lp_att = 70;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--taps") && i + 1 < argc) taps = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--alpha") && i + 1 < argc) alpha = atof(argv[++i]);
        else if (!strcmp(argv[i], "--sr") && i + 1 < argc) sr = atof(argv[++i]);
        else if (!strcmp(argv[i], "--gain") && i + 1 < argc) gain_db = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--kaiser") && i + 1 < argc) { kaiser = 1; beta = atof(argv[++i]); }
        else if (!strcmp(argv[i], "--no-rx")) with_rx = 0;
        else if (!strcmp(argv[i], "--lowpass")) lowpass = 1;
        else if (!strcmp(argv[i], "--fs") && i + 1 < argc) lp_fs = atof(argv[++i]);
        else if (!strcmp(argv[i], "--pass") && i + 1 < argc) lp_pass = atof(argv[++i]);
        else if (!strcmp(argv[i], "--stop") && i + 1 < argc) lp_stop = atof(argv[++i]);
        else if (!strcmp(argv[i], "--atten") && i + 1 < argc) lp_att = atof(argv[++i]);
        else if (!strcmp(argv[i], "-o") && i + 1 < argc) out = argv[++i];
        else { fprintf(stderr, "rrc_fir_gen [--taps 128] [--alpha 0.15] [--sr 6900000] [--gain 0|-6] [--kaiser BETA] [--no-rx] [-o datei]\n"
                           "rrc_fir_gen --lowpass [--fs 9142857] [--pass 3805000] [--stop 4200000] [--atten 70] [--taps 128] [-o datei]\n"); return 2; }
    }
    if (lowpass) return lowpass_main(taps, lp_fs, lp_pass, lp_stop, lp_att, with_rx, out);
    if (taps % 16 || taps > 128 || taps < 16) { fprintf(stderr, "taps: 16er-Vielfaches, max 128\n"); return 2; }
    double *h = malloc(taps * sizeof(double));
    rrc_design(alpha, interp, taps, h);
    if (kaiser) {   /* optionales Kaiser-Fenster gegen Abbruchwelligkeit */
        double i0b = 1, t = 1; for (int k = 1; k < 50; k++) { t *= (beta / 2) / k; i0b += t * t; }
        for (int i = 0; i < taps; i++) {
            double x = 2.0 * i / (taps - 1) - 1, a = beta * sqrt(1 - x * x), i0 = 1; t = 1;
            for (int k = 1; k < 50; k++) { t *= (a / 2) / k; i0 += t * t; }
            h[i] *= i0 / i0b;
        }
    }
    double mx = 0; for (int i = 0; i < taps; i++) if (fabs(h[i]) > mx) mx = fabs(h[i]);
    int16_t *q = malloc(taps * sizeof(int16_t));
    double s0 = 0, s1 = 0;
    for (int i = 0; i < taps; i++) { q[i] = (int16_t)lrint(h[i] * 32767.0 / mx); if (i & 1) s1 += q[i]; else s0 += q[i]; }
    /* Frequenzgang: Bildunterdrückung (f ≥ SR − 0,575·SR … ) und Passband-Welligkeit */
    double fs = sr * interp, worst_img = 0, pb_min = 1e9, pb_max = -1e9;
    for (int fi = 0; fi <= 400; fi++) {
        double f = fs / 2 * fi / 400.0, re = 0, im = 0;
        for (int i = 0; i < taps; i++) { re += q[i] * cos(2 * M_PI * f * i / fs); im -= q[i] * sin(2 * M_PI * f * i / fs); }
        double mag = 20 * log10(sqrt(re * re + im * im) / 65536.0 + 1e-12);
        if (f <= (1 - alpha) / 2 * sr) { if (mag < pb_min) pb_min = mag; if (mag > pb_max) pb_max = mag; }
        if (f >= (1 + alpha) / 2 * sr && mag > worst_img) worst_img = mag;
    }
    FILE *fp = out ? fopen(out, "w") : stdout;
    if (!fp) { perror(out); return 1; }
    fprintf(fp, "# RRC alpha=%.2f, %d taps, INT %d, SR %.0f Bd (FIR-Takt %.0f Hz), erzeugt von rrc_fir_gen (dvbmod_lab)\n", alpha, taps, interp, sr, fs);
    fprintf(fp, "# Skalierung: max. Tap 32767 (Q15), Summe gerade Taps %.0f, ungerade %.0f (DC-Verstaerkung je Phase %.3f/%.3f)\n", s0, s1, s0 / 32768, s1 / 32768);
    fprintf(fp, "# Passband (0..%.0f Hz): %.2f..%.2f dB, Rest-Spektrum ab %.0f Hz: max %.1f dB\n", (1 - alpha) / 2 * sr, pb_min, pb_max, (1 + alpha) / 2 * sr, worst_img);
    fprintf(fp, "TX 3 GAIN %d INT %d\n", gain_db, interp);
    if (with_rx) fprintf(fp, "RX 3 GAIN -6 DEC %d\n", interp);
    fprintf(fp, "BWTX %.0f\n", sr * (1 + alpha));
    if (with_rx) fprintf(fp, "BWRX %.0f\n", sr * (1 + alpha));
    for (int i = 0; i < taps; i++) fprintf(fp, with_rx ? "%d,%d\n" : "%d\n", q[i], q[i]);
    if (fp != stdout) fclose(fp);
    fprintf(stderr, "{\"taps\":%d,\"max_tap\":32767,\"phase_gain\":[%.4f,%.4f],\"passband_ripple_db\":[%.3f,%.3f],\"stopband_max_db\":%.2f}\n",
            taps, s0 / 32768, s1 / 32768, pb_min, pb_max, worst_img);
    free(h); free(q);
    return 0;
}
