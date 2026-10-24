/*
 * isdbt_mod – ISDB-Tb-Modulator (ARIB STD-B31 / ABNT NBR 15601-1, 6-MHz-Kanal, 512/63 MS/s) für den ADALM-Pluto.
 *
 * Pipeline (2 Threads, wie dvbt_mod):
 *   Kern 0: TS → PID-Schichtzuordnung (FIFOs, Nullpaket-Stopfen) → je Schicht RS/Verwürfelung/Byte-Interleaver/
 *           Faltungscode/Bit- und Zeitverschachtelung → Rahmen (Frequenzverschachtelung, SP/CP/TMCC/AC1) → int16-Bins
 *   Kern 1: IFFT (int16, NEON; Modus 2: float) → Guard → Ausgabe (Datei/stdout/IIO-DMA)
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "lib/dvbmod.h"
#include "lib/isdbt.h"
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
#define MAXPIDS 64

typedef struct { uint8_t *buf; int cap, head, n; } pktq_t;

static struct {
    isdbt_cfg_t cfg;
    double backoff_db;
    const char *in_spec, *out_path;
    int loop, threads, profile, realtime, affinity, rt_prio, udp_buffer, quiet, bench_symbols, window, print_rates;
    long long max_packets; double max_seconds;
    out_params_t op;
    int a_pids[MAXPIDS], n_a_pids, have_a_pids;
    /* Zustand */
    isdbt_layer_t lay[2]; isdbt_frame_t frame; fft16_t *fft; ofdm_window_t win;
    ts_input_t *ts, *ts_l[2];       /* ts_l: getrennte PRBS-Quellen je Schicht (Eingang prbs) */
    pktq_t q[2];
    out_sink_t *out;
    int nfft, guard_len, eof, flush_frames;
    long long in_packets;
    uint64_t stuffed[2], dropped[2], nulls_in, dup_psi;
    /* Ring */
    int16_t *slots[RING]; int head, tail; pthread_mutex_t mx; pthread_cond_t cv_full, cv_empty; int done, err;
    uint64_t symbols_coded, symbols_out;
    double t_frame, t_fft;
    double cpu_a, cpu_b, wall_start;
    /* float-IFFT (Modus 2) */
    float *fre, *fim, *twr, *twi; int *bitrev;
    volatile sig_atomic_t stop;
} G;

static void on_sigint(int s) { (void)s; G.stop = 1; }

static void usage(void)
{
    fprintf(stderr,
"isdbt_mod – ISDB-Tb-Modulator (ARIB STD-B31 / ABNT NBR 15601), 6 MHz, 512/63 MS/s, IQ int16 LE\n"
"  -i SPEC        TS-Eingang: Datei | - (stdin) | udp://[addr]:port | prbs[:N] | null   (Standard: prbs)\n"
"  -o PATH        Ausgabe: Datei | - (stdout) | iio: | iio:URI | /dev/null              (Standard: -)\n"
"  --mode 1|2|3   (Standard 3 = 8k)      --guard 1/4|1/8|1/16|1/32   (Standard 1/8)\n"
"  --layers 1|2   1: Schicht A über 13 Segmente; 2: A = 1 Segment (1seg, Teilempfang) + B = 12 Segmente (Standard 2)\n"
"  --a-mod qpsk|16qam|64qam --a-rate 1/2|2/3|3/4|5/6|7/8 --a-ti I   (Standard: qpsk 2/3, I=4)\n"
"  --b-mod …  --b-rate …  --b-ti I                                       (Standard: 64qam 3/4, I=2)\n"
"                 I (Zeitverschachtelung): Modus 3: 0|1|2|4, Modus 2: 0|2|4|8, Modus 1: 0|4|8|16\n"
"  --layer-a-pids P,P,…  PIDs (dezimal/0x…) für Schicht A; übrige → B. PSI/SI (PAT, CAT, NIT, SDT, EIT, TDT/TOT,\n"
"                 1seg-PMT 0x1FC8..0x1FCF) gehen in beide Schichten. Ohne Liste bei 2 Schichten: alles → B.\n"
"                 Eingang prbs: je Schicht eine eigene PRBS-Quelle.\n"
"  --print-rates  nutzbare TS-Raten als JSON-Zeile auf stdout ausgeben und beenden\n"
"  --backoff dB   RMS-Abstand zur Vollaussteuerung (Standard 12)\n"
"  --window W     RC-Symbolfensterung über W Samples im Guard (Standard N/256, max. Guard/2; 0 = aus)\n"
"  --packets N    nach N Eingangspaketen enden   --seconds S   nach S Sekunden Signal (jeweils am Rahmenende)\n"
"  --loop         Eingabedatei wiederholen      --udp-buffer N   UDP-Ring in Paketen (4000)\n"
"  --threads 1|2  --affinity  --rt   --realtime (Datei/stdout mit nominaler Rate)   --profile   --bench N\n"
"  IIO: --lo HZ --atten DB --rfbw HZ --iio-buf SAMPLES --iio-kbufs N --enable --fir DATEI\n"
"  --quiet        keine Statuszeilen\n");
}

