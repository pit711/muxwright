/*
 * dvbc_mod – DVB-C-Modulator (ETSI EN 300 429 V1.2.1, Annex A / ITU-T J.83 A) für den ADALM-Pluto.
 * TS → Energieverwürfelung → RS(204,188) → Faltungsinterleaver I=12 → Byte→m-Tupel → Differenzcodierung
 * → QAM (16…256) → (a) RRC α=0,15 bei 2 Samples/Symbol oder (b) 1 Sample/Symbol (RRC im AD9361-TX-FIR,
 * Koeffizienten aus rrc_fir_gen).
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
"dvbc_mod – DVB-C-Modulator (EN 300 429 Annex A)\n"
"  -i SPEC        TS-Eingang: Datei | - | udp://[addr]:port | prbs[:N] | null   (Standard prbs)\n"
"  -o PATH        Ausgabe: Datei | - | iio: | iio:URI                              (Standard -)\n"
"  --qam 16|32|64|128|256   --sr SYMBOLRATE (Baud, 1e6..7e6, Standard 6900000)\n"
"  --sps 1|2      Samples je Symbol: 2 = RRC im Programm (Ausgaberate 2·SR), 1 = RRC im AD9361-FIR\n"
"  --taps N       RRC-Länge bei --sps 2 (Standard 128)   --backoff dB  (Standard 10)\n"
"  --packets N | --seconds S   Dauer      --loop   Datei wiederholen    --realtime   stdout bremsen\n"
"  --udp-buffer N  UDP-Ring in Paketen    --quiet\n"
"  IIO: --lo HZ --atten DB --rfbw HZ --iio-buf SAMPLES --iio-kbufs N --cyclic --enable --fir DATEI\n");
}

int main(int argc, char **argv)
{
    int qam = 256, sps = 2, taps = 128, loop = 0, realtime = 0, quiet = 0, udp_buffer = 2000;
    double sr = 6.9e6, backoff = 10, max_seconds = 0; long long max_packets = 0;
    const char *in_spec = "prbs", *out_path = "-";
    out_params_t op; memset(&op, 0, sizeof op); op.atten_db = 89.75; op.kernel_buffers = 4;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
#define ARG(name) (!strcmp(a, name) && i + 1 < argc)
        if (ARG("-i")) in_spec = argv[++i];
        else if (ARG("-o")) out_path = argv[++i];
        else if (ARG("--qam")) qam = atoi(argv[++i]);
        else if (ARG("--sr")) sr = atof(argv[++i]);
        else if (ARG("--sps")) sps = atoi(argv[++i]);
        else if (ARG("--taps")) taps = atoi(argv[++i]);
        else if (ARG("--backoff")) backoff = atof(argv[++i]);
        else if (ARG("--packets")) max_packets = atoll(argv[++i]);
        else if (ARG("--seconds")) max_seconds = atof(argv[++i]);
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
    int m; switch (qam) { case 16: m = 4; break; case 32: m = 5; break; case 64: m = 6; break; case 128: m = 7; break; case 256: m = 8; break; default: usage(); return 2; }
    if (sps != 1 && sps != 2) { usage(); return 2; }
    double bitrate = sr * m * 188.0 / 204.0;
    double sample_rate = sr * sps;
    /* Pegel: Symbol-RMS = A; nach RRC (Verstärkung g je Phase, Energie Σh²/sps) RMS_out ≈ A·sqrt(Σh²/sps)·(32767/max) */
    double rms_target = 32767.0 * pow(10.0, -backoff / 20.0);
    rrc_t rrc; double gain_rrc = 1.0;
    if (sps == 2) {
        if (rrc_init(&rrc, 0.15, 2, taps) < 0) { fprintf(stderr, "taps muss gerade sein\n"); return 1; }
        double e = 0; for (int i = 0; i < taps; i++) e += (double)rrc.taps[i] * rrc.taps[i];
        gain_rrc = sqrt(e / sps) / 32768.0;
    }
    int A = (int)lrint(rms_target / gain_rrc);
    /* Spitzenwert der Konstellation darf int16 nicht sprengen: max |I| = (sqrt(M)-1)/RMS·A */
    double peak = ((qam == 32) ? 5 : (qam == 128) ? 11 : (sqrt((double)qam) - 1)) / dvbc_map_rms(qam);
    if (A * peak > 32000) A = (int)(32000 / peak);

    dvb_rs_init(); dvb_scrambler_t scr; dvb_scrambler_init(&scr); dvb_convint_t ci; dvb_convint_init(&ci);
    {   /* Interleaver-Historie mit verwürfelten Nullpaketen vorbelegen (keine Nullfolgen am Anfang) */
        dvb_scrambler_t s2; dvb_scrambler_init(&s2); uint8_t p[RS_PKT], o[RS_PKT];
        for (int i = 0; i < 11; i++) { memcpy(p, "Gÿ", 4); memset(p + 4, 0xff, 184); dvb_scramble_packet(&s2, p); dvb_rs_encode(p, p); dvb_convint_packet(&ci, p, o); }
    }
    dvbc_map_t map; dvbc_map_init(&map, qam, A);
    ts_input_t *ts = ts_input_open(in_spec, loop, udp_buffer, 0);
    if (!ts) { fprintf(stderr, "Eingang %s nicht zu öffnen\n", in_spec); return 1; }
    op.path = out_path; op.sample_rate = sample_rate;
    if (op.rf_bw_hz <= 0) op.rf_bw_hz = sr * 1.15;
    if (op.buf_samples <= 0) op.buf_samples = 65536;
    out_sink_t *out = out_open(&op);
    if (!out) { fprintf(stderr, "Ausgabe %s nicht zu öffnen\n", out_path); return 1; }
    if (realtime) out_set_realtime(out, 1);
    if (!quiet)
        fprintf(stderr, "{\"qam\":%d,\"symbol_rate\":%.0f,\"sps\":%d,\"sample_rate\":%.0f,\"bitrate_bps\":%.3f,\"A\":%d,\"backoff_db\":%g,"
                "\"symbols_per_8pkt\":%d,\"samples_per_8pkt\":%d}\n", qam, sr, sps, sample_rate, bitrate, A, backoff, 8 * 204 * 8 / m, 8 * 204 * 8 / m * sps);
    signal(SIGINT, on_sig); signal(SIGTERM, on_sig);
    uint8_t pkt[RS_PKT], il[RS_PKT];
    int16_t sym[2 * RS_PKT * 2], smp[2 * RS_PKT * 2 * 2];
    long long npk = 0; double t0 = dvb_now(), last = t0, cpu0 = dvb_cpu_time();
    uint64_t total_samples = 0;
    while (!stop_flag) {
        if (max_packets && npk >= max_packets) break;
        if (max_seconds > 0 && (double)total_samples / sample_rate >= max_seconds && (npk % 8) == 0) break;
        if (!ts_input_packet(ts, pkt)) {
            if (npk % 8 == 0) break;             /* nur ganze 8er-Gruppen */
            memcpy(pkt, "\x47\x1f\xff\x10", 4); memset(pkt + 4, 0xff, 184);
        }
        dvb_scramble_packet(&scr, pkt); dvb_rs_encode(pkt, pkt); dvb_convint_packet(&ci, pkt, il);
        size_t ns = dvbc_map_bytes(&map, il, RS_PKT, sym);
        const int16_t *o = sym; size_t n = ns;
        if (sps == 2) { n = rrc_run(&rrc, sym, ns, smp); o = smp; }
        if (out_write(out, o, n) < 0) { fprintf(stderr, "Schreibfehler\n"); break; }
        total_samples += n; npk++;
        if (!quiet && (npk & 1023) == 0) {
            double now = dvb_now();
            if (now - last >= 1.0) {
                ts_stats_t st; ts_input_stats(ts, &st); last = now;
                fprintf(stderr, "{\"t\":%.1f,\"packets\":%lld,\"signal_s\":%.2f,\"rt_factor\":%.2f,\"cpu\":%.2f,\"ts_in\":%llu,\"stuffed\":%llu,\"dropped\":%llu,\"fill\":%d}\n",
                        now - t0, npk, total_samples / sample_rate, total_samples / sample_rate / (now - t0), (dvb_cpu_time() - cpu0) / (now - t0),
                        (unsigned long long)st.packets_in, (unsigned long long)st.nulls_stuffed, (unsigned long long)st.nulls_dropped, st.fill);
            }
        }
    }
    double el = dvb_now() - t0;
    fprintf(stderr, "{\"done\":true,\"packets\":%lld,\"samples\":%llu,\"signal_s\":%.3f,\"wall_s\":%.3f,\"rt_factor\":%.3f,\"cpu_s\":%.3f}\n",
            npk, (unsigned long long)total_samples, total_samples / sample_rate, el, total_samples / sample_rate / (el > 0 ? el : 1), dvb_cpu_time() - cpu0);
    out_close(out); ts_input_close(ts); if (sps == 2) rrc_free(&rrc);
    return 0;
}
