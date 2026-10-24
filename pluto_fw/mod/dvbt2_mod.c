/*
 * dvbt2_mod – DVB-T2-Modulator (ETSI EN 302 755, T2-base, SISO, ein PLP, TS in HEM) für den ADALM-Pluto (Labor-Prüfsender).
 *
 * Pipeline (2 Threads):
 *   Kern 0: TS → BBFRAME (HEM) → BCH → LDPC → Bit-IL/Demux → Mapping (rotiert) → Zell-IL → Zeit-IL → Rahmen (L1, Dummy)
 *           → Frequenz-Interleaver   (ganzer T2-Rahmen je Durchlauf, Ring aus 3 Rahmen)
 *   Kern 1: P1 (vorab berechnet) + je Symbol Piloten/Datenträger → int16-IFFT → Guard → Ausgabe (Datei/stdout/IIO-DMA)
 * Abtastrate = Elementartakt 1/T (8 MHz: 64/7 = 9,142857 MS/s), IQ int16 LE, LO = Kanalmitte.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "lib/dvbmod.h"
#include "lib/dvbt2.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <signal.h>
#include <unistd.h>
#include <pthread.h>
#include <sched.h>
#include <dirent.h>

#define RING 3

static volatile sig_atomic_t stop_flag;
static void on_sig(int s) { (void)s; stop_flag = 1; }

static struct {
    t2_tx_t *tx; ts_input_t *ts; out_sink_t *out; const t2_info_t *I;
    int threads, affinity, rt_prio, quiet, profile;
    long long max_frames, frames_enc, frames_out, npk;
    int eof;
    t2_frame_t *ring[RING]; int head, tail, done, err;
    pthread_mutex_t mx; pthread_cond_t cv_full, cv_empty;
    double t0, cpu_coder, cpu_out, render_s, write_s;
    uint64_t samples;
    int16_t *sbuf;
} G;

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
/* --rt: die Netzverarbeitung des Kernels (ksoftirqd) läuft sonst SCHED_OTHER und verhungert neben den RT-Rechenthreads →
   die netdev-Warteschlange läuft über (USB-RNDIS, ~2600 Datagramme/s). ksoftirqd über die Rechenthreads, Warteschlange größer.
   Ohne root: still übergehen. */
static void sys_tune(int prio)
{
#ifdef __linux__
    FILE *f = fopen("/proc/sys/net/core/netdev_max_backlog", "r+");
    if (f) { int v = 0; if (fscanf(f, "%d", &v) == 1 && v < 20000) { rewind(f); fprintf(f, "20000\n"); } fclose(f); }
    DIR *d = opendir("/proc");
    if (!d) return;
    struct dirent *e;
    while ((e = readdir(d))) {
        int pid = atoi(e->d_name);
        if (pid <= 0) continue;
        char p[64], comm[32] = ""; snprintf(p, sizeof p, "/proc/%d/comm", pid);
        FILE *c = fopen(p, "r");
        if (!c) continue;
        if (fgets(comm, sizeof comm, c) && !strncmp(comm, "ksoftirqd/", 10)) {
            struct sched_param sp; sp.sched_priority = prio;
            sched_setscheduler(pid, SCHED_FIFO, &sp);
        }
        fclose(c);
    }
    closedir(d);
#else
    (void)prio;
#endif
}

static int src_pkt(void *ctx, uint8_t *pkt)
{
    (void)ctx;
    if (G.eof) return 0;                      /* Nullpaket (bbframe stopft selbst) */
    if (ts_input_packet(G.ts, pkt)) { G.npk++; return 1; }
    G.eof = 1;
    return 0;
}

static int encode_one(t2_frame_t *f)
{
    if (stop_flag || G.eof) return 0;         /* nach Dateiende wird der laufende Rahmen mit Nullpaketen fertig */
    if (G.max_frames && G.frames_enc >= G.max_frames) return 0;
    t2_encode_frame(G.tx, src_pkt, NULL, f);
    G.frames_enc++;
    return 1;
}

static void status_line(int final);