static int parse_name(const char *s, const char *const *names, int n) { for (int i = 0; i < n; i++) if (!strcmp(s, names[i])) return i; return -1; }

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

/* ------------------------------------------------------------------ PID-Zuordnung */
static int is_psi(int pid)
{
    return pid == 0x0000 || pid == 0x0001 || (pid >= 0x0010 && pid <= 0x0014) || (pid >= 0x1FC8 && pid <= 0x1FCF);
}
static int is_a_pid(int pid) { for (int i = 0; i < G.n_a_pids; i++) if (G.a_pids[i] == pid) return 1; return 0; }

static void q_push(pktq_t *q, const uint8_t *p, int l)
{
    if (q->n >= q->cap) { G.dropped[l]++; return; }
    memcpy(q->buf + (size_t)((q->head + q->n) % q->cap) * TS_PKT, p, TS_PKT); q->n++;
}
static void q_pop(pktq_t *q, uint8_t *p)
{
    memcpy(p, q->buf + (size_t)q->head * TS_PKT, TS_PKT); q->head = (q->head + 1) % q->cap; q->n--;
}
static void make_null(uint8_t *p) { p[0] = 0x47; p[1] = 0x1F; p[2] = 0xFF; p[3] = 0x10; memset(p + 4, 0xFF, 184); }

static void get_packet(void *ctx, int l, uint8_t *pkt)
{
    (void)ctx;
    if (G.ts_l[0]) {                                   /* PRBS: eigene Quelle je Schicht */
        if (G.eof || !ts_input_packet(G.ts_l[l], pkt)) { G.eof = 1; make_null(pkt); G.stuffed[l]++; return; }
        if (G.max_packets && ++G.in_packets >= G.max_packets) G.eof = 1;
        return;
    }
    pktq_t *q = &G.q[l];
    int nulls = 0;
    while (q->n == 0 && !G.eof) {
        /* Andere Schicht voll → nicht weiterlesen (sonst Verlust), stattdessen stopfen */
        if (G.cfg.nlayers == 2 && G.q[1 - l].n >= G.q[1 - l].cap) break;
        uint8_t p[TS_PKT];
        if (!ts_input_packet(G.ts, p)) { G.eof = 1; break; }
        G.in_packets++;
        if (G.max_packets && G.in_packets >= G.max_packets) G.eof = 1;
        int pid = ((p[1] & 0x1F) << 8) | p[2];
        if (pid == 0x1FFF) { G.nulls_in++; if (++nulls >= 8) break; continue; }   /* Eingangsstopfung verwerfen */
        nulls = 0;
        if (G.cfg.nlayers == 1) q_push(&G.q[0], p, 0);
        else if (is_psi(pid)) { q_push(&G.q[0], p, 0); q_push(&G.q[1], p, 1); G.dup_psi++; }
        else if (G.have_a_pids && is_a_pid(pid)) q_push(&G.q[0], p, 0);
        else q_push(&G.q[1], p, 1);
    }
    if (q->n) q_pop(q, pkt);
    else { make_null(pkt); G.stuffed[l]++; }
}

