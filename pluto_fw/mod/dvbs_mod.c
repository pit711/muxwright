/*
 * dvbs_mod – DVB-S-Modulator (ETSI EN 300 421 V1.1.2) für den ADALM-Pluto.
 * TS → Energieverwürfelung → RS(204,188) → Faltungsinterleaver I=12 → Faltungscode K=7 (171/133 oktal) mit Punktierung
 * 1/2 … 7/8 → serieller Bitstrom paarweise auf (I,Q) (Tabelle 2: z. B. 3/4: I = X1 Y2, Q = Y1 X3) → QPSK Gray
 * (Bit 0 → +, Bit 1 → −) → (a) RRC α=0,35 bei 2 Samples/Symbol oder (b) 1 Sample/Symbol mit RRC im AD9361-TX-FIR
 * (rrc_fir_gen --alpha 0.35, INT 2).
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "lib/dvbmod.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <signal.h>

static volatile sig_atomic_t stop_flag;
static void on_sig(int s) { (void)s; stop_flag = 1; }

static void usage(void)
{
    fprintf(stderr,
"dvbs_mod – DVB-S-Modulator (EN 300 421)\n"
"  -i SPEC        TS-Eingang: Datei | - | udp://[addr]:port | prbs[:N] | null   (Standard prbs)\n"
"  -o PATH        Ausgabe: Datei | - | iio: | iio:URI                              (Standard -)\n"
"  --sr BAUD      Symbolrate (Standard 27500000)   --rate 1/2|2/3|3/4|5/6|7/8 (Standard 3/4)\n"
"  --sps 1|2|4|8  1 = RRC im AD9361-FIR (Ausgaberate = SR), 2/4/8 = RRC α=0,35 im Programm (kleine Symbolraten, z. B. DATV)\n"
"  --taps N       RRC-Länge bei --sps ≥ 2 (Standard 32·sps)   --backoff dB (Standard 6)\n"
"  --packets N | --seconds S  --loop  --realtime  --udp-buffer N  --quiet  --bench N (Pakete, ohne Ausgabe)\n"
"  IIO: --lo HZ --atten DB --rfbw HZ --iio-buf SAMPLES --iio-kbufs N --cyclic --enable --fir DATEI\n");
}

int main(int argc, char **argv)
{
    int sps = 1, taps = 0, loop = 0, realtime = 0, quiet = 0, udp_buffer = 4000, cr = CR_3_4;
    double sr = 27.5e6, backoff = 6, max_seconds = 0; long long max_packets = 0, bench = 0;
    const char *in_spec = "prbs", *out_path = "-";
    out_params_t op; memset(&op, 0, sizeof op); op.atten_db = 89.75; op.kernel_buffers = 4;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
#define ARG(name) (!strcmp(a, name) && i + 1 < argc)
        if (ARG("-i")) in_spec = argv[++i];
        else if (ARG("-o")) out_path = argv[++i];
        else if (ARG("--sr")) sr = atof(argv[++i]);
        else if (ARG("--rate")) { const char *r = argv[++i]; cr = -1; for (int k = 0; k < 5; k++) if (!strcmp(r, dvb_coderate_name[k])) cr = k; if (cr < 0) { usage(); return 2; } }
        else if (ARG("--sps")) sps = atoi(argv[++i]);
        else if (ARG("--taps")) taps = atoi(argv[++i]);
        else if (ARG("--backoff")) backoff = atof(argv[++i]);
        else if (ARG("--packets")) max_packets = atoll(argv[++i]);
        else if (ARG("--seconds")) max_seconds = atof(argv[++i]);
        else if (ARG("--bench")) { bench = atoll(argv[++i]); out_path = NULL; }
        else if (!strcmp(a, "--loop")) loop = 1;
        else if (!strcmp(a, "--realtime")) realtime = 1;
        else if (!strcmp(a, "--quiet")) quiet = 1;
        else if (ARG("--udp-buffer")) udp_buffer = atoi(argv[++i]);
        else if (ARG("--lo")) op.lo_hz = atof(argv[++i]);
        else if (ARG("--atten")) op.atten_db = atof(argv[++i]);
        else if (ARG("--rfbw")) op.rf_bw_hz = atof(argv[++i]);
        else if (ARG("--iio-buf")) op.buf_samples = atoi(argv[++i]);
        else if (ARG("--iio-kbufs")) op.kernel_buffers = atoi(argv[++i]);
        else if (!strcmp(a, "--cyclic")) op.cyclic = 1;
        else if (!strcmp(a, "--enable")) op.enable = 1;
        else if (ARG("--fir")) op.fir_file = argv[++i];
        else { usage(); return 2; }
    }
    if (sps != 1 && sps != 2 && sps != 4 && sps != 8) { usage(); return 2; }
    if (taps <= 0) taps = 32 * sps;
    const double bitrate = sr * 2.0 * dvb_coderate_num[cr] / dvb_coderate_den[cr] * 188.0 / 204.0;
    const double sample_rate = sr * sps;
    /* Pegel: QPSK-Symbole mit Betrag A; nach RRC (sps 2) bzw. AD9361-FIR (sps 1) RMS ≈ A */
    const double rms_target = 32767.0 * pow(10.0, -backoff / 20.0);
    rrc_t rrc; double gain_rrc = 1.0;
    if (sps > 1) {
        if (rrc_init(&rrc, 0.35, sps, taps) < 0) { fprintf(stderr, "taps muss ein Vielfaches von sps sein\n"); return 1; }
        double e = 0; for (int i = 0; i < taps; i++) e += (double)rrc.taps[i] * rrc.taps[i];
        gain_rrc = sqrt(e / sps) / 32768.0;
    }
    int A = (int)lrint(rms_target / gain_rrc / sqrt(2.0));   /* je Komponente */
    if (A > 30000) A = 30000;
    /* Tabelle: Byte (4 Bitpaare, MSB zuerst) → 4 Symbole als (I | Q<<16) */
    static uint32_t lut[256][4];
    for (int b = 0; b < 256; b++)
        for (int s = 0; s < 4; s++) {
            int bi = (b >> (7 - 2 * s)) & 1, bq = (b >> (6 - 2 * s)) & 1;
            int16_t I = (int16_t)(bi ? -A : A), Q = (int16_t)(bq ? -A : A);
            lut[b][s] = (uint16_t)I | ((uint32_t)(uint16_t)Q << 16);
        }

    dvb_rs_init(); dvb_scrambler_t scr; dvb_scrambler_init(&scr); dvb_convint_t ci; dvb_convint_init(&ci);
    {   /* Interleaver-Historie mit verwürfelten Nullpaketen vorbelegen */
        dvb_scrambler_t s2; dvb_scrambler_init(&s2); uint8_t p[RS_PKT], o[RS_PKT];
        for (int i = 0; i < 11; i++) { memcpy(p, "\x47\x1f\xff\x10", 4); memset(p + 4, 0xff, 184); dvb_scramble_packet(&s2, p); dvb_rs_encode(p, p); dvb_convint_packet(&ci, p, o); }
    }
    dvb_convcode_t cc; dvb_convcode_init(&cc, cr);
    ts_input_t *ts = ts_input_open(in_spec, loop, udp_buffer, 0);
    if (!ts) { fprintf(stderr, "Eingang %s nicht zu öffnen\n", in_spec); return 1; }
    out_sink_t *out = NULL;
    if (out_path) {
        op.path = out_path; op.sample_rate = sample_rate;
        if (op.rf_bw_hz <= 0) op.rf_bw_hz = sr * 1.35;
        if (op.buf_samples <= 0) op.buf_samples = 1 << 17;
        out = out_open(&op);
        if (!out) { fprintf(stderr, "Ausgabe %s nicht zu öffnen\n", out_path); return 1; }
        if (realtime) out_set_realtime(out, 1);
    }
    if (!quiet)
        fprintf(stderr, "{\"std\":\"DVB-S\",\"symbol_rate\":%.0f,\"code_rate\":\"%s\",\"sps\":%d,\"sample_rate\":%.0f,\"bitrate_bps\":%.3f,\"A\":%d,\"backoff_db\":%g}\n",
                sr, dvb_coderate_name[cr], sps, sample_rate, bitrate, A, backoff);
    signal(SIGINT, on_sig); signal(SIGTERM, on_sig);
    uint8_t pkt[RS_PKT], il[RS_PKT], bits[2 * RS_PKT + 8];
    static uint32_t sym[4 * (2 * RS_PKT + 8)];
    static int16_t smp[2 * 8 * 4 * (2 * RS_PKT + 8)];
    long long npk = 0; double t0 = dvb_now(), last = t0, cpu0 = dvb_cpu_time();
    uint64_t total = 0;
    while (!stop_flag) {
        if (max_packets && npk >= max_packets) break;
        if (bench && npk >= bench) break;
        if (max_seconds > 0 && (double)total / sample_rate >= max_seconds && (npk % 8) == 0) break;
        if (!ts_input_packet(ts, pkt)) {
            if (npk % 8 == 0) break;
            memcpy(pkt, "\x47\x1f\xff\x10", 4); memset(pkt + 4, 0xff, 184);
        }
        dvb_scramble_packet(&scr, pkt); dvb_rs_encode(pkt, pkt); dvb_convint_packet(&ci, pkt, il);
        size_t nb = dvb_convcode_bytes(&cc, il, RS_PKT, bits);
        for (size_t k = 0; k < nb; k++) { const uint32_t *l = lut[bits[k]]; uint32_t *d = sym + 4 * k; d[0] = l[0]; d[1] = l[1]; d[2] = l[2]; d[3] = l[3]; }
        size_t ns = 4 * nb;
        const int16_t *o = (const int16_t *)sym; size_t n = ns;
        if (sps > 1) { n = rrc_run(&rrc, (const int16_t *)sym, ns, smp); o = smp; }
        if (out && out_write(out, o, n) < 0) { fprintf(stderr, "Schreibfehler\n"); break; }
        total += n; npk++;
        if (!quiet && (npk & 4095) == 0) {
            double now = dvb_now();
            if (now - last >= 1.0) {
                ts_stats_t st; ts_input_stats(ts, &st); last = now;
                fprintf(stderr, "{\"t\":%.1f,\"packets\":%lld,\"signal_s\":%.2f,\"rt_factor\":%.2f,\"cpu\":%.2f,\"ts_in\":%llu,\"stuffed\":%llu,\"dropped\":%llu,\"fill\":%d}\n",
                        now - t0, npk, total / sample_rate, total / sample_rate / (now - t0), (dvb_cpu_time() - cpu0) / (now - t0),
                        (unsigned long long)st.packets_in, (unsigned long long)st.nulls_stuffed, (unsigned long long)st.nulls_dropped, st.fill);
            }
        }
    }
    double el = dvb_now() - t0;
    fprintf(stderr, "{\"done\":true,\"packets\":%lld,\"samples\":%llu,\"signal_s\":%.3f,\"wall_s\":%.3f,\"rt_factor\":%.3f,\"cpu_s\":%.3f,\"max_symbol_rate_1core\":%.0f}\n",
            npk, (unsigned long long)total, total / sample_rate, el, total / sample_rate / (el > 0 ? el : 1), dvb_cpu_time() - cpu0,
            el > 0 ? (double)total / sps / (dvb_cpu_time() - cpu0) : 0);
    if (out) out_close(out);
    ts_input_close(ts); if (sps > 1) rrc_free(&rrc);
    return 0;
}
