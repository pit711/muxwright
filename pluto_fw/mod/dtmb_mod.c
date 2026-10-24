/*
 * dtmb_mod – DTMB-Modulator (GB 20600-2006, China, 8-MHz-Kanal) für den ADALM-Pluto (Labor-Prüfsender).
 * TS → Verwürfelung → BCH(762,752) → LDPC(7488; 0,4/0,6/0,8) → 4QAM-NR/4QAM/16QAM/32QAM/64QAM → Zeitverschachtelung
 * (B = 52, M = 240/720) → Rahmenkörper (36 SI + 3744 Daten; C = 3780 TDS-OFDM oder C = 1 Einträger) → Rahmenkopf
 * PN420/PN595/PN945 → 7,56 MS/s → SRRC α = 0,05.
 * Ausgabe IQ int16 LE. --sps 1: 7,56 MS/s, SRRC im AD9361-TX-FIR (INT 2, 128 Taps, wird bei -o iio: automatisch
 * geladen); --sps 2: 15,12 MS/s, SRRC in Software (gleiche Taps). LO = Kanalmitte.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "lib/dvbmod.h"
#include "lib/dtmb.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <signal.h>
#include <unistd.h>
#include <pthread.h>
#include <sched.h>

#define RING 8

static volatile sig_atomic_t stop_flag;
static void on_sig(int s) { (void)s; stop_flag = 1; }

static struct {
    dtmb_tx_t *tx; ts_input_t *ts; out_sink_t *out; dtmb_srrc_t srrc;
    int sps, flen, threads, affinity, rt_prio, quiet, profile;
    long long max_frames, frames_enc, frames_out, npk;
    int eof, file_input; long long flush_frames, flush_left;
    dtmb_job_t *job[RING]; int head, tail, done, err;
    pthread_mutex_t mx; pthread_cond_t cv_full, cv_empty;
    double t0, cpu_coder, cpu_out, render_s, filter_s, write_s;
    uint64_t samples;
    double sample_rate;
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

static int src_pkt(void *ctx, uint8_t *pkt)
{
    (void)ctx;
    if (!G.eof) {
        if (ts_input_packet(G.ts, pkt)) { G.npk++; return 1; }
        G.eof = 1; G.flush_left = G.file_input ? G.flush_frames : 0;
    }
    if (G.flush_left > 0) { pkt[0] = 0x47; pkt[1] = 0x1F; pkt[2] = 0xFF; pkt[3] = 0x10; memset(pkt + 4, 0xFF, 184); return 1; }
    return 0;
}

/* nächsten Rahmen codieren; 0 = Ende */
static int encode_one(dtmb_job_t *j)
{
    if (dtmb_tx_group_boundary(G.tx)) {
        if (stop_flag) return 0;
        if (G.max_frames && G.frames_enc >= G.max_frames) return 0;
        if (G.eof && G.flush_left <= 0) return 0;
    }
    if (!dtmb_tx_encode(G.tx, src_pkt, NULL, j)) return 0;
    if (G.eof && G.flush_left > 0) G.flush_left--;
    G.frames_enc++;
    return 1;
}

static int16_t *obuf1, *obuf2, *scratch;
static void status_line(int final);