/* ------------------------------------------------------------------ float-IFFT für Modus 2 (4096) */
static void fifft_init(int n)
{
    G.fre = (float *)malloc(n * sizeof(float)); G.fim = (float *)malloc(n * sizeof(float));
    G.twr = (float *)malloc(n / 2 * sizeof(float)); G.twi = (float *)malloc(n / 2 * sizeof(float));
    G.bitrev = (int *)malloc(n * sizeof(int));
    int lg = 0; while ((1 << lg) < n) lg++;
    for (int i = 0; i < n; i++) { int r = 0; for (int b = 0; b < lg; b++) if (i >> b & 1) r |= 1 << (lg - 1 - b); G.bitrev[i] = r; }
    for (int i = 0; i < n / 2; i++) { G.twr[i] = (float)cos(2 * M_PI * i / n); G.twi[i] = (float)sin(2 * M_PI * i / n); }
}
static inline int16_t sat16f(float x) { long v = lrintf(x); return (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v); }
static void fifft_ofdm(const int16_t *in, int16_t *out, int n, int guard, float scale)
{
    float *re = G.fre, *im = G.fim;
    for (int i = 0; i < n; i++) { int r = G.bitrev[i]; re[r] = in[2 * i]; im[r] = in[2 * i + 1]; }
    for (int len = 2; len <= n; len <<= 1) {
        int h = len / 2, st = n / len;
        for (int i = 0; i < n; i += len)
            for (int j = 0; j < h; j++) {
                float wr = G.twr[j * st], wi = G.twi[j * st];
                float xr = re[i + j + h] * wr - im[i + j + h] * wi, xi = re[i + j + h] * wi + im[i + j + h] * wr;
                re[i + j + h] = re[i + j] - xr; im[i + j + h] = im[i + j] - xi; re[i + j] += xr; im[i + j] += xi;
            }
    }
    int16_t *o = out + 2 * guard;
    for (int i = 0; i < n; i++) { o[2 * i] = sat16f(re[i] * scale); o[2 * i + 1] = sat16f(im[i] * scale); }
    memcpy(out, out + 2 * n, (size_t)guard * 4);
}

/* ------------------------------------------------------------------ Status */
static double signal_seconds(uint64_t sym) { return (double)sym * (G.nfft + G.guard_len) / ISDBT_FS; }
static void status_line(void)
{
    ts_stats_t st; memset(&st, 0, sizeof st);
    if (G.ts) ts_input_stats(G.ts, &st);
    double el = dvb_now() - G.wall_start, gen = signal_seconds(G.symbols_out);
    fprintf(stderr, "{\"t\":%.1f,\"symbols\":%llu,\"signal_s\":%.2f,\"rt_factor\":%.2f,\"cpu_coder\":%.2f,\"cpu_fft\":%.2f,"
            "\"ts_in\":%lld,\"stuffed_a\":%llu,\"stuffed_b\":%llu,\"dropped_a\":%llu,\"dropped_b\":%llu,\"q_a\":%d,\"q_b\":%d,\"fill\":%d,\"cap\":%d}\n",
            el, (unsigned long long)G.symbols_out, gen, el > 0 ? gen / el : 0, G.cpu_a / (el > 0 ? el : 1), G.cpu_b / (el > 0 ? el : 1),
            G.in_packets, (unsigned long long)G.stuffed[0], (unsigned long long)G.stuffed[1],
            (unsigned long long)G.dropped[0], (unsigned long long)G.dropped[1], G.q[0].n, G.q[1].n, st.fill, st.capacity);
}

