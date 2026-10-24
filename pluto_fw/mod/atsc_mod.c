/*
 * atsc_mod – ATSC-1.0-Modulator (A/53 Part 2, 8-VSB, 6-MHz-Kanal) für den ADALM-Pluto.
 * TS → Randomizer → RS(207,187) → Faltungs-Byte-Interleaver (52) → 2/3-Trellis (12 Encoder, Precoder)
 * → Segment-/Feldsync → 8 Pegel + Pilot 1,25 → Drehung −fs/4 → RRC-Halbband (α = 11,52 %) → IQ int16.
 * Ausgaberate = Symbolrate = 10,762238 MS/s komplex (1 Sample/Symbol); Pilot bei −2,690559 MHz relativ
 * zum LO (= Kanalmitte), also 309,44 kHz über der Kanalunterkante.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "lib/dvbmod.h"
#include "lib/atsc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <signal.h>
#include <pthread.h>
#include <semaphore.h>

static volatile sig_atomic_t stop_flag;
static void on_sig(int s) { (void)s; stop_flag = 1; }

typedef struct { int16_t *w, *iq; } blk_t;

/* Filter-Worker (zweiter Kern) */
static struct { const atsc_vsb_t *v; sem_t go, done; const blk_t *b; size_t i0, i1; volatile int quit; double last, busy; } W;
static void *worker(void *arg)
{
    (void)arg;
    for (;;) {
        sem_wait(&W.go);
        if (W.quit) break;
        double t = dvb_now();
        atsc_vsb_filter(W.v, W.b->w, W.i0, W.i1, W.b->iq);
        W.last = dvb_now() - t; W.busy += W.last;
        sem_post(&W.done);
    }
    return NULL;
}

/* Erzeuger: TS-Pakete codieren bis eine 12er-Gruppe (ggf. + Feldsync) fertig ist; Grenzen nur an Feldgrenzen */
static struct {
    atsc_enc_t *enc; atsc_vsb_t *vsb; ts_input_t *ts; FILE *fdump;
    long long max_packets, npk, fields; double max_seconds; uint64_t produced;
    uint8_t syms[ATSC_MAX_SEGS_OUT * ATSC_SEG_SYMS];
} P;
static size_t produce(blk_t *b)
{
    uint8_t pkt[TS_PKT];
    for (;;) {
        if (atsc_enc_field_boundary(P.enc)) {
            if (stop_flag) return 0;
            if (P.max_packets && P.npk >= P.max_packets) return 0;
            if (P.max_seconds > 0 && (double)P.produced / ATSC_SYMBOL_RATE >= P.max_seconds) return 0;
        }
        if (!ts_input_packet(P.ts, pkt)) {
            if (atsc_enc_field_boundary(P.enc)) return 0;          /* nur ganze Felder */
            memcpy(pkt, "Gÿ", 4); memset(pkt + 4, 0xff, 184);
        }
        int nseg = atsc_enc_packet(P.enc, pkt, P.syms);
        P.npk++;
        if (!nseg) continue;
        size_t n = (size_t)nseg * ATSC_SEG_SYMS;
        if (P.fdump) fwrite(P.syms, 1, n, P.fdump);
        atsc_vsb_map(P.vsb, P.syms, n, b->w);
        P.produced += n;
        if (atsc_enc_field_boundary(P.enc)) P.fields++;
        return n;
    }
}

static void usage(void)
{
    fprintf(stderr,
"atsc_mod – ATSC-1.0-Modulator (A/53 Part 2, 8-VSB, 19,392658 Mbit/s)\n"
"  Ausgabe: IQ int16 LE interleaved, 10 762 238 S/s komplex (1 Sample/Symbol, Symbolrate 4,5 MHz/286·684);\n"
"           Kanal ±3 MHz um den LO, Pilot bei −2,690559 MHz (309,44 kHz über der Kanalunterkante)\n"
"  -i SPEC        TS-Eingang: Datei | - | udp://[addr]:port | prbs[:N] | null   (Standard prbs)\n"
"  -o PATH        Ausgabe: Datei | - | iio: | iio:URI                              (Standard -)\n"
"  --backoff dB   RMS unter Vollaussteuerung (Standard 10)\n"
"  --taps N       RRC-Länge, ungerade (Standard 81)   --kaiser BETA  Fenster (Standard 4, 0 = keins)\n"
"  --packets N | --seconds S   Dauer (ganze Felder à 312 Pakete)   --loop   Datei wiederholen\n"
"  --realtime     Datei/stdout auf Nominalrate bremsen   --udp-buffer N  UDP-Ring in Paketen   --quiet\n"
"  --print-rates  eine JSON-Zeile (ts_bps, sample_rate, symbol_rate) auf stdout und Ende\n"
"  --dump-sym DATEI  Symbolindizes 0..7 (uint8, 832 je Segment) zusätzlich schreiben (Test)\n"
"  IIO: --lo HZ (= Kanalmitte) --atten DB --rfbw HZ (Standard 6e6) --iio-buf SAMPLES --iio-kbufs N --enable\n");
}

