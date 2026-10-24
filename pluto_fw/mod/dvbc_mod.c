/*
 * dvbc_mod – Kabel-QAM-Modulator für den ADALM-Pluto: ITU-T J.83 Annex A (DVB-C, ETSI EN 300 429 V1.2.1),
 * Annex B (US-Kabel, SCTE 07) und Annex C (Japan).
 * Annex A/C: TS → Energieverwürfelung → RS(204,188) → Faltungsinterleaver I=12 → Byte→m-Tupel → Differenzcodierung
 *   → QAM (16…256) → (a) RRC (A: α=0,15, C: α=0,13) bei 2 Samples/Symbol oder (b) 1 Sample/Symbol (RRC im AD9361-TX-FIR,
 *   Koeffizienten aus rrc_fir_gen).
 * Annex B (lib/j83b.c): MPEG-Prüfsumme → RS(128,122) GF(128) → Faltungsinterleaver (Level 2) → Randomizer → FEC-Rahmen
 *   → Trellis-codierte 64-QAM (14/15) bzw. 256-QAM (19/20) → RRC α = 0,18 bzw. 0,12.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "lib/dvbmod.h"
#include "lib/j83b.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <signal.h>
#include <pthread.h>
#include <semaphore.h>

static volatile sig_atomic_t stop_flag;
static void on_sig(int s) { (void)s; stop_flag = 1; }

/* Annex B mit zwei Threads: Hauptthread = TS-Eingang, Prüfsumme, RS, Interleaver, Randomizer (j83b_front);
   Worker = Trellis, Mapper, RRC, Ausgabe (j83b_back). Übergabe in Fächern zu höchstens 16 RS-Blöcken. */
#define NSLOT 8
#define SLOT_BLKS 16
typedef struct { int nb, quit; uint8_t b[SLOT_BLKS * J83B_BLK]; } slot_t;
static struct {
    slot_t s[NSLOT]; sem_t full, empty;
    j83b_enc_t *e; rrc_t *rrc; int sps; out_sink_t *out; FILE *fdump;
    volatile uint64_t samples; volatile int err; double busy;
} Q;

static void *bworker(void *arg)
{
    (void)arg;
    static int16_t sym[2 * SLOT_BLKS * J83B_MAX_SYM_PER_BLK], smp[4 * SLOT_BLKS * J83B_MAX_SYM_PER_BLK];
    static uint8_t lab[SLOT_BLKS * J83B_MAX_SYM_PER_BLK];
    for (int k = 0;; k = (k + 1) % NSLOT) {
        sem_wait(&Q.full);
        slot_t *s = &Q.s[k];
        if (s->quit) break;
        double t = dvb_now();
        size_t ns = 0;
        for (int b = 0; b < s->nb; b++) ns += j83b_back(Q.e, s->b + b * J83B_BLK, sym + 2 * ns, Q.fdump ? lab + ns : NULL);
        if (Q.fdump && ns) fwrite(lab, 1, ns, Q.fdump);
        const int16_t *o = sym; size_t n = ns;
        if (Q.sps == 2) { n = rrc_run(Q.rrc, sym, ns, smp); o = smp; }
        Q.busy += dvb_now() - t;
        if (n && !Q.err && out_write(Q.out, o, n) < 0) Q.err = 1;
        Q.samples += n;
        sem_post(&Q.empty);
    }
    return NULL;
}

