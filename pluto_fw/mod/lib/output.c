/*
 * output.c – IQ-Ausgabe: Datei/stdout (int16 interleaved, blockierend) oder lokaler IIO-Kontext
 * (libiio, cf-ad9361-dds-core-lpc) hinter HAVE_LIBIIO. Der Pluto startet stumm (−89,75 dB), die
 * Dämpfung wird nur gesetzt, wenn --enable gegeben ist.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "dvbmod.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <time.h>

#ifdef HAVE_LIBIIO
#include <iio.h>
#endif

struct out_sink {
    int kind;                 /* 0 Datei, 1 IIO */
    int fd;
    uint64_t samples, underflows;
    double rate;
    int realtime; double t0;  /* optional Echtzeit-Bremse für Datei/stdout */
#ifdef HAVE_LIBIIO
    struct iio_context *ctx; struct iio_device *tx, *phy; struct iio_buffer *buf;
    struct iio_channel *ch_i, *ch_q;
    int buf_samples, cyclic, pushed_cyclic;
    int16_t *fill; int fill_n;
#endif
};

static int write_all(int fd, const void *p, size_t n)
{
    const uint8_t *b = (const uint8_t *)p;
    while (n) {
        ssize_t w = write(fd, b, n);
        if (w < 0) { if (errno == EINTR) continue; return -1; }
        b += w; n -= (size_t)w;
    }
    return 0;
}

#ifdef HAVE_LIBIIO
static int phy_write(struct iio_device *phy, const char *ch, int output, const char *attr, const char *val)
{
    struct iio_channel *c = iio_device_find_channel(phy, ch, output);
    if (!c) return -1;
    return (int)iio_channel_attr_write(c, attr, val);
}
static int phy_write_ll(struct iio_device *phy, const char *ch, int output, const char *attr, long long v)
{
    struct iio_channel *c = iio_device_find_channel(phy, ch, output);
    if (!c) return -1;
    return (int)iio_channel_attr_write_longlong(c, attr, v);
}

static int iio_open(out_sink_t *o, const out_params_t *p)
{
    const char *uri = p->path + 4;
    o->ctx = (*uri) ? iio_create_context_from_uri(uri) : iio_create_local_context();
    if (!o->ctx) { fprintf(stderr, "iio: kein Kontext (%s)\n", *uri ? uri : "local"); return -1; }
    o->phy = iio_context_find_device(o->ctx, "ad9361-phy");
    o->tx = iio_context_find_device(o->ctx, "cf-ad9361-dds-core-lpc");
    if (!o->phy || !o->tx) { fprintf(stderr, "iio: ad9361-phy / cf-ad9361-dds-core-lpc fehlt\n"); return -1; }
    /* Sicherheitsregel: zuerst stumm */
    phy_write(o->phy, "voltage0", 1, "hardwaregain", "-89.75");
    if (p->fir_file) {
        FILE *f = fopen(p->fir_file, "rb");
        if (!f) { fprintf(stderr, "iio: FIR-Datei %s fehlt\n", p->fir_file); return -1; }
        char *txt = (char *)malloc(65536); size_t n = fread(txt, 1, 65535, f); txt[n] = 0; fclose(f);
        iio_device_attr_write(o->phy, "filter_fir_config", txt); free(txt);
        iio_channel_attr_write_bool(iio_device_find_channel(o->phy, "voltage0", 1), "filter_fir_en", true);
    } else {
        /* Kein eigenes FIR: ein von anderen Werkzeugen geladenes TX-FIR (z. B. 128 Taps INT 4 für eine andere
           Rate) würde das Signal formen → abschalten (bei ≥ 2,083 MS/s ohne FIR zulässig). */
        struct iio_channel *c = iio_device_find_channel(o->phy, "voltage0", 1);
        if (c && iio_channel_attr_write_bool(c, "filter_fir_en", false) < 0)
            fprintf(stderr, "iio: filter_fir_en=0 fehlgeschlagen\n");
    }
    phy_write_ll(o->phy, "voltage0", 1, "sampling_frequency", (long long)(p->sample_rate + 0.5));
    if (p->rf_bw_hz > 0) phy_write_ll(o->phy, "voltage0", 1, "rf_bandwidth", (long long)p->rf_bw_hz);
    if (p->lo_hz > 0) phy_write_ll(o->phy, "altvoltage1", 1, "frequency", (long long)p->lo_hz);
    o->ch_i = iio_device_find_channel(o->tx, "voltage0", 1);
    o->ch_q = iio_device_find_channel(o->tx, "voltage1", 1);
    if (!o->ch_i || !o->ch_q) { fprintf(stderr, "iio: TX-Kanäle fehlen\n"); return -1; }
    iio_channel_enable(o->ch_i); iio_channel_enable(o->ch_q);
    if (p->kernel_buffers > 0) iio_device_set_kernel_buffers_count(o->tx, (unsigned)p->kernel_buffers);
    o->buf_samples = p->buf_samples > 0 ? p->buf_samples : 65536;
    o->cyclic = p->cyclic;
    o->buf = iio_device_create_buffer(o->tx, (size_t)o->buf_samples, p->cyclic ? true : false);
    if (!o->buf) { fprintf(stderr, "iio: Puffer (%d Samples) fehlgeschlagen: %s\n", o->buf_samples, strerror(errno)); return -1; }
    o->fill = (int16_t *)iio_buffer_start(o->buf); o->fill_n = 0;
    if (p->enable) {
        double a = p->atten_db; if (a < 0) a = 0; if (a > 89.75) a = 89.75;
        char v[32]; snprintf(v, sizeof v, "%.2f", -a);
        phy_write(o->phy, "voltage0", 1, "hardwaregain", v);
    }
    return 0;
}

