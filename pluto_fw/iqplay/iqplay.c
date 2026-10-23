/*
 * iqplay – IQ-Daten aus Datei, /dev/zero, Testton oder UDP über den lokalen
 * IIO-Kontext in den DAC des Pluto spielen (zyklisch oder fortlaufend).
 * Dient als Platzhalter/Test für die On-Device-Modulatoren und zur Messung
 * der maximalen lokalen DMA-Rate.
 *
 *   iqplay -r 9142857 -f /tmp/test.iq          Datei (int16 I/Q verschachtelt) fortlaufend (Schleife)
 *   iqplay -r 9142857 -f /tmp/test.iq -c       Datei einmalig in einen zyklischen Puffer laden
 *   iqplay -r 14000000 -z -t 60                 Nullen 60 s lang (DMA-Durchsatztest)
 *   iqplay -r 2048000 -s 100000 -t 30           Sinus 100 kHz, 30 s
 *   iqplay -r 9142857 -u 5000                   UDP-Port 5000 (rohes int16-IQ) → DAC
 *   iqplay -r 8000000 -N -c -f /tmp/pal.iq -l 593500000 -w 8000000   PAL-Schleife: ganze Datei als ein
 *                                               zyklischer Puffer, FIR aus, Rate direkt (wie pal_ctl.sh)
 *
 * Optionen: -l LO_Hz  -g Dämpfung_dB (Start immer -89.75 dB, dann -g)  -b Blockgröße (Samples)
 *           -n Anzahl Kernelpuffer  -w RF-Bandbreite_Hz  -q leise  -F fir.txt (filter_fir_config)
 *           -N FIR aus und Rate direkt setzen (statt ad9361_set_bb_rate; Rate muss ohne FIR gültig sein)
 *           -c mit -f: ganze Datei = ein zyklischer Puffer (Blockgröße = Dateigröße/4, max. 64 MiB)
 * Underflow: Bit 0 in Register 0x88 des DDS-Cores (sticky, W1C) wird jede Sekunde gelesen/gezählt.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <errno.h>
#include <signal.h>
#include <math.h>
#include <time.h>
#include <sched.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <iio.h>
#include <ad9361.h>

static volatile int stop;
static void on_sig(int s) { (void)s; stop = 1; }
static double now_s(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return ts.tv_sec + ts.tv_nsec * 1e-9; }

static void usage(void)
{
	fprintf(stderr, "iqplay -r RATE [-f FILE | -z | -s TONE_HZ | -u UDP_PORT] [-c] [-t SEC] [-l LO] [-g ATT_DB] [-b BLOCK] [-n NBUF] [-w BW] [-F fir.txt | -N] [-q]\n");
	exit(2);
}

static int unf_read_clear(struct iio_device *dds)
{
	uint32_t v = 0;
	if (iio_device_reg_read(dds, 0x88, &v) < 0) return -1;
	if (v & 1) iio_device_reg_write(dds, 0x88, 1);
	return v & 1;
}

int main(int argc, char **argv)
{
	const char *file = NULL, *firfile = NULL; int zeros = 0, cyclic = 0, quiet = 0, udp_port = 0, nbuf = 4, opt, nofir = 0;
	double rate = 0, tone = 0, dur = 0, lo = 0, att = 89.75, bw = 0; size_t block = 0;
	struct iio_context *ctx; struct iio_device *phy, *dds; struct iio_channel *tx0, *tx1, *txch, *txlo; struct iio_buffer *buf;
	FILE *fp = NULL; int sock = -1; long long ll; double t0, tlast; unsigned long long total = 0, blocks = 0; long unf = 0; int rc;

	while ((opt = getopt(argc, argv, "r:f:zs:u:ct:l:g:b:n:w:F:qNh")) != -1) switch (opt) {
		case 'r': rate = atof(optarg); break; case 'f': file = optarg; break; case 'z': zeros = 1; break;
		case 's': tone = atof(optarg); break; case 'u': udp_port = atoi(optarg); break; case 'c': cyclic = 1; break;
		case 't': dur = atof(optarg); break; case 'l': lo = atof(optarg); break; case 'g': att = atof(optarg); break;
		case 'b': block = (size_t)atol(optarg); break; case 'n': nbuf = atoi(optarg); break; case 'w': bw = atof(optarg); break;
		case 'F': firfile = optarg; break; case 'q': quiet = 1; break; case 'N': nofir = 1; break; default: usage();
	}
	if (rate <= 0 || (!file && !zeros && tone <= 0 && !udp_port)) usage();
	if (att < 0) att = 0; if (att > 89.75) att = 89.75;
	if (!block && cyclic && file && strcmp(file, "-")) {	/* ganze Datei als ein zyklischer Puffer */
		FILE *t = fopen(file, "rb"); long n = -1;
		if (t) { fseek(t, 0, SEEK_END); n = ftell(t); fclose(t); }
		if (n < 16 || n % 4 || n > (64L << 20)) { fprintf(stderr, "%s: Größe %ld ungeeignet (Vielfaches von 4, max. 64 MiB)\n", file, n); return 1; }
		block = (size_t)(n / 4); nbuf = 1;
	}
	if (!block) block = (size_t)(rate / 8);	/* 125 ms je Block */
	if (block < 4096 && !cyclic) block = 4096;
	signal(SIGINT, on_sig); signal(SIGTERM, on_sig);

	ctx = iio_create_local_context();
	if (!ctx) { fprintf(stderr, "kein lokaler IIO-Kontext\n"); return 1; }
	phy = iio_context_find_device(ctx, "ad9361-phy"); dds = iio_context_find_device(ctx, "cf-ad9361-dds-core-lpc");
	if (!phy || !dds) { fprintf(stderr, "ad9361-phy/dds nicht gefunden\n"); return 1; }
	txch = iio_device_find_channel(phy, "voltage0", true); txlo = iio_device_find_channel(phy, "altvoltage1", true);
	/* Sicherheit: zuerst stumm */
	iio_channel_attr_write_double(txch, "hardwaregain", -89.75);
	if (lo > 0) iio_channel_attr_write_longlong(txlo, "frequency", (long long)lo);
	if (firfile) {
		FILE *ff = fopen(firfile, "r"); char *fc; long n;
		if (!ff) { perror(firfile); return 1; }
		fseek(ff, 0, SEEK_END); n = ftell(ff); fseek(ff, 0, SEEK_SET); fc = malloc(n + 1); n = fread(fc, 1, n, ff); fclose(ff);
		rc = iio_device_attr_write_raw(phy, "filter_fir_config", fc, n); free(fc);
		if (rc < 0) { fprintf(stderr, "filter_fir_config: %d\n", rc); return 1; }
		iio_device_attr_write(phy, "in_out_voltage_filter_fir_en", "1");
		rc = iio_channel_attr_write_longlong(txch, "sampling_frequency", (long long)rate);
	} else if (nofir) {
		iio_device_attr_write(phy, "in_out_voltage_filter_fir_en", "0");
		rc = iio_channel_attr_write_longlong(txch, "sampling_frequency", (long long)rate);
	} else {
		rc = ad9361_set_bb_rate(phy, (unsigned long)rate);
	}
	if (rc < 0) { fprintf(stderr, "Rate %.0f: Fehler %d (%s)\n", rate, rc, strerror(-rc)); return 1; }
	iio_channel_attr_read_longlong(txch, "sampling_frequency", &ll);
	if (bw > 0) iio_channel_attr_write_longlong(txch, "rf_bandwidth", (long long)bw);
	if (!quiet) { char fe[16] = "?"; iio_device_attr_read(phy, "in_out_voltage_filter_fir_en", fe, sizeof fe); long long bwr = 0; iio_channel_attr_read_longlong(txch, "rf_bandwidth", &bwr);
		printf("TX-Rate gesetzt: %lld S/s (gewünscht %.0f), FIR=%s, RF-BW %lld Hz, Block %zu Samples, %d Kernelpuffer\n", ll, rate, fe, bwr, block, nbuf); }

	tx0 = iio_device_find_channel(dds, "voltage0", true); tx1 = iio_device_find_channel(dds, "voltage1", true);
	iio_channel_enable(tx0); iio_channel_enable(tx1);
	iio_device_set_kernel_buffers_count(dds, nbuf);
	buf = iio_device_create_buffer(dds, block, cyclic);
	if (!buf) { fprintf(stderr, "Puffer (%zu Samples): %s\n", block, strerror(errno)); return 1; }

	if (file) { fp = strcmp(file, "-") ? fopen(file, "rb") : stdin; if (!fp) { perror(file); return 1; } }
	if (udp_port) {
		struct sockaddr_in a; int rb = 8 << 20;
		sock = socket(AF_INET, SOCK_DGRAM, 0); setsockopt(sock, SOL_SOCKET, SO_RCVBUF, &rb, sizeof rb);
		memset(&a, 0, sizeof a); a.sin_family = AF_INET; a.sin_port = htons(udp_port); a.sin_addr.s_addr = INADDR_ANY;
		if (bind(sock, (struct sockaddr *)&a, sizeof a) < 0) { perror("bind"); return 1; }
	}

	unf_read_clear(dds);
	t0 = tlast = now_s();
	{
		ptrdiff_t step = iio_buffer_step(buf); char *start = iio_buffer_first(buf, tx0), *end = iio_buffer_end(buf);
		size_t bytes = (size_t)(end - start); double ph = 0, dph = 2 * M_PI * tone / (double)ll; int armed = 0;
		(void)step;
		while (!stop) {
			if (zeros) memset(start, 0, bytes);
			else if (tone > 0) { int16_t *p = (int16_t *)start; size_t i; for (i = 0; i < block; i++) { p[2*i] = (int16_t)(0.5 * 32767 * cos(ph)); p[2*i+1] = (int16_t)(0.5 * 32767 * sin(ph)); ph += dph; if (ph > 2*M_PI) ph -= 2*M_PI; } }
			else if (fp) {
				size_t got = 0;
				while (got < bytes) { size_t r = fread(start + got, 1, bytes - got, fp); if (r == 0) { if (fp == stdin || cyclic) break; rewind(fp); continue; } got += r; }
				if (got < bytes) { memset(start + got, 0, bytes - got); if (got == 0 && fp == stdin) break; }
			} else if (sock >= 0) {
				size_t got = 0;
				while (got < bytes && !stop) { ssize_t r = recv(sock, start + got, bytes - got, 0); if (r <= 0) continue; got += r; }
			}
			rc = (int)iio_buffer_push(buf);
			if (rc < 0) { fprintf(stderr, "push: %s\n", strerror(-rc)); break; }
			total += block; blocks++;
			if (!armed) { armed = 1; iio_channel_attr_write_double(txch, "hardwaregain", -att); unf_read_clear(dds); if (!quiet) printf("sendet, Dämpfung %.2f dB\n", att); }
			if (cyclic) { if (!quiet) printf("zyklischer Puffer läuft (%zu Samples)\n", block); while (!stop && (dur <= 0 || now_s() - t0 < dur)) { sleep(1); int u = unf_read_clear(dds); if (u > 0) unf++; if (!quiet) { printf("t=%.0fs underflow_bit=%d (gesamt %ld)\n", now_s() - t0, u, unf); fflush(stdout); } } break; }
			if (now_s() - tlast >= 1.0) {
				int u = unf_read_clear(dds); if (u > 0) unf++;
				if (!quiet) { printf("t=%.0fs %.3f MS/s gesendet=%llu underflow_bit=%d (gesamt %ld)\n", now_s() - t0, total / (now_s() - t0) / 1e6, total, u, unf); fflush(stdout); }
				tlast = now_s();
			}
			if (dur > 0 && now_s() - t0 >= dur) break;
		}
	}
	iio_channel_attr_write_double(txch, "hardwaregain", -89.75);
	{ int u = unf_read_clear(dds); if (u > 0) unf++; }
	printf("ENDE: %.1f s, %llu Samples (%.4f MS/s mittel), %llu Blöcke, Underflows gesamt=%ld\n", now_s() - t0, total, total / (now_s() - t0) / 1e6, blocks, unf);
	iio_buffer_destroy(buf); iio_context_destroy(ctx);
	return unf ? 3 : 0;
}