static int render_write(const t2_frame_t *f)
{
    const t2_info_t *I = G.I;
    const int sym = I->N + I->guard;
    double a = G.profile ? dvb_now() : 0, rs = 0, ws = 0;
    int16_t *d = out_direct(G.out, 2048);
    if (d) { t2_render_p1(G.tx, d); if (out_commit(G.out, 2048) < 0) return -1; }
    else { t2_render_p1(G.tx, G.sbuf); if (out_write(G.out, G.sbuf, 2048) < 0) return -1; }
    G.samples += 2048;
    for (int l = 0; l < I->lf; l++) {
        double b = G.profile ? dvb_now() : 0;
        d = out_direct(G.out, (size_t)sym);     /* IIO: IFFT schreibt direkt in den DMA-Puffer (an Puffergrenzen über sbuf) */
        t2_render_symbol(G.tx, f, l, d ? d : G.sbuf);
        double c = G.profile ? dvb_now() : 0;
        if (d ? out_commit(G.out, (size_t)sym) < 0 : out_write(G.out, G.sbuf, (size_t)sym) < 0) return -1;
        if (G.profile) { rs += c - b; ws += dvb_now() - c; }
        G.samples += (uint64_t)sym;
    }
    (void)a;
    G.render_s += rs; G.write_s += ws;
    G.frames_out++;
    return 0;
}

static void *coder_thread(void *arg)
{
    (void)arg;
    if (G.affinity) pin_cpu(0);
    if (G.rt_prio) set_rt(G.rt_prio);
    double c0 = dvb_cpu_time();
    for (;;) {
        pthread_mutex_lock(&G.mx);
        while ((G.head + 1) % RING == G.tail && !G.err) pthread_cond_wait(&G.cv_empty, &G.mx);
        int err = G.err;
        pthread_mutex_unlock(&G.mx);
        if (err) break;
        if (!encode_one(G.ring[G.head])) break;
        pthread_mutex_lock(&G.mx);
        G.head = (G.head + 1) % RING;
        pthread_cond_signal(&G.cv_full);
        pthread_mutex_unlock(&G.mx);
        G.cpu_coder = dvb_cpu_time() - c0;
    }
    G.cpu_coder = dvb_cpu_time() - c0;
    pthread_mutex_lock(&G.mx); G.done = 1; pthread_cond_signal(&G.cv_full); pthread_mutex_unlock(&G.mx);
    return NULL;
}

static void *output_thread(void *arg)
{
    (void)arg;
    if (G.affinity) pin_cpu(1);
    if (G.rt_prio) set_rt(G.rt_prio);
    double c0 = dvb_cpu_time(), last = dvb_now();
    for (;;) {
        pthread_mutex_lock(&G.mx);
        while (G.head == G.tail && !G.done) pthread_cond_wait(&G.cv_full, &G.mx);
        if (G.head == G.tail && G.done) { pthread_mutex_unlock(&G.mx); break; }
        pthread_mutex_unlock(&G.mx);
        if (render_write(G.ring[G.tail]) < 0) {
            fprintf(stderr, "Schreibfehler\n");
            pthread_mutex_lock(&G.mx); G.err = 1; pthread_cond_signal(&G.cv_empty); pthread_mutex_unlock(&G.mx);
            break;
        }
        pthread_mutex_lock(&G.mx);
        G.tail = (G.tail + 1) % RING;
        pthread_cond_signal(&G.cv_empty);
        pthread_mutex_unlock(&G.mx);
        G.cpu_out = dvb_cpu_time() - c0;
        if (!G.quiet) { double nw = dvb_now(); if (nw - last >= 1.0) { last = nw; status_line(0); } }
    }
    G.cpu_out = dvb_cpu_time() - c0;
    return NULL;
}