static void usage(void)
{
    fprintf(stderr,
"dvbc_mod – Kabel-QAM-Modulator (ITU-T J.83 Annex A = DVB-C EN 300 429, Annex B = SCTE 07, Annex C = Japan)\n"
"  -i SPEC        TS-Eingang: Datei | - | udp://[addr]:port | prbs[:N] | null   (Standard prbs)\n"
"  -o PATH        Ausgabe: Datei | - | iio: | iio:URI                              (Standard -)\n"
"  --annex a|b|c  a (Standard): DVB-C, RRC 0,15 | c: wie a, RRC 0,13, Standard-SR 5274000 | b: J.83B, nur 64/256,\n"
"                 SR 5056941 (64) / 5360537 (256), RRC 0,18 / 0,12\n"
"  --qam 16|32|64|128|256   --sr SYMBOLRATE (Baud, 1e6..7e6, Standard A 6900000, C 5274000, B normgemäß)\n"
"  --sps 1|2      Samples je Symbol: 2 = RRC im Programm (Ausgaberate 2·SR), 1 = RRC im AD9361-FIR\n"
"  --taps N       RRC-Länge bei --sps 2 (Standard 128)   --backoff dB  (Standard 10)   --rolloff A  (überschreibt α)\n"
"  --interleave CW  Annex B: Steuerwort 0..14 (Tabelle B.2; Standard 6 = I 128/J 4; 1 = I 128/J 1, Level 1)\n"
"  --threads 1|2  Annex B: 2 (Standard) = Coder auf zwei Kerne verteilt (Ausgabe bitgleich zu 1)\n"
"  --packets N | --seconds S   Dauer      --loop   Datei wiederholen    --realtime   stdout bremsen\n"
"  --udp-buffer N  UDP-Ring in Paketen    --quiet    --print-rates  JSON (ts_bps, symbol_rate, sample_rate) und Ende\n"
"  --dump-sym DATEI  Annex B: QAM-Labels (uint8, C5..C0 bzw. C7..C0) zusätzlich schreiben (Test)\n"
"  IIO: --lo HZ --atten DB --rfbw HZ --iio-buf SAMPLES --iio-kbufs N --cyclic --enable --fir DATEI\n");
}