/* ------------------------------------------------------------------ Coder-Thread (Kern 0) */
static void *coder_thread(void *arg)
{
    (void)arg;
    if (G.affinity) pin_cpu(0);
    if (G.rt_prio) set_rt(G.rt_prio);
    long long eof_frames = -1;
    for (;;) {
        if (G.stop) break;
        if (G.frame.symbol == 0) {
            if (G.max_seconds > 0 && signal_seconds(G.symbols_coded) >= G.max_seconds) break;
            if (G.eof) { if (eof_frames < 0) eof_frames = 0; else if (++eof_frames > G.flush_frames) break; }
        }
        if (G.bench_symbols > 0 && (long long)G.symbols_coded >= G.bench_symbols) break;
        pthread_mutex_lock(&G.mx);
        while ((G.head + 1) % RING == G.tail && !G.stop) pthread_cond_wait(&G.cv_empty, &G.mx);
        pthread_mutex_unlock(&G.mx);
        if (G.stop) break;
        for (int l = 0; l < G.cfg.nlayers; l++) isdbt_layer_symbol(&G.lay[l], get_packet, NULL, G.profile);
        double t0 = G.profile ? dvb_now() : 0;
        isdbt_frame_build(&G.frame, G.lay, G.slots[G.head]);
        if (G.profile) G.t_frame += dvb_now() - t0;
        G.symbols_coded++;
        pthread_mutex_lock(&G.mx);
        G.head = (G.head + 1) % RING;
        pthread_cond_signal(&G.cv_full);
        pthread_mutex_unlock(&G.mx);
    }
    pthread_mutex_lock(&G.mx); G.done = 1; pthread_cond_signal(&G.cv_full); pthread_mutex_unlock(&G.mx);
    G.cpu_a = dvb_cpu_time();
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
        double t0 = G.profile ? dvb_now() : 0;
        int16_t *o = out + (size_t)nbatch * nsym * 2;
        if (G.fft) fft16_ifft_ofdm(G.fft, G.slots[G.tail], o, G.guard_len);
        else fifft_ofdm(G.slots[G.tail], o, G.nfft, G.guard_len, 1.0f / 64.0f);
        ofdm_window_apply(&G.win, o, G.guard_len);
        if (G.profile) G.t_fft += dvb_now() - t0;
        pthread_mutex_lock(&G.mx);
        G.tail = (G.tail + 1) % RING;
        pthread_cond_signal(&G.cv_empty);
        pthread_mutex_unlock(&G.mx);
        if (++nbatch == batch) {
            if (G.out && out_write(G.out, out, (size_t)nsym * batch) < 0) { G.err = 1; G.stop = 1; }
            nbatch = 0;
        }
        G.symbols_out++;
        if (!G.quiet && dvb_now() - last_status >= 1.0) { G.cpu_b = dvb_cpu_time(); last_status = dvb_now(); status_line(); }
    }
    if (nbatch && G.out) out_write(G.out, out, (size_t)nsym * nbatch);
    G.cpu_b = dvb_cpu_time();
    free(out);
    return NULL;
}

static int parse_layer_opt(const char *a, const char *v, int *ok)
{
    static const char *const mnames[] = {"qpsk", "16qam", "64qam"};
    *ok = 1;
    for (int l = 0; l < 2; l++) {
        char key[16];
        snprintf(key, sizeof key, "--%c-mod", 'a' + l);
        if (!strcmp(a, key)) { G.cfg.L[l].mod = parse_name(v, mnames, 3); return G.cfg.L[l].mod >= 0; }
        snprintf(key, sizeof key, "--%c-rate", 'a' + l);
        if (!strcmp(a, key)) { G.cfg.L[l].cr = parse_name(v, dvb_coderate_name, 5); return G.cfg.L[l].cr >= 0; }
        snprintf(key, sizeof key, "--%c-ti", 'a' + l);
        if (!strcmp(a, key)) { G.cfg.L[l].ti = atoi(v); return 1; }
    }
    *ok = 0; return 0;
}