static void status_line(int final)
{
    ts_stats_t st; ts_input_stats(G.ts, &st);
    double el = dvb_now() - G.t0, sig = G.samples / G.I->fs;
    if (el <= 0) el = 1e-9;
    if (!final) {
        fprintf(stderr, "{\"t\":%.1f,\"frames\":%lld,\"signal_s\":%.2f,\"rt_factor\":%.2f,\"cpu_coder\":%.2f,\"cpu_out\":%.2f,"
                "\"ts_in\":%llu,\"stuffed\":%llu,\"dropped\":%llu,\"fill\":%d}\n",
                el, G.frames_out, sig, sig / el, G.cpu_coder / el, G.cpu_out / el,
                (unsigned long long)st.packets_in, (unsigned long long)st.nulls_stuffed, (unsigned long long)st.nulls_dropped, st.fill);
        return;
    }
    double nf = G.frames_out > 0 ? (double)G.frames_out : 1, pr[8];
    t2_profile(G.tx, G.profile, pr);
    fprintf(stderr, "{\"done\":true,\"frames\":%lld,\"packets\":%lld,\"samples\":%llu,\"signal_s\":%.3f,\"wall_s\":%.3f,\"rt_factor\":%.3f,"
            "\"cpu_coder_s\":%.3f,\"cpu_out_s\":%.3f,\"load_coder\":%.3f,\"load_out\":%.3f,\"frame_ms\":%.3f,\"threads\":%d,"
            "\"ts_in\":%llu,\"stuffed\":%llu,\"dropped\":%llu",
            G.frames_out, G.npk, (unsigned long long)G.samples, sig, el, sig / el, G.cpu_coder, G.cpu_out,
            G.cpu_coder / (sig > 0 ? sig : 1), G.cpu_out / (sig > 0 ? sig : 1), G.I->tf_s * 1e3, G.threads,
            (unsigned long long)st.packets_in, (unsigned long long)st.nulls_stuffed, (unsigned long long)st.nulls_dropped);
    if (G.profile) {
        double fecb = nf * G.I->nfec;
        fprintf(stderr, ",\"prof_us_per_fec\":{\"bb\":%.1f,\"bch\":%.1f,\"ldpc\":%.1f,\"bitil\":%.1f,\"map_cellil\":%.1f},"
                "\"prof_ms_per_frame\":{\"ti_read\":%.2f,\"sym_flush\":%.2f,\"frame_fi\":%.2f,\"render\":%.2f,\"write\":%.2f}",
                pr[0] / fecb * 1e6, pr[1] / fecb * 1e6, pr[2] / fecb * 1e6, pr[3] / fecb * 1e6, pr[4] / fecb * 1e6,
                pr[5] / nf * 1e3, pr[7] / nf * 1e3, pr[6] / nf * 1e3, G.render_s / nf * 1e3, G.write_s / nf * 1e3);
    }
    fprintf(stderr, "}\n");
}

static void usage(void)
{
    fprintf(stderr,
"dvbt2_mod – DVB-T2-Modulator (EN 302 755, T2-base, SISO, 1 PLP, TS/HEM), IQ int16 LE, Abtastrate 1/T (8 MHz: 9,142857 MS/s)\n"
"  -i SPEC          TS-Eingang: Datei | - | udp://[addr]:port | prbs[:N] | null   (Standard prbs)\n"
"  -o PATH          Ausgabe: Datei | - | /dev/null | iio: | iio:URI   (Standard -)\n"
"  --preset de|leicht  DE: 8 MHz 32K ext 64QAM 2/3 GI 19/256 PP4 rot; Leicht: 8K ext 64QAM 3/5 GI 1/8 PP2 rot (Standard de)\n"
"  --bw 1.7|5|6|7|8|10  --fft 1k|2k|4k|8k|16k|32k  --ext 0|1  --const qpsk|16qam|64qam|256qam\n"
"  --rate 1/2|3/5|2/3|3/4|4/5|5/6  --guard 1/128|1/32|1/16|19/256|1/8|19/128|1/4  --pp 1..8  --rotation 0|1\n"
"  --fec 64800|16200  --ldata N (Datensymbole, Standard max. ≤ 250 ms)  --nfec N (FEC-Blöcke/Rahmen, Standard max.)\n"
"  --nti N (TI-Blöcke, Standard min.)  --t2frames N (Standard 2)  --l1-const bpsk|qpsk|16qam|64qam (Standard bpsk)\n"
"  --cell-id N --network-id N --t2-system-id N --frequency HZ (L1-post; Standard = --lo)\n"
"  --backoff dB     RMS der Datensymbole unter Vollaussteuerung (Standard 12)\n"
"  --frames N | --seconds S   Dauer   --loop   --realtime   --udp-buffer N   --quiet   --profile\n"
"  --threads 1|2    (Standard 2: Kodierung | Piloten/IFFT/Ausgabe)   --affinity   --rt\n"
"  --print-rates    eine JSON-Zeile (ts_bps, sample_rate, …) auf stdout und Ende   --selftest   --dump PREFIX\n"
"  IIO: --lo HZ (= Kanalmitte) --atten DB --rfbw HZ (Standard = Bandbreite) --iio-buf SAMPLES --iio-kbufs N --enable --fir DATEI\n");
}