int main(int argc, char **argv)
{
    int threads = 2, print_rates = 0, loop = 0, realtime = 0, quiet = 0, udp_buffer = 2000, taps = 81;
    double backoff = 10, max_seconds = 0, beta = 4; long long max_packets = 0;
    const char *in_spec = "prbs", *out_path = "-", *dump_sym = NULL;
    out_params_t op; memset(&op, 0, sizeof op); op.atten_db = 89.75; op.kernel_buffers = 4;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
#define ARG(name) (!strcmp(a, name) && i + 1 < argc)
        if (ARG("-i")) in_spec = argv[++i];
        else if (ARG("-o")) out_path = argv[++i];
        else if (ARG("--backoff")) backoff = atof(argv[++i]);
        else if (ARG("--taps")) taps = atoi(argv[++i]);
        else if (ARG("--kaiser")) beta = atof(argv[++i]);
        else if (ARG("--threads")) threads = atoi(argv[++i]);
        else if (ARG("--packets")) max_packets = atoll(argv[++i]);
        else if (ARG("--seconds")) max_seconds = atof(argv[++i]);
        else if (ARG("--dump-sym")) dump_sym = argv[++i];
        else if (!strcmp(a, "--loop")) loop = 1;
        else if (!strcmp(a, "--realtime")) realtime = 1;
        else if (!strcmp(a, "--quiet")) quiet = 1;
        else if (!strcmp(a, "--print-rates")) print_rates = 1;
        else if (ARG("--udp-buffer")) udp_buffer = atoi(argv[++i]);
        else if (ARG("--lo")) op.lo_hz = atof(argv[++i]);
        else if (ARG("--atten")) op.atten_db = atof(argv[++i]);
        else if (ARG("--rfbw")) op.rf_bw_hz = atof(argv[++i]);
        else if (ARG("--iio-buf")) op.buf_samples = atoi(argv[++i]);
        else if (ARG("--iio-kbufs")) op.kernel_buffers = atoi(argv[++i]);
        else if (!strcmp(a, "--enable")) op.enable = 1;
        else { usage(); return 2; }
    }
    if (threads != 1 && threads != 2) { usage(); return 2; }
    if (taps < 9 || !(taps & 1)) { fprintf(stderr, "--taps muss ungerade und >= 9 sein\n"); return 2; }
    const double sr = ATSC_SYMBOL_RATE, sample_rate = sr;
    const double bitrate = sr * 312.0 * 188.0 * 8.0 / (313.0 * 832.0);   /* 19 392 658,46 b/s */
    if (print_rates) {
        printf("{\"ts_bps\":%.3f,\"sample_rate\":%.3f,\"symbol_rate\":%.3f,\"sps\":1,\"bandwidth_hz\":6000000,\"pilot_offset_hz\":%.3f}\n",
               bitrate, sample_rate, sr, -sr / 4.0);
        return 0;
    }

    static atsc_enc_t enc;
    atsc_enc_init(&enc);
    atsc_vsb_t vsb;
    if (atsc_vsb_init(&vsb, taps, beta, 32767.0 * pow(10.0, -backoff / 20.0), ATSC_MAX_SEGS_OUT * ATSC_SEG_SYMS) < 0) {
        fprintf(stderr, "VSB-Filter: Initialisierung fehlgeschlagen\n"); return 1;
    }
    ts_input_t *ts = ts_input_open(in_spec, loop, udp_buffer, 0);
    if (!ts) { fprintf(stderr, "Eingang %s nicht zu öffnen\n", in_spec); return 1; }
    FILE *fdump = NULL;
    if (dump_sym && !(fdump = fopen(dump_sym, "wb"))) { fprintf(stderr, "%s nicht zu öffnen\n", dump_sym); return 1; }
    op.path = out_path; op.sample_rate = sample_rate;
    if (op.rf_bw_hz <= 0) op.rf_bw_hz = 6e6;
    if (op.buf_samples <= 0) op.buf_samples = ATSC_SEG_SYMS * 104;   /* 86 528 Samples ≈ 8 ms */
    out_sink_t *out = out_open(&op);
    if (!out) { fprintf(stderr, "Ausgabe %s nicht zu öffnen\n", out_path); return 1; }
    if (realtime) out_set_realtime(out, 1);
    if (!quiet)
        fprintf(stderr, "{\"standard\":\"ATSC A/53 8-VSB\",\"symbol_rate\":%.3f,\"sps\":1,\"sample_rate\":%.3f,\"bitrate_bps\":%.3f,"
                "\"pilot_offset_hz\":%.1f,\"taps\":%d,\"kaiser\":%g,\"A\":%.1f,\"rms\":%.0f,\"peak_w\":%.0f,\"backoff_db\":%g,\"filter\":\"%s\"}\n",
                sr, sample_rate, bitrate, -sr / 4.0, taps, beta, vsb.A, vsb.rms, vsb.peak_w, backoff, atsc_vsb_backend());
    signal(SIGINT, on_sig); signal(SIGTERM, on_sig);

    /* Pipeline: Hauptthread codiert Block k+1 (TS → Symbole → w) und filtert den hinteren Teil von Block k,
       der Worker gleichzeitig den vorderen Teil [0, s). s wird laufend so nachgeführt, dass beide gleich lang rechnen. */
    P.enc = &enc; P.vsb = &vsb; P.ts = ts; P.fdump = fdump; P.max_packets = max_packets; P.max_seconds = max_seconds;
    size_t wlen = atsc_vsb_wbuf_len(&vsb, ATSC_MAX_SEGS_OUT * ATSC_SEG_SYMS);
    blk_t B[2];
    for (int k = 0; k < 2; k++) {
        B[k].w = (int16_t *)calloc(wlen, sizeof(int16_t));
        B[k].iq = (int16_t *)malloc(2 * sizeof(int16_t) * ATSC_MAX_SEGS_OUT * ATSC_SEG_SYMS);
    }
    pthread_t wt;
    if (threads == 2) {
        sem_init(&W.go, 0, 0); sem_init(&W.done, 0, 0); W.v = &vsb;
        if (pthread_create(&wt, NULL, worker, NULL)) { fprintf(stderr, "pthread_create fehlgeschlagen\n"); threads = 1; }
    }
    double t0 = dvb_now(), last = t0, cpu0 = dvb_cpu_time(), r = 0.5, wait_s = 0;
    uint64_t total_samples = 0;
    int cur = 0;
    size_t n = produce(&B[0]);
    while (n) {
        blk_t *b = &B[cur];
        size_t s = (threads == 2) ? ((size_t)(r * n) & ~(size_t)7) : 0;
        if (s) { W.b = b; W.i0 = 0; W.i1 = s; sem_post(&W.go); }
        double tm0 = dvb_now();
        size_t nn = produce(&B[cur ^ 1]);
        atsc_vsb_filter(&vsb, b->w, s, n, b->iq);
        double tm = dvb_now() - tm0;
        if (s) {
            double tw = dvb_now(); sem_wait(&W.done); wait_s += dvb_now() - tw;
            /* Nachführung: f = Filterzeit/Sample (Worker), C = Codierzeit des Hauptthreads → r = (C + n·f)/(2·n·f) */
            double f = W.last / (double)s, C = tm - (double)(n - s) * f;
            if (f > 0) {
                double ro = (C + n * f) / (2.0 * n * f);
                ro = ro < 0.05 ? 0.05 : ro > 0.95 ? 0.95 : ro;
                r = 0.8 * r + 0.2 * ro;
            }
        }
        if (out_write(out, b->iq, n) < 0) { fprintf(stderr, "Schreibfehler\n"); break; }
        total_samples += n;
        if (!quiet) {
            double now = dvb_now();
            if (now - last >= 1.0) {
                ts_stats_t st; ts_input_stats(ts, &st); last = now;
                fprintf(stderr, "{\"t\":%.1f,\"packets\":%lld,\"fields\":%lld,\"signal_s\":%.2f,\"rt_factor\":%.2f,\"cpu\":%.2f,\"split\":%.2f,\"ts_in\":%llu,\"stuffed\":%llu,\"dropped\":%llu,\"fill\":%d}\n",
                        now - t0, P.npk, P.fields, total_samples / sample_rate, total_samples / sample_rate / (now - t0), (dvb_cpu_time() - cpu0) / (now - t0), r,
                        (unsigned long long)st.packets_in, (unsigned long long)st.nulls_stuffed, (unsigned long long)st.nulls_dropped, st.fill);
            }
        }
        cur ^= 1; n = nn;
    }
    if (threads == 2) { W.quit = 1; sem_post(&W.go); pthread_join(wt, NULL); }
    long long npk = P.npk, fields = P.fields;
    double el = dvb_now() - t0, cpu = dvb_cpu_time() - cpu0;
    fprintf(stderr, "{\"done\":true,\"packets\":%lld,\"fields\":%lld,\"samples\":%llu,\"signal_s\":%.3f,\"wall_s\":%.3f,\"rt_factor\":%.3f,\"cpu_s\":%.3f,"
            "\"msym_per_s\":%.3f,\"msym_per_cpu_s\":%.3f,\"threads\":%d,\"split\":%.3f,\"worker_s\":%.3f,\"main_wait_s\":%.3f}\n",
            npk, fields, (unsigned long long)total_samples, total_samples / sample_rate, el, total_samples / sample_rate / (el > 0 ? el : 1), cpu,
            total_samples / 1e6 / (el > 0 ? el : 1), total_samples / 1e6 / (cpu > 0 ? cpu : 1), threads, r, W.busy, wait_s);
    if (fdump) fclose(fdump);
    out_close(out); ts_input_close(ts); atsc_vsb_free(&vsb);
    for (int k = 0; k < 2; k++) { free(B[k].w); free(B[k].iq); }
    return 0;
}
