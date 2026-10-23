/*
 * dvbt_mod – DVB-T-Modulator (ETSI EN 300 744 V1.6.1, nicht-hierarchisch) für den ADALM-Pluto.
 *
 * Pipeline (2 Threads):
 *   Kern 0: TS → Energieverwürfelung → RS(204,188) → Faltungsinterleaver → Faltungscoder/Punktierung
 *           → Demux/Bit-Interleaver → Symbol-Interleaver → Mapper → Piloten/TPS → Frequenzbereichsvektor (int16)
 *   Kern 1: IFFT (int16, NEON) → Guard → Ausgabe (Datei/stdout/IIO-DMA)
 * Ringpuffer zwischen den Kernen, keine Kopien im Signalpfad.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "lib/dvbmod.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <unistd.h>
#include <time.h>

#define RING 8

typedef struct {
    int16_t *iq;      /* nfft Bins, interleaved I/Q */
} slot_t;

static struct {
    /* Parameter */
    int mode, constellation, coderate, guard, cell_id, tps_lp_same;
    double bw_mhz, backoff_db;
    const char *in_spec, *out_path;
    int loop, threads, profile, realtime, affinity, rt_prio, udp_buffer, quiet, bench_symbols;
    const char *dump; FILE *f_scr, *f_rs, *f_ci, *f_punct, *f_yp, *f_y;
    int window; ofdm_window_t win;
    int slow_us;      /* Test: künstliche Verzögerung je Symbol im IFFT-Thread (Negativprobe für Unterlauf) */
    long long max_frames; double max_seconds;
    out_params_t op;
    /* Zustand */
    dvb_scrambler_t scr; dvb_convint_t ci; dvb_convcode_t cc;
    dvbt_inner_t inner; dvbt_frame_t frame; fft16_t *fft;
    ts_input_t *ts; out_sink_t *out;
    int nfft, guard_len, ncells, v, bits_per_symbol_bytes;
    double sample_rate, bitrate;
    /* Ring */
    slot_t slots[RING]; int head, tail; pthread_mutex_t mx; pthread_cond_t cv_full, cv_empty; int done, err;
    /* Statistik */
    uint64_t symbols_coded, symbols_out, clipped;
    double t_fec, t_bitil, t_symil, t_frame, t_fft, t_out;  /* Sekunden je Stufe (Profil) */
    double cpu_a, cpu_b, wall_start;
    volatile sig_atomic_t stop;
} G;

static void on_sigint(int s) { (void)s; G.stop = 1; }

static void usage(void)
{
    fprintf(stderr,
"dvbt_mod – DVB-T-Modulator (EN 300 744)\n"
"  -i SPEC        TS-Eingang: Datei | - (stdin) | udp://[addr]:port | prbs[:N] | null   (Standard: prbs)\n"
"  -o PATH        Ausgabe: Datei | - (stdout) | iio: | iio:URI | /dev/null              (Standard: -)\n"
"  --mode 2k|8k   --const qpsk|16qam|64qam   --rate 1/2|2/3|3/4|5/6|7/8   --guard 1/32|1/16|1/8|1/4\n"
"  --bw 5|6|7|8   Kanalbandbreite MHz (Abtastrate = BW·8/7 MS/s)\n"
"  --backoff dB   RMS-Abstand zur Vollaussteuerung (Standard 12)\n"
"  --window W     RC-Symbolfensterung über W Samples im Guard (Standard: N/64, max. Guard/2; 0 = aus)\n"
"  --cell-id N    TPS-Zellkennung (0 = nicht gesendet, Längenkennung 010111)\n"
"  --tps-lp-same  LP-Coderate in TPS = HP (wie gr-dtv) statt 000 (Norm, nicht-hierarchisch)\n"
"  --frames N     nach N OFDM-Rahmen (68 Symbole) beenden   --seconds S   nach S Sekunden Signal\n"
"  --loop         Eingabedatei wiederholen      --udp-buffer N   UDP-Ring in Paketen (2000)\n"
"  --threads 1|2  Pipeline-Threads (Standard 2)  --affinity   Kern 0/1 fest zuweisen  --rt   SCHED_FIFO\n"
"  --realtime     Datei/stdout mit nominaler Rate bremsen (für PC-Streaming)\n"
"  --slow US      Test: US Mikrosekunden Pause je Symbol (Negativprobe Unterlauf)\n"
"  --profile      Stufenzeiten messen           --bench N    nur Signalkette, N Symbole, ohne Ausgabe\n"
"  IIO: --lo HZ --atten DB --rfbw HZ --iio-buf SAMPLES --iio-kbufs N --cyclic --enable --fir DATEI\n"
"  --quiet        keine Statuszeilen\n");
}