int main(int argc, char **argv)
{
    memset(&G, 0, sizeof G);
    G.cfg.mode = 3; G.cfg.guard = GI_1_8; G.cfg.nlayers = 2;
    G.cfg.L[0] = (isdbt_layer_cfg_t){ISDBT_QPSK, CR_2_3, 4, 1};
    G.cfg.L[1] = (isdbt_layer_cfg_t){ISDBT_64QAM, CR_3_4, 2, 12};
    G.backoff_db = 12; G.in_spec = "prbs"; G.out_path = "-"; G.threads = 2; G.udp_buffer = 4000; G.window = -1;
    G.op.atten_db = 89.75; G.op.kernel_buffers = 4;
    static const char *const gnames[] = {"1/32", "1/16", "1/8", "1/4"};
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        int ok;
#define ARG(name) (!strcmp(a, name) && i + 1 < argc)
        if (i + 1 < argc && !strncmp(a, "--", 2) && a[3] == '-' && (a[2] == 'a' || a[2] == 'b')) {
            int r = parse_layer_opt(a, argv[i + 1], &ok);
            if (ok) { if (!r) { usage(); return 2; } i++; continue; }
        }
        if (ARG("-i")) G.in_spec = argv[++i];
        else if (ARG("-o")) G.out_path = argv[++i];
        else if (ARG("--mode")) G.cfg.mode = atoi(argv[++i]);
        else if (ARG("--guard")) { G.cfg.guard = parse_name(argv[++i], gnames, 4); if (G.cfg.guard < 0) { usage(); return 2; } }
        else if (ARG("--layers")) G.cfg.nlayers = atoi(argv[++i]);
        else if (ARG("--layer-a-pids")) {
            char *s = argv[++i], *e;
            G.have_a_pids = 1;
            while (*s && G.n_a_pids < MAXPIDS) { long v = strtol(s, &e, 0); if (e == s) { usage(); return 2; } G.a_pids[G.n_a_pids++] = (int)v; s = (*e == ',') ? e + 1 : e; }
        }
        else if (!strcmp(a, "--print-rates")) G.print_rates = 1;
        else if (ARG("--backoff")) G.backoff_db = atof(argv[++i]);
        else if (ARG("--packets")) G.max_packets = atoll(argv[++i]);
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
        else if (ARG("--window")) G.window = atoi(argv[++i]);
        else if (ARG("--seg-type")) G.cfg.seg_type = atoi(argv[++i]);
        else if (ARG("--lo")) G.op.lo_hz = atof(argv[++i]);
        else if (ARG("--atten")) G.op.atten_db = atof(argv[++i]);
        else if (ARG("--rfbw")) G.op.rf_bw_hz = atof(argv[++i]);
        else if (ARG("--iio-buf")) G.op.buf_samples = atoi(argv[++i]);
        else if (ARG("--iio-kbufs")) G.op.kernel_buffers = atoi(argv[++i]);
        else if (!strcmp(a, "--enable")) G.op.enable = 1;
        else if (ARG("--fir")) G.op.fir_file = argv[++i];
        else if (!strcmp(a, "-h") || !strcmp(a, "--help")) { usage(); return 0; }
        else { usage(); return 2; }
    }
    if (G.cfg.nlayers == 1) G.cfg.L[0].nseg = 13, G.cfg.partial = 0;
    else G.cfg.L[0].nseg = 1, G.cfg.L[1].nseg = 12, G.cfg.partial = 1;
    char err[160];
    if (isdbt_cfg_check(&G.cfg, err, sizeof err) < 0) { fprintf(stderr, "Parameter: %s\n", err); return 2; }
    G.nfft = isdbt_nfft(G.cfg.mode);
    G.guard_len = G.nfft >> (5 - G.cfg.guard);
    double ra = isdbt_layer_bitrate(&G.cfg, 0), rb = isdbt_layer_bitrate(&G.cfg, 1);
    if (G.print_rates) {
        printf("{\"layer_a_bps\":%.3f,\"layer_b_bps\":%.3f,\"total_bps\":%.3f,\"sample_rate\":%.3f,\"mode\":%d,\"guard\":\"%s\",\"layers\":%d,"
               "\"a_packets_per_frame\":%d,\"b_packets_per_frame\":%d,\"frame_s\":%.9f}\n",
               ra, rb, ra + rb, ISDBT_FS, G.cfg.mode, gnames[G.cfg.guard], G.cfg.nlayers,
               isdbt_layer_bytes_per_symbol(&G.cfg, 0), G.cfg.nlayers > 1 ? isdbt_layer_bytes_per_symbol(&G.cfg, 1) : 0,
               204.0 * (G.nfft + G.guard_len) / ISDBT_FS);
        return 0;
    }
    if (G.window < 0) { G.window = G.nfft / 256; if (G.window > G.guard_len / 2) G.window = G.guard_len / 2; }
    if (G.window > G.guard_len) G.window = G.guard_len;
    G.window &= ~7;

    dvb_rs_init();
    for (int l = 0; l < G.cfg.nlayers; l++)
        if (isdbt_layer_init(&G.lay[l], &G.cfg, l, ISDBT_BIT_SYMS) < 0) { fprintf(stderr, "Schicht %c: Init fehlgeschlagen\n", 'A' + l); return 1; }
    int S;
    if (G.cfg.mode == 2) { fifft_init(G.nfft); S = 6; }
    else { G.fft = fft16_create(G.nfft); if (!G.fft) { fprintf(stderr, "FFT %d nicht unterstützt\n", G.nfft); return 1; } S = fft16_shift_bits(G.fft); }
    isdbt_frame_t tmp;
    int rc = isdbt_frame_init(&tmp, &G.cfg, G.lay, 1000);
    if (rc < 0) { fprintf(stderr, "Trägertabellen inkonsistent (%d)\n", rc); return 1; }
    double P = tmp.mean_cell_power; isdbt_frame_free(&tmp);
    double rms = 32767.0 * pow(10.0, -G.backoff_db / 20.0);
    int A = (int)lrint(rms * (double)(1 << S) / sqrt(P));
    if (A * 4 / 3 > 32000) A = 24000;
    if (isdbt_frame_init(&G.frame, &G.cfg, G.lay, A) < 0) return 1;
    if (ofdm_window_init(&G.win, G.window) < 0) return 1;
    for (int i = 0; i < RING; i++) G.slots[i] = (int16_t *)calloc(G.nfft, 4);
    pthread_mutex_init(&G.mx, NULL); pthread_cond_init(&G.cv_full, NULL); pthread_cond_init(&G.cv_empty, NULL);
    /* Nach Eingangsende: Interleaver leeren (Zeitverschachtelung + Ausgleich + Byte-Interleaver), dann Rahmenende */
    int maxd = 0;
    for (int l = 0; l < G.cfg.nlayers; l++) { int d = G.lay[l].tdepth + ISDBT_BIT_SYMS + 1; if (d > maxd) maxd = d; }
    G.flush_frames = (maxd + 203) / 204 + 1;

    if (!strncmp(G.in_spec, "prbs", 4)) {
        for (int l = 0; l < G.cfg.nlayers; l++) G.ts_l[l] = ts_input_open(G.in_spec, 0, 0, 0);
    } else {
        G.ts = ts_input_open(G.in_spec, G.loop, G.udp_buffer, 0);
        if (!G.ts) { fprintf(stderr, "Eingang %s nicht zu öffnen\n", G.in_spec); return 1; }
    }
    for (int l = 0; l < G.cfg.nlayers; l++) {
        G.q[l].cap = 2 * G.lay[l].N + 64;      /* zwei Rahmen Puffer */
        G.q[l].buf = (uint8_t *)malloc((size_t)G.q[l].cap * TS_PKT);
    }
    if (G.out_path) {
        G.op.path = G.out_path; G.op.sample_rate = ISDBT_FS;
        if (G.op.rf_bw_hz <= 0) G.op.rf_bw_hz = 6e6;
        if (G.op.buf_samples <= 0) G.op.buf_samples = 8 * (G.nfft + G.guard_len);
        G.out = out_open(&G.op);
        if (!G.out) { fprintf(stderr, "Ausgabe %s nicht zu öffnen\n", G.out_path); return 1; }
        if (G.realtime) out_set_realtime(G.out, 1);
    }
    static const char *const mn[] = {"qpsk", "16qam", "64qam"};
    if (!G.quiet) {
        fprintf(stderr, "{\"mode\":%d,\"guard\":\"%s\",\"layers\":%d,\"sample_rate\":%.3f,\"nfft\":%d,\"amp\":%d,\"backoff_db\":%g,\"window\":%d,\"fft\":\"%s\"",
                G.cfg.mode, gnames[G.cfg.guard], G.cfg.nlayers, ISDBT_FS, G.nfft, A, G.backoff_db, G.window, G.fft ? fft16_backend() : "float-radix2");
        for (int l = 0; l < G.cfg.nlayers; l++)
            fprintf(stderr, ",\"%c\":{\"seg\":%d,\"mod\":\"%s\",\"rate\":\"%s\",\"ti\":%d,\"bps\":%.3f,\"tsp_per_frame\":%d}",
                    'A' + l, G.cfg.L[l].nseg, mn[G.cfg.L[l].mod], dvb_coderate_name[G.cfg.L[l].cr], G.cfg.L[l].ti,
                    isdbt_layer_bitrate(&G.cfg, l), G.lay[l].N);
        fprintf(stderr, "}\n");
    }
    signal(SIGINT, on_sigint); signal(SIGTERM, on_sigint);
    G.wall_start = dvb_now();
    pthread_t ta, tb;
    pthread_create(&ta, NULL, coder_thread, NULL);
    if (G.threads >= 2) { pthread_create(&tb, NULL, fft_thread, NULL); pthread_join(tb, NULL); }
    else fft_thread(NULL);
    pthread_join(ta, NULL);
    double el = dvb_now() - G.wall_start, sig = signal_seconds(G.symbols_out);
    fprintf(stderr, "{\"done\":true,\"symbols\":%llu,\"frames\":%.2f,\"signal_s\":%.3f,\"wall_s\":%.3f,\"rt_factor\":%.3f,\"cpu_coder_s\":%.3f,\"cpu_fft_s\":%.3f,"
            "\"us_per_symbol_coder\":%.1f,\"us_per_symbol_fft\":%.1f,\"symbol_us\":%.1f,\"ts_in\":%lld,\"packets_a\":%llu,\"packets_b\":%llu,"
            "\"stuffed_a\":%llu,\"stuffed_b\":%llu,\"dropped_a\":%llu,\"dropped_b\":%llu,\"nulls_in\":%llu,\"psi_dup\":%llu",
            (unsigned long long)G.symbols_out, G.symbols_out / 204.0, sig, el, el > 0 ? sig / el : 0, G.cpu_a, G.cpu_b,
            G.symbols_out ? 1e6 * G.cpu_a / G.symbols_out : 0, G.symbols_out ? 1e6 * G.cpu_b / G.symbols_out : 0,
            1e6 * (G.nfft + G.guard_len) / ISDBT_FS, G.in_packets,
            (unsigned long long)G.lay[0].packets, (unsigned long long)G.lay[1].packets,
            (unsigned long long)G.stuffed[0], (unsigned long long)G.stuffed[1], (unsigned long long)G.dropped[0], (unsigned long long)G.dropped[1],
            (unsigned long long)G.nulls_in, (unsigned long long)G.dup_psi);
    if (G.profile && G.symbols_out) {
        double n = (double)G.symbols_out;
        fprintf(stderr, ",\"profile_us\":{\"fec\":%.1f,\"bit_il\":%.1f,\"time_il\":%.1f,\"frame\":%.1f,\"fft\":%.1f}",
                1e6 * (G.lay[0].t_fec + G.lay[1].t_fec) / n, 1e6 * (G.lay[0].t_bit + G.lay[1].t_bit) / n,
                1e6 * (G.lay[0].t_ti + G.lay[1].t_ti) / n, 1e6 * G.t_frame / n, 1e6 * G.t_fft / n);
    }
    fprintf(stderr, "}\n");
    if (G.out) out_close(G.out);
    if (G.ts) ts_input_close(G.ts);
    for (int l = 0; l < 2; l++) if (G.ts_l[l]) ts_input_close(G.ts_l[l]);
    return G.err;
}
