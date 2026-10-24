/*
 * dvbs2_mod – DVB-S2-Modulator (ETSI EN 302 307-1, Broadcast CCM, ein TS) für den ADALM-Pluto.
 * TS → BBFRAME (Mode-/Stream-Adaptation, BB-Scrambling) → BCH → LDPC → Bit-Interleaver → QPSK/8PSK/16APSK/32APSK
 * → PL-Framing (PLHEADER, optional Pilots, PL-Scrambling Gold n) – alles in lib/dvbs2.c – dann
 * (a) RRC bei 2 Samples/Symbol im Programm oder (b) 1 Sample/Symbol mit RRC im AD9361-TX-FIR (rrc_fir_gen --alpha …, INT 2).
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "lib/dvbmod.h"
#include "lib/dvbs2.h"
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
"dvbs2_mod – DVB-S2-Modulator (EN 302 307-1, CCM)\n"
"  -i SPEC        TS-Eingang: Datei | - | udp://[addr]:port | prbs[:N] | null   (Standard prbs)\n"
"  -o PATH        Ausgabe: Datei | - | iio: | iio:URI                              (Standard -)\n"
"  --sr BAUD      Symbolrate (Standard 22000000)\n"
"  --mod qpsk|8psk|16apsk|32apsk (Standard qpsk)   --rate 1/4 … 9/10 (Standard 3/4)\n"
"  --pilots 0|1   (Standard 1)   --short 0|1 (Standard 0)   --rolloff 0.35|0.25|0.20 (Standard 0.35)   --gold N (Standard 0)\n"
"  --sps N       1 = RRC im AD9361-FIR (Ausgaberate = SR), 2…128 = RRC im Programm (kleine Symbolraten, z. B. DATV)\n"
"  --taps N       RRC-Länge bei --sps ≥ 2 (Standard 32·sps)   --backoff dB (Standard 6)\n"
"  --frames N | --seconds S  --loop  --realtime  --udp-buffer N  --quiet  --bench N (PLFRAMEs, ohne Ausgabe)\n"
"  IIO: --lo HZ --atten DB --rfbw HZ --iio-buf SAMPLES --iio-kbufs N --cyclic --enable --fir DATEI\n");
}

typedef struct { ts_input_t *ts; int eof; } src_t;
static int src_pkt(void *ctx, uint8_t *p)
{
    src_t *c = ctx;
    if (c->eof) return 0;
    if (!ts_input_packet(c->ts, p)) { c->eof = 1; return 0; }
    return 1;
}

int main(int argc, char **argv)
{
    int sps = 1, taps = 0, loop = 0, realtime = 0, quiet = 0, udp_buffer = 4000;
    int mod = S2_QPSK, rate = R3_4, pilots = 1, shortf = 0, rolloff = S2_RO_035, gold = 0;
    double sr = 22e6, backoff = 6, max_seconds = 0; long long max_frames = 0, bench = 0;
    const char *in_spec = "prbs", *out_path = "-";
    static const double RO[3] = {0.35, 0.25, 0.20};
    out_params_t op; memset(&op, 0, sizeof op); op.atten_db = 89.75; op.kernel_buffers = 4;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
#define ARG(name) (!strcmp(a, name) && i + 1 < argc)
        if (ARG("-i")) in_spec = argv[++i];
        else if (ARG("-o")) out_path = argv[++i];
        else if (ARG("--sr")) sr = atof(argv[++i]);
        else if (ARG("--mod")) {
            const char *m = argv[++i]; mod = -1;
            for (int k = 0; k < 4; k++) if (!strcasecmp(m, s2_mod_name[k])) mod = k;
            if (mod < 0) { usage(); return 2; }
        }
        else if (ARG("--rate")) {
            const char *r = argv[++i]; rate = -1;
            for (int k = 0; k < S2_NRATE; k++) if (!strcmp(r, s2_rate_name[k])) rate = k;
            if (rate < 0) { usage(); return 2; }
        }
        else if (ARG("--pilots")) pilots = atoi(argv[++i]);
        else if (ARG("--short")) shortf = atoi(argv[++i]);
        else if (ARG("--rolloff")) {
            double r = atof(argv[++i]); rolloff = -1;
            for (int k = 0; k < 3; k++) if (fabs(r - RO[k]) < 1e-3) rolloff = k;
            if (rolloff < 0) { usage(); return 2; }
        }
        else if (ARG("--gold")) gold = atoi(argv[++i]);
        else if (ARG("--sps")) sps = atoi(argv[++i]);
        else if (ARG("--taps")) taps = atoi(argv[++i]);
        else if (ARG("--backoff")) backoff = atof(argv[++i]);
        else if (ARG("--frames")) max_frames = atoll(argv[++i]);
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
    if (sps < 1 || sps > 128) { usage(); return 2; }   /* DATV über USB: bis 84 Samples/Symbol (25 kS/s → 2,1 MS/s) */
    if (taps <= 0) taps = 32 * sps;
    const double sample_rate = sr * sps;
    /* Pegel: Symbole mit RMS-Betrag A; nach RRC (sps 2) bzw. AD9361-FIR (sps 1) RMS ≈ A */
    const double rms_target = 32767.0 * pow(10.0, -backoff / 20.0);
    rrc_t rrc; double gain_rrc = 1.0;
    if (sps > 1) {
        if (rrc_init(&rrc, RO[rolloff], sps, taps) < 0) { fprintf(stderr, "taps muss ein Vielfaches von sps sein\n"); return 1; }
        double e = 0; for (int i = 0; i < taps; i++) e += (double)rrc.taps[i] * rrc.taps[i];
        gain_rrc = sqrt(e / sps) / 32768.0;
    }
    int A = (int)lrint(rms_target / gain_rrc);
    dvbs2_t *s2 = dvbs2_create(mod, rate, shortf, pilots, rolloff, gold, 8000);
    if (!s2) { fprintf(stderr, "unzulässige Kombination %s %s%s\n", s2_mod_name[mod], s2_rate_name[rate], shortf ? " short" : ""); return 2; }
    double peak = dvbs2_peak(s2); dvbs2_free(s2);
    if (A * peak > 30000) A = (int)(30000 / peak);   /* Außenring (APSK) darf int16 nicht sprengen */
    s2 = dvbs2_create(mod, rate, shortf, pilots, rolloff, gold, A);
    const double bitrate = dvbs2_ts_bitrate(s2, sr);
    const int nsym = dvbs2_plframe_symbols(s2);

    ts_input_t *ts = ts_input_open(in_spec, loop, udp_buffer, 0);
    if (!ts) { fprintf(stderr, "Eingang %s nicht zu öffnen\n", in_spec); return 1; }
    src_t src = {ts, 0};
    dvbs2_set_input(s2, src_pkt, &src);
    out_sink_t *out = NULL;
    if (out_path) {
        op.path = out_path; op.sample_rate = sample_rate;
        if (op.rf_bw_hz <= 0) op.rf_bw_hz = sr * (1 + RO[rolloff]);
        if (op.buf_samples <= 0) op.buf_samples = 1 << 17;
        out = out_open(&op);
        if (!out) { fprintf(stderr, "Ausgabe %s nicht zu öffnen\n", out_path); return 1; }
        if (realtime) out_set_realtime(out, 1);
    }
    if (!quiet)
        fprintf(stderr, "{\"std\":\"DVB-S2\",\"modcod\":\"%s\",\"modcod_id\":%d,\"symbol_rate\":%.0f,\"rolloff\":%.2f,\"gold\":%d,\"sps\":%d,"
                "\"sample_rate\":%.0f,\"bitrate_bps\":%.3f,\"plframe_symbols\":%d,\"A\":%d,\"backoff_db\":%g}\n",
                dvbs2_name(s2), dvbs2_modcod(s2), sr, RO[rolloff], gold, sps, sample_rate, bitrate, nsym, A, backoff);
    signal(SIGINT, on_sig); signal(SIGTERM, on_sig);
    int16_t *sym = malloc((size_t)nsym * 4);
    int16_t *smp = sps > 1 ? malloc((size_t)nsym * 4 * sps) : NULL;
    long long nfr = 0; double t0 = dvb_now(), last = t0, cpu0 = dvb_cpu_time();
    uint64_t total = 0;
    while (!stop_flag) {
        if (max_frames && nfr >= max_frames) break;
        if (bench && nfr >= bench) break;
        if (max_seconds > 0 && (double)total / sample_rate >= max_seconds) break;
        dvbs2_frame(s2, sym);
        if (src.eof && !bench) break;      /* Datei/stdin zu Ende: letzten (aufgefüllten) Rahmen verwerfen */
        const int16_t *o = sym; size_t n = (size_t)nsym;
        if (sps > 1) { n = rrc_run(&rrc, sym, (size_t)nsym, smp); o = smp; }
        if (out && out_write(out, o, n) < 0) { fprintf(stderr, "Schreibfehler\n"); break; }
        total += n; nfr++;
        if (!quiet) {
            double now = dvb_now();
            if (now - last >= 1.0) {
                ts_stats_t st; ts_input_stats(ts, &st); last = now;
                fprintf(stderr, "{\"t\":%.1f,\"frames\":%lld,\"signal_s\":%.2f,\"rt_factor\":%.2f,\"cpu\":%.2f,\"ts_in\":%llu,\"stuffed\":%llu,\"dropped\":%llu,\"fill\":%d}\n",
                        now - t0, nfr, total / sample_rate, total / sample_rate / (now - t0), (dvb_cpu_time() - cpu0) / (now - t0),
                        (unsigned long long)st.packets_in, (unsigned long long)st.nulls_stuffed, (unsigned long long)st.nulls_dropped, st.fill);
            }
        }
    }
    double el = dvb_now() - t0, cpu = dvb_cpu_time() - cpu0;
    fprintf(stderr, "{\"done\":true,\"frames\":%lld,\"samples\":%llu,\"signal_s\":%.3f,\"wall_s\":%.3f,\"rt_factor\":%.3f,\"cpu_s\":%.3f,\"max_symbol_rate_1core\":%.0f}\n",
            nfr, (unsigned long long)total, total / sample_rate, el, total / sample_rate / (el > 0 ? el : 1), cpu,
            cpu > 0 ? (double)total / sps / cpu : 0);
    if (out) out_close(out);
    ts_input_close(ts); dvbs2_free(s2); free(sym); free(smp);
    if (sps > 1) rrc_free(&rrc);
    return 0;
}