static int parse_frac(const char *s, const char *const *names, int n) { for (int i = 0; i < n; i++) if (!strcmp(s, names[i])) return i; return -1; }

static void status_line(void)
{
    ts_stats_t st; ts_input_stats(G.ts, &st);
    double now = dvb_now(), el = now - G.wall_start;
    double gen_s = (double)G.symbols_out * (G.nfft + G.guard_len) / G.sample_rate;
    fprintf(stderr, "{\"t\":%.1f,\"symbols\":%llu,\"signal_s\":%.2f,\"rt_factor\":%.2f,\"cpu_coder\":%.2f,\"cpu_fft\":%.2f,"
            "\"ts_in\":%llu,\"ts_out\":%llu,\"stuffed\":%llu,\"dropped\":%llu,\"fill\":%d,\"cap\":%d,\"clipped\":%llu}\n",
            el, (unsigned long long)G.symbols_out, gen_s, el > 0 ? gen_s / el : 0, G.cpu_a / (el > 0 ? el : 1), G.cpu_b / (el > 0 ? el : 1),
            (unsigned long long)st.packets_in, (unsigned long long)st.packets_out, (unsigned long long)st.nulls_stuffed,
            (unsigned long long)st.nulls_dropped, st.fill, st.capacity, (unsigned long long)G.clipped);
}

static void pin_cpu(int cpu)
{
#ifdef __linux__
    cpu_set_t set; CPU_ZERO(&set); CPU_SET(cpu, &set);
    pthread_setaffinity_np(pthread_self(), sizeof set, &set);
#endif
}
static void set_rt(int prio)
{
    struct sched_param sp; sp.sched_priority = prio;
    if (pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp)) perror("SCHED_FIFO");
}

/* ------------------------------------------------------------------ Coder-Thread (Kern 0) */
static int next_symbol(uint8_t *punct, int *pn, uint8_t *yp, uint8_t *y, slot_t *s)
{
    const int need = G.bits_per_symbol_bytes;
    uint8_t pkt[RS_PKT], il[RS_PKT];
    double t0 = G.profile ? dvb_now() : 0;
    while (*pn < need) {
        if (!ts_input_packet(G.ts, pkt)) return 0;
        dvb_scramble_packet(&G.scr, pkt);
        if (G.dump) fwrite(pkt, 1, TS_PKT, G.f_scr);
        dvb_rs_encode(pkt, pkt);
        if (G.dump) fwrite(pkt, 1, RS_PKT, G.f_rs);
        dvb_convint_packet(&G.ci, pkt, il);
        if (G.dump) fwrite(il, 1, RS_PKT, G.f_ci);
        int nb = (int)dvb_convcode_bytes(&G.cc, il, RS_PKT, punct + *pn);
        if (G.dump) fwrite(punct + *pn, 1, (size_t)nb, G.f_punct);
        *pn += nb;
    }
    double t1 = G.profile ? dvb_now() : 0;
    dvbt_bit_interleave(&G.inner, punct, yp);
    if (G.dump) fwrite(yp, 1, (size_t)G.ncells, G.f_yp);
    double t2 = G.profile ? dvb_now() : 0;
    dvbt_symbol_interleave(&G.inner, yp, y, G.frame.symbol & 1);
    if (G.dump) fwrite(y, 1, (size_t)G.ncells, G.f_y);
    double t3 = G.profile ? dvb_now() : 0;
    dvbt_frame_build(&G.frame, &G.inner, y, s->iq);
    double t4 = G.profile ? dvb_now() : 0;
    *pn -= need; memmove(punct, punct + need, (size_t)*pn);
    if (G.profile) { G.t_fec += t1 - t0; G.t_bitil += t2 - t1; G.t_symil += t3 - t2; G.t_frame += t4 - t3; }
    return 1;
}