static int lookup(const char *s, const char *const *names, int n)
{
    char b[32]; int k = 0;
    for (const char *p = s; *p && k < 31; p++) if (*p != '-' && *p != ' ') b[k++] = (char)(*p >= 'A' && *p <= 'Z' ? *p + 32 : *p);
    b[k] = 0;
    for (int i = 0; i < n; i++) if (!strcmp(b, names[i])) return i;
    return -1;
}

static void preset(t2_cfg_t *c, const char *p)
{
    c->bw10 = 80; c->ext = 1; c->mod = T2_64QAM; c->rot = 1; c->fec16k = 0;
    if (!strcmp(p, "leicht")) { c->fft = 8192; c->rate = T2_R3_5; c->gi = T2_GI_1_8; c->pp = 2; }
    else { c->fft = 32768; c->rate = T2_R2_3; c->gi = T2_GI_19_256; c->pp = 4; }
}

int main(int argc, char **argv)
{
    t2_cfg_t c; memset(&c, 0, sizeof c);
    preset(&c, "de");
    c.l1mod = T2_L1_BPSK; c.network_id = 0x3085; c.t2_system_id = 0x8001; c.plp_group_id = 1;
    int print_rates = 0, loop = 0, realtime = 0, udp_buffer = 2000, selftest = 0, have_freq = 0, ext_given = 0;
    double backoff = 12, seconds = 0;
    const char *in_spec = "prbs", *out_path = "-", *dump = NULL, *fir = NULL;
    out_params_t op; memset(&op, 0, sizeof op); op.atten_db = 89.75; op.kernel_buffers = 4;
    G.threads = 2;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
#define ARG(name) (!strcmp(a, name) && i + 1 < argc)
        if (ARG("-i")) in_spec = argv[++i];
        else if (ARG("-o")) out_path = argv[++i];
        else if (ARG("--preset")) preset(&c, argv[++i]);
        else if (ARG("--bw")) c.bw10 = (int)lrint(atof(argv[++i]) * 10);
        else if (ARG("--fft")) { const char *s = argv[++i]; int k = atoi(s); c.fft = (strchr(s, 'k') || strchr(s, 'K')) ? k * 1024 : k; }
        else if (ARG("--ext")) { c.ext = atoi(argv[++i]); ext_given = 1; }
        else if (ARG("--const")) { static const char *const n[4] = { "qpsk", "16qam", "64qam", "256qam" }; c.mod = lookup(argv[++i], n, 4); }
        else if (ARG("--rate")) c.rate = lookup(argv[++i], t2_rate_name, 6);
        else if (ARG("--guard")) c.gi = lookup(argv[++i], t2_gi_name, 7);
        else if (ARG("--pp")) { const char *s = argv[++i]; c.pp = atoi((s[0] == 'P' || s[0] == 'p') ? s + 2 : s); }
        else if (ARG("--rotation")) c.rot = atoi(argv[++i]);
        else if (ARG("--fec")) c.fec16k = atoi(argv[++i]) == 16200;
        else if (ARG("--ldata")) c.ldata = atoi(argv[++i]);
        else if (ARG("--nfec")) c.nfec = atoi(argv[++i]);
        else if (ARG("--nti")) c.nti = atoi(argv[++i]);
        else if (ARG("--t2frames")) c.nt2 = atoi(argv[++i]);
        else if (ARG("--l1-const")) { static const char *const n[4] = { "bpsk", "qpsk", "16qam", "64qam" }; c.l1mod = lookup(argv[++i], n, 4); }
        else if (ARG("--cell-id")) c.cell_id = (int)strtol(argv[++i], NULL, 0);
        else if (ARG("--network-id")) c.network_id = (int)strtol(argv[++i], NULL, 0);
        else if (ARG("--t2-system-id")) c.t2_system_id = (int)strtol(argv[++i], NULL, 0);
        else if (ARG("--frequency")) { c.freq_hz = (uint32_t)atof(argv[++i]); have_freq = 1; }
        else if (ARG("--backoff")) backoff = atof(argv[++i]);
        else if (ARG("--frames")) G.max_frames = atoll(argv[++i]);
        else if (ARG("--seconds")) seconds = atof(argv[++i]);
        else if (!strcmp(a, "--loop")) loop = 1;
        else if (!strcmp(a, "--realtime")) realtime = 1;
        else if (ARG("--udp-buffer")) udp_buffer = atoi(argv[++i]);
        else if (!strcmp(a, "--quiet")) G.quiet = 1;
        else if (!strcmp(a, "--profile")) G.profile = 1;
        else if (ARG("--threads")) G.threads = atoi(argv[++i]);
        else if (!strcmp(a, "--affinity")) G.affinity = 1;
        else if (!strcmp(a, "--rt")) G.rt_prio = 40;
        else if (!strcmp(a, "--print-rates")) print_rates = 1;
        else if (!strcmp(a, "--selftest")) selftest = 1;
        else if (ARG("--bench-ifft")) { int nn = atoi(argv[++i]); t2_bench_ifft(nn < 64 ? nn * 1024 : nn, 2.0); return 0; }
        else if (ARG("--dump")) dump = argv[++i];
        else if (ARG("--lo")) op.lo_hz = atof(argv[++i]);
        else if (ARG("--atten")) op.atten_db = atof(argv[++i]);
        else if (ARG("--rfbw")) op.rf_bw_hz = atof(argv[++i]);
        else if (ARG("--iio-buf")) op.buf_samples = atoi(argv[++i]);
        else if (ARG("--iio-kbufs")) op.kernel_buffers = atoi(argv[++i]);
        else if (!strcmp(a, "--enable")) op.enable = 1;
        else if (ARG("--fir")) fir = argv[++i];
        else { usage(); return 2; }
    }
    if (selftest) return t2_selftest(1) ? 1 : 0;
    if (!have_freq && op.lo_hz > 0) c.freq_hz = (uint32_t)op.lo_hz;
    if (!ext_given && c.fft < 8192) c.ext = 0;   /* Extended-Modus gibt es nur bei 8K/16K/32K (Vorgabe aus --preset) */
    if (G.threads != 1 && G.threads != 2) { usage(); return 2; }
    t2_info_t inf; char err[200];
    if (t2_cfg_resolve(&c, &inf, err, sizeof err)) { fprintf(stderr, "%s\n", err); return 2; }
    int is_iio = !strncmp(out_path, "iio:", 4);
    const char *mname[4] = { "QPSK", "16QAM", "64QAM", "256QAM" };
    if (print_rates) {
        printf("{\"ts_bps\":%.3f,\"sample_rate\":%.3f,\"bandwidth_hz\":%d,\"fft\":%d,\"ext\":%d,\"guard\":\"%s\",\"pp\":%d,"
               "\"const\":\"%s\",\"rate\":\"%s\",\"fec_len\":%d,\"rotation\":%d,\"ldata\":%d,\"np2\":%d,\"frame_s\":%.6f,"
               "\"nfec\":%d,\"nti\":%d,\"t2frames\":%d,\"ncells\":%d,\"cdata\":%d,\"cp2\":%d,\"fc\":%d,\"nfc\":%d,\"cfc\":%d,"
               "\"ctot\":%d,\"l1_const\":\"%s\",\"l1post_cells\":%d,\"dummy_cells\":%d,\"kbch\":%d,\"s2\":%d,\"lo\":\"Kanalmitte\"}\n",
               inf.ts_bps, inf.fs, c.bw10 * 100000, c.fft, c.ext, t2_gi_name[c.gi], c.pp, mname[c.mod], t2_rate_name[c.rate],
               inf.nldpc, c.rot, inf.ldata, inf.np2, inf.tf_s, inf.nfec, inf.nti, inf.nt2, inf.ncells, inf.cdata, inf.cp2,
               inf.fc, inf.nfc, inf.cfc, inf.ctot, t2_l1_name[c.l1mod], inf.dl1post, inf.ndummy, inf.kbch, inf.s2field1 << 1);
        return 0;
    }
    if (is_iio && inf.fs < 2.083e6) { fprintf(stderr, "Abtastrate %.0f S/s unter dem AD9361-Minimum ohne FIR\n", inf.fs); return 2; }
    double rms = 32767.0 * pow(10.0, -backoff / 20.0);
    G.tx = t2_tx_create(&c, rms);
    if (!G.tx) { fprintf(stderr, "Initialisierung fehlgeschlagen\n"); return 1; }
    G.I = t2_tx_info(G.tx);
    if (dump && t2_set_dump(G.tx, dump)) return 1;
    t2_profile(G.tx, G.profile, NULL);
    if (seconds > 0) G.max_frames = (long long)ceil(seconds / G.I->tf_s);
    /* Der Kodier-Thread holt je T2-Rahmen N_FEC·DFL/1496 Pakete am Stück und läuft bis zu RING-1 Rahmen voraus:
       der UDP-Ring muss mehrere Rahmen fassen (Vorfüllung 50 %), sonst wird gestopft bzw. verworfen */
    { int ppf = (int)ceil((double)G.I->nfec * (G.I->kbch - 80) / 1496.0), need = 4 * ppf;
      if (!strncmp(in_spec, "udp://", 6) && udp_buffer < need) udp_buffer = need; }
    if (G.rt_prio) { ts_input_rt_prio = G.rt_prio + 10; sys_tune(G.rt_prio + 15); }   /* UDP-Leser und Netz über den Rechenthreads */
    G.ts = ts_input_open(in_spec, loop, udp_buffer, 0);
    if (!G.ts) { fprintf(stderr, "Eingang %s nicht zu öffnen\n", in_spec); return 1; }
    op.path = out_path; op.sample_rate = G.I->fs; op.fir_file = fir;
    if (op.rf_bw_hz <= 0) op.rf_bw_hz = c.bw10 * 1e5;
    if (op.buf_samples <= 0) op.buf_samples = (G.I->N + G.I->guard) * 8;
    G.out = out_open(&op);
    if (!G.out) { fprintf(stderr, "Ausgabe %s nicht zu öffnen\n", out_path); return 1; }
    if (realtime) out_set_realtime(G.out, 1);
    if (!G.quiet)
        fprintf(stderr, "{\"standard\":\"DVB-T2 EN 302 755\",\"bw_mhz\":%.1f,\"fft\":%d,\"ext\":%d,\"const\":\"%s\",\"rate\":\"%s\",\"guard\":\"%s\","
                "\"pp\":%d,\"rotation\":%d,\"fec_len\":%d,\"ldata\":%d,\"nfec\":%d,\"nti\":%d,\"t2frames\":%d,\"l1_const\":\"%s\","
                "\"sample_rate\":%.3f,\"ts_bps\":%.3f,\"frame_ms\":%.3f,\"backoff_db\":%g,\"ifft\":\"%s\",\"ifft_scale\":%d,\"udp_buffer\":%d}\n",
                c.bw10 / 10.0, c.fft, c.ext, mname[c.mod], t2_rate_name[c.rate], t2_gi_name[c.gi], c.pp, c.rot, G.I->nldpc,
                G.I->ldata, G.I->nfec, G.I->nti, G.I->nt2, t2_l1_name[c.l1mod], G.I->fs, G.I->ts_bps, G.I->tf_s * 1e3, backoff,
                t2_ifft_backend(G.tx), G.I->ifft_scale, udp_buffer);
    signal(SIGINT, on_sig); signal(SIGTERM, on_sig);
    G.sbuf = (int16_t *)malloc(sizeof(int16_t) * 2 * (size_t)(G.I->N + G.I->guard + 2048));
    for (int i = 0; i < RING; i++) G.ring[i] = t2_frame_alloc(G.tx);
    pthread_mutex_init(&G.mx, NULL); pthread_cond_init(&G.cv_full, NULL); pthread_cond_init(&G.cv_empty, NULL);
    G.t0 = dvb_now();
    if (G.threads == 2) {
        pthread_t ta, tb;
        pthread_create(&ta, NULL, coder_thread, NULL);
        pthread_create(&tb, NULL, output_thread, NULL);
        pthread_join(ta, NULL); pthread_join(tb, NULL);
    } else {
        if (G.affinity) pin_cpu(0);
        if (G.rt_prio) set_rt(G.rt_prio);
        double c0 = dvb_cpu_time(), last = G.t0;
        while (encode_one(G.ring[0])) {
            if (render_write(G.ring[0]) < 0) { fprintf(stderr, "Schreibfehler\n"); break; }
            G.cpu_coder = dvb_cpu_time() - c0;
            if (!G.quiet) { double nw = dvb_now(); if (nw - last >= 1.0) { last = nw; status_line(0); } }
        }
        G.cpu_coder = dvb_cpu_time() - c0;
    }
    status_line(1);
    out_close(G.out); ts_input_close(G.ts);
    for (int i = 0; i < RING; i++) t2_frame_free(G.ring[i]);
    t2_tx_free(G.tx); free(G.sbuf);
    return 0;
}