static int render_write(const dtmb_job_t *j)
{
    double a = G.profile ? dvb_now() : 0;
    dtmb_tx_render(G.tx, j, scratch, obuf1);
    double b = G.profile ? dvb_now() : 0;
    const int16_t *o = obuf1; size_t n = (size_t)G.flen;
    if (G.sps == 2) { dtmb_srrc_run(&G.srrc, obuf1, n, obuf2); o = obuf2; n *= 2; }
    double c = G.profile ? dvb_now() : 0;
    if (out_write(G.out, o, n) < 0) return -1;
    if (G.profile) { double d = dvb_now(); G.render_s += b - a; G.filter_s += c - b; G.write_s += d - c; }
    G.samples += n; G.frames_out++;
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
        if (!encode_one(G.job[G.head])) break;
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
        if (render_write(G.job[G.tail]) < 0) {
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
    double el = dvb_now() - G.t0, sig = G.samples / G.sample_rate;
    if (!final) {
        fprintf(stderr, "{\"t\":%.1f,\"frames\":%lld,\"signal_s\":%.2f,\"rt_factor\":%.2f,\"cpu_coder\":%.2f,\"cpu_out\":%.2f,"
                "\"ts_in\":%llu,\"stuffed\":%llu,\"dropped\":%llu,\"fill\":%d}\n",
                el, G.frames_out, sig, sig / (el > 0 ? el : 1), G.cpu_coder / (el > 0 ? el : 1), G.cpu_out / (el > 0 ? el : 1),
                (unsigned long long)st.packets_in, (unsigned long long)st.nulls_stuffed, (unsigned long long)st.nulls_dropped, st.fill);
        return;
    }
    double pr[4]; dtmb_tx_profile(G.tx, pr);
    double nf = G.frames_out > 0 ? (double)G.frames_out : 1;
    fprintf(stderr, "{\"done\":true,\"frames\":%lld,\"packets\":%lld,\"samples\":%llu,\"signal_s\":%.3f,\"wall_s\":%.3f,\"rt_factor\":%.3f,"
            "\"cpu_coder_s\":%.3f,\"cpu_out_s\":%.3f,\"us_per_frame_coder\":%.1f,\"us_per_frame_out\":%.1f,\"frame_us\":%.1f,\"threads\":%d,"
            "\"stuffed\":%llu,\"dropped\":%llu",
            G.frames_out, G.npk, (unsigned long long)G.samples, sig, el, sig / (el > 0 ? el : 1), G.cpu_coder, G.cpu_out,
            G.cpu_coder / nf * 1e6, G.cpu_out / nf * 1e6, G.flen / DTMB_SYMBOL_RATE * 1e6, G.threads,
            (unsigned long long)st.nulls_stuffed, (unsigned long long)st.nulls_dropped);
    if (G.profile)
        fprintf(stderr, ",\"prof_us\":{\"scr_bch\":%.1f,\"ldpc\":%.1f,\"labels\":%.1f,\"ti\":%.1f,\"render\":%.1f,\"srrc\":%.1f,\"write\":%.1f}",
                pr[0] / nf * 1e6, pr[1] / nf * 1e6, pr[2] / nf * 1e6, pr[3] / nf * 1e6, G.render_s / nf * 1e6, G.filter_s / nf * 1e6, G.write_s / nf * 1e6);
    fprintf(stderr, "}\n");
}

static void usage(void)
{
    fprintf(stderr,
"dtmb_mod – DTMB-Modulator (GB 20600-2006, 8 MHz, Symbolrate 7,56 MS/s)\n"
"  -i SPEC          TS-Eingang: Datei | - | udp://[addr]:port | prbs[:N] | null   (Standard prbs)\n"
"  -o PATH          Ausgabe: Datei | - | /dev/null | iio: | iio:URI   IQ int16 LE   (Standard -)\n"
"  --carriers 3780|1  --const 4qamnr|4qam|16qam|32qam|64qam  --rate 0.4|0.6|0.8  --pn 420|595|945  --ti 240|720\n"
"                   (Standard 3780, 64qam, 0.6, 945, 720; 4QAM-NR und 32QAM nur 0.8)\n"
"  --pn-const       Rahmenkopf mit konstanter PN-Phase (Standard: Phase wechselt je Rahmen, PN420/945)\n"
"  --sps 1|2        1: 7,56 MS/s, SRRC im AD9361-TX-FIR (Standard bei -o iio:); 2: 15,12 MS/s, SRRC in Software\n"
"  --taps N         SRRC-Länge (Vielfaches von 16, Standard 128)   --kaiser BETA (Standard 3)\n"
"  --backoff dB     RMS unter Vollaussteuerung nach der SRRC-Formung (Standard 12)\n"
"  --frames N | --seconds S   Dauer   --loop   --realtime   --udp-buffer N   --quiet   --profile\n"
"  --threads 1|2    (Standard 2: Codierung | Rahmen/IDFT/Ausgabe)   --affinity   --rt\n"
"  --print-rates    eine JSON-Zeile (ts_bps, sample_rate, symbol_rate, …) auf stdout und Ende\n"
"  --fir-out DATEI  AD9361-FIR-Datei (SRRC, INT 2) schreiben und Ende   --selftest\n"
"  IIO: --lo HZ (= Kanalmitte) --atten DB --rfbw HZ (Standard 8e6) --iio-buf SAMPLES --iio-kbufs N --enable\n"
"       --fir DATEI (eigenes FIR statt der eingebauten SRRC)  --no-fir (kein FIR laden; nur mit --sps 2 sinnvoll)\n");
}

static int parse_qam(const char *s)
{
    static const char *n[5] = { "4qamnr", "4qam", "16qam", "32qam", "64qam" };
    for (int i = 0; i < 5; i++) if (!strcmp(s, n[i])) return i;
    if (!strcmp(s, "4qam-nr")) return 0;
    return -1;
}

int main(int argc, char **argv)
{
    dtmb_cfg_t c = { 3780, DTMB_64QAM, 1, 945, 720, 1 };
    int print_rates = 0, loop = 0, realtime = 0, udp_buffer = 2000, taps = 128, no_fir = 0, selftest = 0;
    double backoff = 12, seconds = 0, kaiser = 3.0;
    const char *in_spec = "prbs", *out_path = "-", *fir_out = NULL, *fir_user = NULL;
    out_params_t op; memset(&op, 0, sizeof op); op.atten_db = 89.75; op.kernel_buffers = 4;
    G.threads = 2; G.sps = 0;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
#define ARG(name) (!strcmp(a, name) && i + 1 < argc)
        if (ARG("-i")) in_spec = argv[++i];
        else if (ARG("-o")) out_path = argv[++i];
        else if (ARG("--carriers")) c.carriers = atoi(argv[++i]);
        else if (ARG("--const")) c.qam = parse_qam(argv[++i]);
        else if (ARG("--rate")) { const char *r = argv[++i]; c.rate = !strcmp(r, "0.4") ? 0 : !strcmp(r, "0.6") ? 1 : !strcmp(r, "0.8") ? 2 : -1; }
        else if (ARG("--pn")) c.pn = atoi(argv[++i]);
        else if (ARG("--ti")) c.ti = atoi(argv[++i]);
        else if (!strcmp(a, "--pn-const")) c.pn_rotate = 0;
        else if (ARG("--sps")) G.sps = atoi(argv[++i]);
        else if (ARG("--taps")) taps = atoi(argv[++i]);
        else if (ARG("--kaiser")) kaiser = atof(argv[++i]);
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
        else if (ARG("--fir-out")) fir_out = argv[++i];
        else if (!strcmp(a, "--selftest")) selftest = 1;
        else if (ARG("--lo")) op.lo_hz = atof(argv[++i]);
        else if (ARG("--atten")) op.atten_db = atof(argv[++i]);
        else if (ARG("--rfbw")) op.rf_bw_hz = atof(argv[++i]);
        else if (ARG("--iio-buf")) op.buf_samples = atoi(argv[++i]);
        else if (ARG("--iio-kbufs")) op.kernel_buffers = atoi(argv[++i]);
        else if (!strcmp(a, "--enable")) op.enable = 1;
        else if (ARG("--fir")) fir_user = argv[++i];
        else if (!strcmp(a, "--no-fir")) no_fir = 1;
        else { usage(); return 2; }
    }
    if (selftest) return dtmb_selftest(1) ? 1 : 0;
    char err[160];
    if (dtmb_cfg_check(&c, err, sizeof err)) { fprintf(stderr, "%s\n", err); return 2; }
    int is_iio = !strncmp(out_path, "iio:", 4);
    if (G.sps == 0) G.sps = is_iio ? 1 : 2;
    if (G.sps != 1 && G.sps != 2) { usage(); return 2; }
    if (G.threads != 1 && G.threads != 2) { usage(); return 2; }
    if (dtmb_srrc_init(&G.srrc, taps, 0.05, kaiser) < 0 || (taps > 128 && G.sps == 1)) {
        fprintf(stderr, "--taps: Vielfaches von 16, 16…128 (AD9361) bzw. …256 (--sps 2)\n"); return 2;
    }
    const double ts_bps = dtmb_ts_bps(&c);
    G.sample_rate = DTMB_SYMBOL_RATE * G.sps;
    const char *fir_mode = G.sps == 2 ? "software" : (is_iio && !no_fir) ? (fir_user ? "ad9361-user" : "ad9361-int2") : "none";
    if (print_rates) {
        printf("{\"ts_bps\":%.3f,\"sample_rate\":%.3f,\"symbol_rate\":%.3f,\"sps\":%d,\"bandwidth_hz\":8000000,\"frame_len\":%d,"
               "\"frames_per_superframe\":%d,\"si_index\":%d,\"carriers\":%d,\"fir\":\"%s\",\"srrc_taps\":%d}\n",
               ts_bps, G.sample_rate, DTMB_SYMBOL_RATE, G.sps, dtmb_frame_len(c.pn), dtmb_frames_per_superframe(c.pn),
               dtmb_si_index(&c), c.carriers, fir_mode, taps);
        return 0;
    }
    char *fir_txt = (char *)malloc(16384);
    dtmb_srrc_fir_text(&G.srrc, fir_txt, 16384);
    if (fir_out) {
        FILE *f = fopen(fir_out, "w");
        if (!f) { perror(fir_out); return 1; }
        fputs(fir_txt, f); fclose(f);
        fprintf(stderr, "{\"fir\":\"%s\",\"taps\":%d,\"kaiser\":%g,\"phase_gain\":[%.4f,%.4f],\"power_gain\":%.4f}\n",
                fir_out, taps, kaiser, G.srrc.phase_gain[0], G.srrc.phase_gain[1], G.srrc.power_gain);
        return 0;
    }

    /* Pegel: RMS nach der Formung = −backoff dBFS; die Symbolfolge wird um 1/√(Leistungsverstärkung des FIR) vorverzerrt */
    double rms = 32767.0 * pow(10.0, -backoff / 20.0) / sqrt(G.srrc.power_gain);
    G.tx = dtmb_tx_create(&c, rms);
    if (!G.tx) { fprintf(stderr, "Initialisierung fehlgeschlagen\n"); return 1; }
    dtmb_tx_set_profile(G.tx, G.profile);
    G.flen = dtmb_tx_frame_len(G.tx);
    if (seconds > 0) G.max_frames = (long long)ceil(seconds * DTMB_SYMBOL_RATE / G.flen);
    G.ts = ts_input_open(in_spec, loop, udp_buffer, 0);
    if (!G.ts) { fprintf(stderr, "Eingang %s nicht zu öffnen\n", in_spec); return 1; }
    G.file_input = strncmp(in_spec, "udp://", 6) && strncmp(in_spec, "prbs", 4) && strcmp(in_spec, "null") && !loop;
    /* Nachlauf bei Dateiende: Zeitverschachteler leeren (52·51·M Symbole bzw. Bits = 170/510 Rahmen) + 4 (Empfänger braucht den Folgekopf) */
    G.flush_frames = (long long)(52 * 51 * c.ti / DTMB_DATA) + 4;

    /* AD9361-FIR (SRRC, INT 2) als temporäre Datei */
    char firpath[64] = "";
    op.path = out_path; op.sample_rate = G.sample_rate;
    if (op.rf_bw_hz <= 0) op.rf_bw_hz = 8e6;
    if (is_iio && G.sps == 1 && !no_fir) {
        if (fir_user) op.fir_file = fir_user;
        else {
            strcpy(firpath, "/tmp/dtmb_fir_XXXXXX");
            int fd = mkstemp(firpath);
            if (fd < 0 || write(fd, fir_txt, strlen(fir_txt)) < 0) { fprintf(stderr, "FIR-Datei nicht schreibbar\n"); return 1; }
            close(fd); op.fir_file = firpath;
        }
    }
    if (op.buf_samples <= 0) op.buf_samples = G.flen * G.sps * 16;
    G.out = out_open(&op);
    if (firpath[0]) unlink(firpath);
    if (!G.out) { fprintf(stderr, "Ausgabe %s nicht zu öffnen\n", out_path); return 1; }
    if (realtime) out_set_realtime(G.out, 1);
    if (!G.quiet)
        fprintf(stderr, "{\"standard\":\"DTMB GB 20600-2006\",\"carriers\":%d,\"const\":\"%s\",\"rate\":\"%s\",\"pn\":%d,\"pn_rotate\":%d,\"ti\":%d,"
                "\"si_index\":%d,\"symbol_rate\":%.0f,\"sps\":%d,\"sample_rate\":%.0f,\"ts_bps\":%.3f,\"frame_len\":%d,\"fir\":\"%s\","
                "\"taps\":%d,\"kaiser\":%g,\"backoff_db\":%g,\"ldpc\":\"%s\",\"fft\":\"%s\"}\n",
                c.carriers, dtmb_qam_name(c.qam), dtmb_rate_name[c.rate], c.pn, c.pn_rotate, c.ti, dtmb_si_index(&c),
                DTMB_SYMBOL_RATE, G.sps, G.sample_rate, ts_bps, G.flen, fir_mode, taps, kaiser, backoff,
                dtmb_ldpc_backend(), dtmb_fft_backend());
    signal(SIGINT, on_sig); signal(SIGTERM, on_sig);

    obuf1 = (int16_t *)malloc(sizeof(int16_t) * 2 * DTMB_MAX_FRAME);
    obuf2 = (int16_t *)malloc(sizeof(int16_t) * 4 * DTMB_MAX_FRAME);
    scratch = (int16_t *)malloc(sizeof(int16_t) * dtmb_tx_scratch_len(G.tx));
    for (int i = 0; i < RING; i++) G.job[i] = dtmb_job_alloc(G.tx);
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
        while (encode_one(G.job[0])) {
            if (render_write(G.job[0]) < 0) { fprintf(stderr, "Schreibfehler\n"); break; }
            G.cpu_coder = dvb_cpu_time() - c0;
            if (!G.quiet) { double nw = dvb_now(); if (nw - last >= 1.0) { last = nw; status_line(0); } }
        }
        G.cpu_coder = dvb_cpu_time() - c0;
    }
    status_line(1);
    out_close(G.out); ts_input_close(G.ts);
    for (int i = 0; i < RING; i++) dtmb_job_free(G.job[i]);
    dtmb_tx_free(G.tx); dtmb_srrc_free(&G.srrc);
    free(obuf1); free(obuf2); free(scratch); free(fir_txt);
    return 0;
}