static void *coder_thread(void *arg)
{
    (void)arg;
    if (G.affinity) pin_cpu(0);
    if (G.rt_prio) set_rt(G.rt_prio);
    uint8_t *punct = (uint8_t *)malloc(G.bits_per_symbol_bytes + 2 * RS_PKT + 64);
    uint8_t *yp = (uint8_t *)malloc(G.ncells), *y = (uint8_t *)malloc(G.ncells);
    int pn = 0, eof = 0;
    long long frames_done = 0;
    for (;;) {
        if (G.stop) break;
        if (G.max_frames > 0 && frames_done >= G.max_frames) break;
        if (G.max_seconds > 0 && (double)G.symbols_coded * (G.nfft + G.guard_len) / G.sample_rate >= G.max_seconds
            && G.frame.symbol == 0 && (G.frame.frame & 3) == 0) break;
        if (G.bench_symbols > 0 && (long long)G.symbols_coded >= G.bench_symbols) break;
        pthread_mutex_lock(&G.mx);
        while ((G.head + 1) % RING == G.tail && !G.stop) pthread_cond_wait(&G.cv_empty, &G.mx);
        pthread_mutex_unlock(&G.mx);
        if (G.stop) break;
        slot_t *s = &G.slots[G.head];
        if (!next_symbol(punct, &pn, yp, y, s)) {
            /* Eingabe zu Ende: Superrahmen mit Nullpaketen auffüllen */
            if (!eof) { eof = 1; }
            if (G.frame.symbol == 0 && (G.frame.frame & 3) == 0) break;
            uint8_t pkt[RS_PKT], il[RS_PKT];
            while (pn < G.bits_per_symbol_bytes) {
                memcpy(pkt, "\x47\x1f\xff\x10", 4); memset(pkt + 4, 0xff, 184);
                dvb_scramble_packet(&G.scr, pkt); dvb_rs_encode(pkt, pkt); dvb_convint_packet(&G.ci, pkt, il);
                pn += (int)dvb_convcode_bytes(&G.cc, il, RS_PKT, punct + pn);
            }
            dvbt_bit_interleave(&G.inner, punct, yp);
            dvbt_symbol_interleave(&G.inner, yp, y, G.frame.symbol & 1);
            dvbt_frame_build(&G.frame, &G.inner, y, s->iq);
            pn -= G.bits_per_symbol_bytes; memmove(punct, punct + G.bits_per_symbol_bytes, (size_t)pn);
        }
        G.symbols_coded++;
        if (G.frame.symbol == 0) frames_done++;
        pthread_mutex_lock(&G.mx);
        G.head = (G.head + 1) % RING;
        pthread_cond_signal(&G.cv_full);
        pthread_mutex_unlock(&G.mx);
        if (G.threads == 1) { /* inline IFFT */ }
    }
    pthread_mutex_lock(&G.mx); G.done = 1; pthread_cond_signal(&G.cv_full); pthread_mutex_unlock(&G.mx);
    G.cpu_a = dvb_cpu_time();
    free(punct); free(yp); free(y);
    return NULL;
}

/* ------------------------------------------------------------------ IFFT/Ausgabe-Thread (Kern 1) */
static void *fft_thread(void *arg)
{
    (void)arg;
    if (G.affinity) pin_cpu(1);
    if (G.rt_prio) set_rt(G.rt_prio);
    const int nsym = G.nfft + G.guard_len;
    int16_t *out = (int16_t *)malloc((size_t)nsym * 4 * 4);
    int nbatch = 0; const int batch = (G.out && G.bench_symbols == 0) ? 4 : 1;
    double last_status = dvb_now();
    for (;;) {
        pthread_mutex_lock(&G.mx);
        while (G.head == G.tail && !G.done) pthread_cond_wait(&G.cv_full, &G.mx);
        if (G.head == G.tail && G.done) { pthread_mutex_unlock(&G.mx); break; }
        pthread_mutex_unlock(&G.mx);
        slot_t *s = &G.slots[G.tail];
        double t0 = G.profile ? dvb_now() : 0;
        fft16_ifft_ofdm(G.fft, s->iq, out + (size_t)nbatch * nsym * 2, G.guard_len);
        ofdm_window_apply(&G.win, out + (size_t)nbatch * nsym * 2, G.guard_len);
        if (G.slow_us > 0) { struct timespec ts = {0, G.slow_us * 1000L}; nanosleep(&ts, NULL); }
        double t1 = G.profile ? dvb_now() : 0;
        pthread_mutex_lock(&G.mx);
        G.tail = (G.tail + 1) % RING;
        pthread_cond_signal(&G.cv_empty);
        pthread_mutex_unlock(&G.mx);
        if (++nbatch == batch) {
            if (G.out && out_write(G.out, out, (size_t)nsym * batch) < 0) { G.err = 1; G.stop = 1; }
            nbatch = 0;
        }
        G.symbols_out++;
        if (G.profile) { G.t_fft += t1 - t0; G.t_out += dvb_now() - t1; }
        if (!G.quiet && dvb_now() - last_status >= 1.0) { G.cpu_b = dvb_cpu_time(); last_status = dvb_now(); status_line(); }
    }
    if (nbatch && G.out) out_write(G.out, out, (size_t)nsym * nbatch);
    G.cpu_b = dvb_cpu_time();
    free(out);
    return NULL;
}