int main(int argc, char **argv)
{
    int qam = 256, sps = 2, taps = 128, loop = 0, realtime = 0, quiet = 0, udp_buffer = 2000, print_rates = 0, cw = 6, threads = 2;
    double sr = 0, backoff = 10, max_seconds = 0, rolloff = 0; long long max_packets = 0;
    char annex = 'a';
    const char *in_spec = "prbs", *out_path = "-", *dump_sym = NULL;
    out_params_t op; memset(&op, 0, sizeof op); op.atten_db = 89.75; op.kernel_buffers = 4;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
#define ARG(name) (!strcmp(a, name) && i + 1 < argc)
        if (ARG("-i")) in_spec = argv[++i];
        else if (ARG("-o")) out_path = argv[++i];
        else if (ARG("--annex")) { const char *x = argv[++i]; annex = (char)(x[0] | 0x20); if (x[1] || (annex != 'a' && annex != 'b' && annex != 'c')) { usage(); return 2; } }
        else if (ARG("--qam")) qam = atoi(argv[++i]);
        else if (ARG("--sr")) sr = atof(argv[++i]);
        else if (ARG("--sps")) sps = atoi(argv[++i]);
        else if (ARG("--taps")) taps = atoi(argv[++i]);
        else if (ARG("--backoff")) backoff = atof(argv[++i]);
        else if (ARG("--rolloff")) rolloff = atof(argv[++i]);
        else if (ARG("--interleave")) cw = atoi(argv[++i]);
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
        else if (!strcmp(a, "--cyclic")) op.cyclic = 1;
        else if (!strcmp(a, "--enable")) op.enable = 1;
        else if (ARG("--fir")) op.fir_file = argv[++i];
        else { usage(); return 2; }
    }
    int m; switch (qam) { case 16: m = 4; break; case 32: m = 5; break; case 64: m = 6; break; case 128: m = 7; break; case 256: m = 8; break; default: usage(); return 2; }
    if (sps != 1 && sps != 2) { usage(); return 2; }
    if (annex == 'b') {
        if (qam != 64 && qam != 256) { fprintf(stderr, "Annex B: nur --qam 64 oder 256\n"); return 2; }
        if (!j83b_cw_valid(cw, NULL, NULL)) { fprintf(stderr, "Annex B: --interleave 0..10, 12, 14 (11/13/15 reserviert)\n"); return 2; }
    }
    if (sr <= 0) sr = annex == 'a' ? 6.9e6 : annex == 'c' ? 5.274e6 : (qam == 64 ? J83B_SR64 : J83B_SR256);
    if (annex == 'b' && fabs(sr / (qam == 64 ? J83B_SR64 : J83B_SR256) - 1) > 1e-4)
        fprintf(stderr, "Warnung: Annex B %d-QAM normgemäß %.0f Bd (±5 ppm), gesetzt %.0f Bd\n", qam, qam == 64 ? J83B_SR64 : J83B_SR256, sr);
    if (threads != 1 && threads != 2) { usage(); return 2; }
    double alpha = rolloff > 0 ? rolloff : annex == 'a' ? 0.15 : annex == 'c' ? 0.13 : (qam == 64 ? 0.18 : 0.12);
    double bitrate = annex == 'b' ? j83b_ts_rate(qam, sr) : sr * m * 188.0 / 204.0;
    double sample_rate = sr * sps;
    if (print_rates) {
        printf("{\"ts_bps\":%.3f,\"symbol_rate\":%.3f,\"sample_rate\":%.3f,\"sps\":%d,\"annex\":\"%c\",\"qam\":%d,\"rolloff\":%g}\n",
               bitrate, sr, sample_rate, sps, annex, qam, alpha);
        return 0;
    }
    /* Pegel: Symbol-RMS = A; nach RRC (Verstärkung g je Phase, Energie Σh²/sps) RMS_out ≈ A·sqrt(Σh²/sps)·(32767/max) */
    double rms_target = 32767.0 * pow(10.0, -backoff / 20.0);
    rrc_t rrc; double gain_rrc = 1.0;
    if (sps == 2) {
        if (rrc_init(&rrc, alpha, 2, taps) < 0) { fprintf(stderr, "taps muss gerade sein\n"); return 1; }
        double e = 0; for (int i = 0; i < taps; i++) e += (double)rrc.taps[i] * rrc.taps[i];
        gain_rrc = sqrt(e / sps) / 32768.0;
    }
    int A = (int)lrint(rms_target / gain_rrc);
    /* Spitzenwert der Konstellation darf int16 nicht sprengen: max |I| = (sqrt(M)-1)/RMS·A */
    double peak = ((qam == 32) ? 5 : (qam == 128) ? 11 : (sqrt((double)qam) - 1)) / dvbc_map_rms(qam);
    if (A * peak > 32000) A = (int)(32000 / peak);

    dvb_scrambler_t scr; dvb_convint_t ci; dvbc_map_t map;
    static j83b_enc_t jb;
    if (annex == 'b') {
        if (j83b_init(&jb, qam, cw, A) < 0) { fprintf(stderr, "J.83B: Initialisierung fehlgeschlagen\n"); return 1; }
    } else {
        dvb_rs_init(); dvb_scrambler_init(&scr); dvb_convint_init(&ci);
        {   /* Interleaver-Historie mit verwürfelten Nullpaketen vorbelegen (keine Nullfolgen am Anfang) */
            dvb_scrambler_t s2; dvb_scrambler_init(&s2); uint8_t p[RS_PKT], o[RS_PKT];
            for (int i = 0; i < 11; i++) { memcpy(p, "Gÿ", 4); /* Bytes wie bisher (UTF-8-ÿ statt 0xff/0x10, Ausgabe bleibt bitgleich) */ memset(p + 4, 0xff, 184); dvb_scramble_packet(&s2, p); dvb_rs_encode(p, p); dvb_convint_packet(&ci, p, o); }
        }
        dvbc_map_init(&map, qam, A);
    }
    ts_input_t *ts = ts_input_open(in_spec, loop, udp_buffer, 0);
    if (!ts) { fprintf(stderr, "Eingang %s nicht zu öffnen\n", in_spec); return 1; }
    FILE *fdump = NULL;
    if (dump_sym && !(fdump = fopen(dump_sym, "wb"))) { fprintf(stderr, "%s nicht zu öffnen\n", dump_sym); return 1; }
    op.path = out_path; op.sample_rate = sample_rate;
    if (op.rf_bw_hz <= 0) op.rf_bw_hz = sr * 1.15;
    if (op.buf_samples <= 0) op.buf_samples = 65536;
    out_sink_t *out = out_open(&op);
    if (!out) { fprintf(stderr, "Ausgabe %s nicht zu öffnen\n", out_path); return 1; }
    if (realtime) out_set_realtime(out, 1);
    if (!quiet) {
        if (annex == 'a')
            fprintf(stderr, "{\"qam\":%d,\"symbol_rate\":%.0f,\"sps\":%d,\"sample_rate\":%.0f,\"bitrate_bps\":%.3f,\"A\":%d,\"backoff_db\":%g,"
                    "\"symbols_per_8pkt\":%d,\"samples_per_8pkt\":%d}\n", qam, sr, sps, sample_rate, bitrate, A, backoff, 8 * 204 * 8 / m, 8 * 204 * 8 / m * sps);
        else if (annex == 'c')
            fprintf(stderr, "{\"annex\":\"c\",\"qam\":%d,\"symbol_rate\":%.0f,\"sps\":%d,\"sample_rate\":%.0f,\"bitrate_bps\":%.3f,\"rolloff\":%g,\"A\":%d,\"backoff_db\":%g}\n",
                    qam, sr, sps, sample_rate, bitrate, alpha, A, backoff);
        else
            fprintf(stderr, "{\"annex\":\"b\",\"qam\":%d,\"symbol_rate\":%.0f,\"sps\":%d,\"sample_rate\":%.0f,\"bitrate_bps\":%.3f,\"rolloff\":%g,"
                    "\"interleave_cw\":%d,\"I\":%d,\"J\":%d,\"A\":%d,\"backoff_db\":%g}\n",
                    qam, sr, sps, sample_rate, bitrate, alpha, cw, jb.I, jb.J, A, backoff);
    }
    signal(SIGINT, on_sig); signal(SIGTERM, on_sig);
    uint8_t pkt[RS_PKT], il[RS_PKT];
    static int16_t sym[2 * J83B_MAX_SYM_PER_PKT], smp[2 * J83B_MAX_SYM_PER_PKT * 2];
    static uint8_t lab[J83B_MAX_SYM_PER_PKT];
    long long npk = 0; double t0 = dvb_now(), last = t0, cpu0 = dvb_cpu_time();
    uint64_t total_samples = 0;
    const int grp = annex == 'b' ? 1 : 8;            /* Annex A/C: nur ganze 8er-Gruppen (Sync-Inversion) */
    pthread_t wt;
    int threaded = annex == 'b' && threads == 2;
    if (threaded) {
        Q.e = &jb; Q.rrc = &rrc; Q.sps = sps; Q.out = out; Q.fdump = fdump;
        sem_init(&Q.full, 0, 0); sem_init(&Q.empty, 0, NSLOT);
        if (pthread_create(&wt, NULL, bworker, NULL)) { fprintf(stderr, "pthread_create fehlgeschlagen\n"); threaded = 0; }
    }
    if (threaded) {
        int k = 0; slot_t *cur = NULL;
        while (!stop_flag && !Q.err) {
            if (max_packets && npk >= max_packets) break;
            if (max_seconds > 0 && npk * 1504.0 / bitrate >= max_seconds) break;
            if (!ts_input_packet(ts, pkt)) break;
            if (!cur) { sem_wait(&Q.empty); cur = &Q.s[k]; cur->nb = 0; cur->quit = 0; }
            cur->nb += j83b_front(&jb, pkt, cur->b + cur->nb * J83B_BLK);
            npk++;
            if (cur->nb > SLOT_BLKS - 2) { sem_post(&Q.full); k = (k + 1) % NSLOT; cur = NULL; }
            if (!quiet && (npk & 1023) == 0) {
                double now = dvb_now();
                if (now - last >= 1.0) {
                    ts_stats_t st; ts_input_stats(ts, &st); last = now; total_samples = Q.samples;
                    fprintf(stderr, "{\"t\":%.1f,\"packets\":%lld,\"signal_s\":%.2f,\"rt_factor\":%.2f,\"cpu\":%.2f,\"cpu_worker\":%.2f,\"ts_in\":%llu,\"stuffed\":%llu,\"dropped\":%llu,\"fill\":%d}\n",
                            now - t0, npk, total_samples / sample_rate, total_samples / sample_rate / (now - t0), (dvb_cpu_time() - cpu0) / (now - t0), Q.busy / (now - t0),
                            (unsigned long long)st.packets_in, (unsigned long long)st.nulls_stuffed, (unsigned long long)st.nulls_dropped, st.fill);
                }
            }
        }
        if (cur) { sem_post(&Q.full); k = (k + 1) % NSLOT; }
        sem_wait(&Q.empty); Q.s[k].nb = 0; Q.s[k].quit = 1; sem_post(&Q.full);
        pthread_join(wt, NULL);
        total_samples = Q.samples;
        if (Q.err) fprintf(stderr, "Schreibfehler\n");
    }
    while (!threaded && !stop_flag) {
        if (max_packets && npk >= max_packets) break;
        if (max_seconds > 0 && (double)total_samples / sample_rate >= max_seconds && (npk % grp) == 0) break;
        if (!ts_input_packet(ts, pkt)) {
            if (npk % grp == 0) break;
            memcpy(pkt, "\x47\x1f\xff\x10", 4); memset(pkt + 4, 0xff, 184);
        }
        size_t ns;
        if (annex == 'b') {
            ns = j83b_packet(&jb, pkt, sym, fdump ? lab : NULL);
            if (fdump && ns) fwrite(lab, 1, ns, fdump);
        } else {
            dvb_scramble_packet(&scr, pkt); dvb_rs_encode(pkt, pkt); dvb_convint_packet(&ci, pkt, il);
            ns = dvbc_map_bytes(&map, il, RS_PKT, sym);
        }
        const int16_t *o = sym; size_t n = ns;
        if (sps == 2) { n = rrc_run(&rrc, sym, ns, smp); o = smp; }
        if (n && out_write(out, o, n) < 0) { fprintf(stderr, "Schreibfehler\n"); break; }
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
    if (threaded)
        fprintf(stderr, "{\"done\":true,\"packets\":%lld,\"samples\":%llu,\"signal_s\":%.3f,\"wall_s\":%.3f,\"rt_factor\":%.3f,\"cpu_s\":%.3f,\"worker_s\":%.3f,\"threads\":2}\n",
                npk, (unsigned long long)total_samples, total_samples / sample_rate, el, total_samples / sample_rate / (el > 0 ? el : 1), dvb_cpu_time() - cpu0, Q.busy);
    else
        fprintf(stderr, "{\"done\":true,\"packets\":%lld,\"samples\":%llu,\"signal_s\":%.3f,\"wall_s\":%.3f,\"rt_factor\":%.3f,\"cpu_s\":%.3f}\n",
                npk, (unsigned long long)total_samples, total_samples / sample_rate, el, total_samples / sample_rate / (el > 0 ? el : 1), dvb_cpu_time() - cpu0);
    if (fdump) fclose(fdump);
    out_close(out); ts_input_close(ts); if (sps == 2) rrc_free(&rrc);
    if (annex == 'b') j83b_free(&jb);
    return 0;
}