static int iio_push_full(out_sink_t *o)
{
    ssize_t r = iio_buffer_push(o->buf);
    if (r < 0) { o->underflows++; fprintf(stderr, "iio: push fehlgeschlagen (%zd)\n", r); return -1; }
    /* WICHTIG: im mmap-Modus (mehrere Kernel-Puffer) zeigt der Puffer nach jedem Push auf den nächsten
       Block → Schreibzeiger neu holen (sonst werden veraltete Blöcke gesendet). */
    o->fill = (int16_t *)iio_buffer_start(o->buf);
    o->fill_n = 0;
    if (o->cyclic) o->pushed_cyclic = 1;
    return 0;
}
static int iio_write(out_sink_t *o, const int16_t *iq, size_t n)
{
    while (n) {
        if (o->cyclic && o->pushed_cyclic) return 0;    /* zyklisch: nur eine Füllung */
        size_t room = (size_t)(o->buf_samples - o->fill_n), take = n < room ? n : room;
        memcpy(o->fill + 2 * (size_t)o->fill_n, iq, take * 4);
        o->fill_n += (int)take; iq += 2 * take; n -= take; o->samples += take;
        if (o->fill_n == o->buf_samples) {
            if (iio_push_full(o) < 0) return -1;
            if (o->cyclic) return 0;
        }
    }
    return 0;
}
#endif

int16_t *out_direct(out_sink_t *o, size_t n)
{
#ifdef HAVE_LIBIIO
    if (o->kind == 1 && !o->cyclic && (size_t)(o->buf_samples - o->fill_n) >= n) return o->fill + 2 * (size_t)o->fill_n;
#endif
    (void)o; (void)n;
    return NULL;
}

int out_commit(out_sink_t *o, size_t n)
{
#ifdef HAVE_LIBIIO
    o->fill_n += (int)n; o->samples += n;
    if (o->fill_n == o->buf_samples) return iio_push_full(o);
#endif
    (void)o; (void)n;
    return 0;
}

out_sink_t *out_open(const out_params_t *p)
{
    out_sink_t *o = (out_sink_t *)calloc(1, sizeof *o);
    o->rate = p->sample_rate; o->fd = -1;
    if (p->path && !strncmp(p->path, "iio:", 4)) {
#ifdef HAVE_LIBIIO
        o->kind = 1;
        if (iio_open(o, p) < 0) { out_close(o); return NULL; }
#else
        fprintf(stderr, "ohne HAVE_LIBIIO gebaut – keine IIO-Ausgabe\n"); free(o); return NULL;
#endif
    } else {
        o->kind = 0;
        if (!p->path || !strcmp(p->path, "-")) o->fd = 1;
        else { o->fd = open(p->path, O_WRONLY | O_CREAT | O_TRUNC, 0644); if (o->fd < 0) { free(o); return NULL; } }
    }
    return o;
}

int out_write(out_sink_t *o, const int16_t *iq, size_t n)
{
#ifdef HAVE_LIBIIO
    if (o->kind == 1) return iio_write(o, iq, n);
#endif
    o->samples += n;
    if (o->realtime) {
        double due = o->t0 + (double)o->samples / o->rate, now = dvb_now();
        if (due > now) { struct timespec ts = {0, (long)((due - now) * 1e9)}; nanosleep(&ts, NULL); }
    }
    return write_all(o->fd, iq, n * 4);
}

void out_set_realtime(out_sink_t *o, int on) { o->realtime = on; o->t0 = dvb_now(); o->samples = 0; }

void out_stats(out_sink_t *o, uint64_t *samples, uint64_t *underflows)
{
    *samples = o->samples; *underflows = o->underflows;
#ifdef HAVE_LIBIIO
    if (o->kind == 1 && o->tx) {   /* DMA-Unterlauf-Zähler des Kerns, falls vorhanden */
        /* cf-ad9361-dds-core-lpc hat kein Underflow-Attribut; Zähler über Pushes */
    }
#endif
}

void out_close(out_sink_t *o)
{
    if (!o) return;
#ifdef HAVE_LIBIIO
    if (o->kind == 1) {
        if (o->buf && !o->cyclic && o->fill_n) { memset(o->fill + 2 * (size_t)o->fill_n, 0, 4 * (size_t)(o->buf_samples - o->fill_n)); iio_buffer_push(o->buf); }
        if (o->buf && !o->cyclic) iio_buffer_destroy(o->buf);
        if (o->buf && o->cyclic) { /* zyklischer Puffer läuft weiter bis Prozessende */ }
        if (o->phy && !o->cyclic) phy_write(o->phy, "voltage0", 1, "hardwaregain", "-89.75");
        if (o->ctx && !o->cyclic) iio_context_destroy(o->ctx);
        free(o); return;
    }
#endif
    if (o->fd > 1) close(o->fd);
    free(o);
}