int main(int argc, char **argv)
{
    memset(&G, 0, sizeof G);
    G.mode = DVBT_8K; G.constellation = DVBT_64QAM; G.coderate = CR_2_3; G.guard = GI_1_32; G.bw_mhz = 8; G.backoff_db = 12;
    G.in_spec = "prbs"; G.out_path = "-"; G.threads = 2; G.udp_buffer = 2000;
    G.op.atten_db = 89.75; G.op.buf_samples = 0; G.op.kernel_buffers = 4; G.window = -1;
    static const char *const cnames[] = {"qpsk", "16qam", "64qam"}, *const gnames[] = {"1/32", "1/16", "1/8", "1/4"};
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
#define ARG(name) (!strcmp(a, name) && i + 1 < argc)
        if (ARG("-i")) G.in_spec = argv[++i];
        else if (ARG("-o")) G.out_path = argv[++i];
        else if (ARG("--mode")) G.mode = !strcmp(argv[++i], "8k") ? DVBT_8K : DVBT_2K;
        else if (ARG("--const")) { G.constellation = parse_frac(argv[++i], cnames, 3); if (G.constellation < 0) { usage(); return 2; } }
        else if (ARG("--rate")) { G.coderate = parse_frac(argv[++i], dvb_coderate_name, 5); if (G.coderate < 0) { usage(); return 2; } }
        else if (ARG("--guard")) { G.guard = parse_frac(argv[++i], gnames, 4); if (G.guard < 0) { usage(); return 2; } }
        else if (ARG("--bw")) G.bw_mhz = atof(argv[++i]);
        else if (ARG("--backoff")) G.backoff_db = atof(argv[++i]);
        else if (ARG("--cell-id")) G.cell_id = atoi(argv[++i]);
        else if (!strcmp(a, "--tps-lp-same")) G.tps_lp_same = 1;
        else if (ARG("--frames")) G.max_frames = atoll(argv[++i]);
        else if (ARG("--seconds")) G.max_seconds = atof(argv[++i]);
        else if (!strcmp(a, "--loop")) G.loop = 1;
        else if (ARG("--udp-buffer")) G.udp_buffer = atoi(argv[++i]);
        else if (ARG("--threads")) G.threads = atoi(argv[++i]);
        else if (!strcmp(a, "--affinity")) G.affinity = 1;
        else if (!strcmp(a, "--rt")) G.rt_prio = 40;
        else if (!strcmp(a, "--realtime")) G.realtime = 1;
        else if (!strcmp(a, "--profile")) G.profile = 1;
        else if (ARG("--bench")) { G.bench_symbols = atoi(argv[++i]); G.out_path = NULL; }
        else if (!strcmp(a, "--quiet")) G.quiet = 1;
        else if (ARG("--dump")) G.dump = argv[++i];
        else if (ARG("--window")) G.window = atoi(argv[++i]);
        else if (ARG("--slow")) G.slow_us = atoi(argv[++i]);
        else if (ARG("--lo")) G.op.lo_hz = atof(argv[++i]);
        else if (ARG("--atten")) G.op.atten_db = atof(argv[++i]);
        else if (ARG("--rfbw")) G.op.rf_bw_hz = atof(argv[++i]);
        else if (ARG("--iio-buf")) G.op.buf_samples = atoi(argv[++i]);
        else if (ARG("--iio-kbufs")) G.op.kernel_buffers = atoi(argv[++i]);
        else if (!strcmp(a, "--cyclic")) G.op.cyclic = 1;
        else if (!strcmp(a, "--enable")) G.op.enable = 1;
        else if (ARG("--fir")) G.op.fir_file = argv[++i];
        else { usage(); return 2; }
    }
    G.nfft = (G.mode == DVBT_8K) ? 8192 : 2048;
    G.guard_len = G.nfft >> (5 - G.guard);
    /* Standardfenster: 8k 32 Samples (Schulter −64 dBc, mit gr-dtv in allen 8k-Fällen bitexakt; W=64 nicht in allen),
       2k 8 Samples (gr-dtv rastet in 2k ab W=16 nicht mehr ein); höchstens Guard/2. Siehe README „Spektrum“. */
    if (G.window < 0) { G.window = (G.mode == DVBT_8K) ? 32 : 8; if (G.window > G.guard_len / 2) G.window = G.guard_len / 2; }
    if (G.window > G.guard_len) G.window = G.guard_len;
    G.window &= ~7;
    G.ncells = (G.mode == DVBT_8K) ? 6048 : 1512;
    G.v = (G.constellation == DVBT_QPSK) ? 2 : (G.constellation == DVBT_16QAM) ? 4 : 6;
    G.bits_per_symbol_bytes = G.ncells * G.v / 8;
    G.sample_rate = G.bw_mhz * 8e6 / 7.0;
    /* Nutzbitrate: Pakete je Superrahmen = 1008·v·CR (8k) bzw. 252·v·CR (2k), Superrahmen = 272 Symbole */
    double pk_sf = ((G.mode == DVBT_8K) ? 1008.0 : 252.0) * G.v * dvb_coderate_num[G.coderate] / dvb_coderate_den[G.coderate];
    G.bitrate = pk_sf * 1504.0 * G.sample_rate / (272.0 * (G.nfft + G.guard_len));

    dvb_rs_init(); dvb_scrambler_init(&G.scr); dvb_convint_init(&G.ci); dvb_convcode_init(&G.cc, G.coderate);
    {   /* Interleaver-Historie mit verwürfelten, RS-codierten Nullpaketen vorbelegen (vermeidet die
           Nullfolgen → Spitzenwert-Übersteuerung der ersten 11 Pakete; Receiver-neutral) */
        dvb_scrambler_t s2; dvb_scrambler_init(&s2); uint8_t p[RS_PKT], o[RS_PKT];
        for (int i = 0; i < 11; i++) {
            memcpy(p, "Gÿ", 4); memset(p + 4, 0xff, 184);
            dvb_scramble_packet(&s2, p); dvb_rs_encode(p, p); dvb_convint_packet(&G.ci, p, o);
        }
    }
    G.fft = fft16_create(G.nfft);
    if (!G.fft) { fprintf(stderr, "FFT %d nicht unterstützt\n", G.nfft); return 1; }
    /* Zellverstärkung A: RMS_out = A·sqrt(mean_cell_power)/2^S  → A = RMS_ziel·2^S/sqrt(P) */
    dvbt_frame_t tmp; if (dvbt_frame_init(&tmp, G.mode, G.guard, G.constellation, G.coderate, G.cell_id, 1000) < 0) { fprintf(stderr, "Pilottabellen inkonsistent\n"); return 1; }
    double P = tmp.mean_cell_power; dvbt_frame_free(&tmp);
    double rms = 32767.0 * pow(10.0, -G.backoff_db / 20.0);
    int A = (int)lrint(rms * (double)(1 << fft16_shift_bits(G.fft)) / sqrt(P));
    if (A * 4 / 3 > 32000) A = 24000;
    if (dvbt_inner_init(&G.inner, G.mode, G.constellation, A) < 0) return 1;
    if (dvbt_frame_init(&G.frame, G.mode, G.guard, G.constellation, G.coderate, G.cell_id, A) < 0) return 1;
    if (G.tps_lp_same) G.frame.coderate_lp = G.coderate;
    if (ofdm_window_init(&G.win, G.window) < 0) return 1;
    for (int i = 0; i < RING; i++) {
        G.slots[i].iq = (int16_t *)calloc(G.nfft, 4);
    }
    pthread_mutex_init(&G.mx, NULL); pthread_cond_init(&G.cv_full, NULL); pthread_cond_init(&G.cv_empty, NULL);

    G.ts = ts_input_open(G.in_spec, G.loop, G.udp_buffer, 0);
    if (!G.ts) { fprintf(stderr, "Eingang %s nicht zu öffnen\n", G.in_spec); return 1; }
    if (G.out_path) {
        G.op.path = G.out_path; G.op.sample_rate = G.sample_rate;
        if (G.op.rf_bw_hz <= 0) G.op.rf_bw_hz = G.bw_mhz * 1e6;
        if (G.op.buf_samples <= 0) G.op.buf_samples = 8 * (G.nfft + G.guard_len);
        G.out = out_open(&G.op);
        if (!G.out) { fprintf(stderr, "Ausgabe %s nicht zu öffnen\n", G.out_path); return 1; }
        if (G.realtime) out_set_realtime(G.out, 1);
    }
    if (!G.quiet)
        fprintf(stderr, "{\"mode\":\"%s\",\"const\":\"%s\",\"rate\":\"%s\",\"guard\":\"%s\",\"bw_mhz\":%g,\"sample_rate\":%.6f,"
                "\"bitrate_bps\":%.3f,\"packets_per_superframe\":%.0f,\"samples_per_superframe\":%d,\"A\":%d,\"backoff_db\":%g,\"fft\":\"%s\",\"threads\":%d,\"window\":%d}\n",
                G.mode == DVBT_8K ? "8k" : "2k", cnames[G.constellation], dvb_coderate_name[G.coderate], gnames[G.guard], G.bw_mhz,
                G.sample_rate, G.bitrate, pk_sf, 272 * (G.nfft + G.guard_len), A, G.backoff_db, fft16_backend(), G.threads, G.window);
    if (G.dump) {
        char fn[512];
#define OPEN_DUMP(fld, ext) snprintf(fn, sizeof fn, "%s.%s", G.dump, ext); G.fld = fopen(fn, "wb");
        OPEN_DUMP(f_scr, "scr") OPEN_DUMP(f_rs, "rs") OPEN_DUMP(f_ci, "ci") OPEN_DUMP(f_punct, "punct") OPEN_DUMP(f_yp, "yp") OPEN_DUMP(f_y, "y")
    }
    signal(SIGINT, on_sigint); signal(SIGTERM, on_sigint);
    G.wall_start = dvb_now();
    pthread_t ta, tb;
    if (G.threads >= 2) {
        pthread_create(&ta, NULL, coder_thread, NULL);
        pthread_create(&tb, NULL, fft_thread, NULL);
        pthread_join(ta, NULL); pthread_join(tb, NULL);
    } else {
        /* ein Thread: Coder und IFFT abwechselnd (Ring tief 1) */
        pthread_create(&ta, NULL, coder_thread, NULL);
        fft_thread(NULL);
        pthread_join(ta, NULL);
    }
    double el = dvb_now() - G.wall_start;
    double sig = (double)G.symbols_out * (G.nfft + G.guard_len) / G.sample_rate;
    fprintf(stderr, "{\"done\":true,\"symbols\":%llu,\"signal_s\":%.3f,\"wall_s\":%.3f,\"rt_factor\":%.3f,\"cpu_coder_s\":%.3f,\"cpu_fft_s\":%.3f,"
            "\"us_per_symbol_coder\":%.1f,\"us_per_symbol_fft\":%.1f,\"symbol_us\":%.1f",
            (unsigned long long)G.symbols_out, sig, el, el > 0 ? sig / el : 0, G.cpu_a, G.cpu_b,
            G.symbols_out ? 1e6 * G.cpu_a / G.symbols_out : 0, G.symbols_out ? 1e6 * G.cpu_b / G.symbols_out : 0,
            1e6 * (G.nfft + G.guard_len) / G.sample_rate);
    if (G.profile && G.symbols_out)
        fprintf(stderr, ",\"profile_us\":{\"fec\":%.1f,\"bit_il\":%.1f,\"sym_il\":%.1f,\"frame\":%.1f,\"fft\":%.1f,\"out\":%.1f}",
                1e6 * G.t_fec / G.symbols_out, 1e6 * G.t_bitil / G.symbols_out, 1e6 * G.t_symil / G.symbols_out,
                1e6 * G.t_frame / G.symbols_out, 1e6 * G.t_fft / G.symbols_out, 1e6 * G.t_out / G.symbols_out);
    fprintf(stderr, "}\n");
    if (G.out) out_close(G.out);
    if (G.dump) { fclose(G.f_scr); fclose(G.f_rs); fclose(G.f_ci); fclose(G.f_punct); fclose(G.f_yp); fclose(G.f_y); }
    ts_input_close(G.ts);
    return G.err;
}
