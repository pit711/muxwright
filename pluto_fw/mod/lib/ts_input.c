/*
 * ts_input.c – Transportstrom-Eingang: Datei, stdin, UDP (optional RTP), synthetischer PRBS-TS.
 * Liefert immer genau ein 188-Byte-Paket je Aufruf; fehlt Nutzlast, wird ein Nullpaket gestopft.
 * UDP: Ringpuffer in Paketen; Lesethread; oberhalb der Hochwassermarke werden eingehende Nullpakete
 * (PID 0x1FFF) verworfen, unterhalb der Niedrigwassermarke wird gestopft (PCR-neutral).
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
#include <pthread.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>

enum { SRC_FILE, SRC_STDIN, SRC_UDP, SRC_PRBS, SRC_NULL };

struct ts_input {
    int kind, loop, fd;
    FILE *fp;
    ts_stats_t st;
    /* Ring für UDP */
    uint8_t *ring; int cap, head, tail; /* in Paketen */
    pthread_t thr; pthread_mutex_t mx; pthread_cond_t cv; int running;
    int low, high, primed;
    /* PRBS */
    uint32_t prbs; uint8_t cc;
    /* Dateisynchronisation */
    uint8_t fbuf[204 * 64]; int fpos, flen, pktsize;
    uint64_t prbs_limit;
};

static const uint8_t NULL_PKT_HDR[4] = {0x47, 0x1F, 0xFF, 0x10};

static void make_null(uint8_t *p) { memcpy(p, NULL_PKT_HDR, 4); memset(p + 4, 0xFF, 184); }

/* ---- Datei/stdin: Blockweise lesen, auf 0x47 synchronisieren (188 oder 204 Byte Raster) */
static int file_refill(ts_input_t *t)
{
    if (t->fpos < t->flen) { memmove(t->fbuf, t->fbuf + t->fpos, t->flen - t->fpos); t->flen -= t->fpos; }
    else t->flen = 0;
    t->fpos = 0;
    while (t->flen < (int)sizeof t->fbuf) {
        ssize_t r = read(t->fd, t->fbuf + t->flen, sizeof t->fbuf - t->flen);
        if (r < 0) { if (errno == EINTR) continue; return -1; }
        if (r == 0) {
            if (t->loop && t->kind == SRC_FILE) { lseek(t->fd, 0, SEEK_SET); if (t->flen >= 204) break; continue; }
            break;
        }
        t->flen += (int)r; t->st.bytes_in += (uint64_t)r;
    }
    return t->flen;
}

static int file_packet(ts_input_t *t, uint8_t *pkt)
{
    for (;;) {
        if (t->flen - t->fpos < 204 + 1) {
            if (file_refill(t) <= 0) return 0;
            if (t->flen - t->fpos < TS_PKT) return 0;
        }
        uint8_t *b = t->fbuf + t->fpos;
        if (b[0] == TS_SYNC) {
            int rest = t->flen - t->fpos;
            if (t->pktsize == 0) {   /* Rastererkennung */
                if (rest > 204 && b[204] == TS_SYNC && !(b[188] == TS_SYNC)) t->pktsize = 204;
                else t->pktsize = TS_PKT;
            }
            if (rest < t->pktsize) { if (rest >= TS_PKT && t->pktsize == 204) { /* letztes Paket */ } else return 0; }
            memcpy(pkt, b, TS_PKT);
            t->fpos += (rest >= t->pktsize) ? t->pktsize : TS_PKT;
            return 1;
        }
        t->fpos++; t->st.sync_losses++;
    }
}

/* ---- UDP */
int ts_input_rt_prio;   /* >0: Lesethread SCHED_FIFO – sonst verhungert er neben RT-Rechenthreads auf beiden Kernen */

static void *udp_thread(void *arg)
{
    ts_input_t *t = (ts_input_t *)arg;
    uint8_t buf[65536];
    if (ts_input_rt_prio > 0) {
        struct sched_param sp; sp.sched_priority = ts_input_rt_prio;
        if (pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp)) perror("SCHED_FIFO udp");
    }
    while (t->running) {
        ssize_t r = recv(t->fd, buf, sizeof buf, 0);
        if (r <= 0) { if (errno == EINTR || errno == EAGAIN) continue; break; }
        t->st.bytes_in += (uint64_t)r;
        uint8_t *p = buf; int n = (int)r;
        if (n >= 12 && (p[0] & 0xC0) == 0x80 && p[0] != TS_SYNC) {   /* RTP: V=2 */
            int cc = p[0] & 0x0F, ext = p[0] & 0x10;
            int hl = 12 + 4 * cc;
            if (ext && n >= hl + 4) hl += 4 + 4 * ((p[hl + 2] << 8) | p[hl + 3]);
            if (hl > n) continue;
            p += hl; n -= hl; t->st.rtp_packets++;
        }
        pthread_mutex_lock(&t->mx);
        while (n >= TS_PKT) {
            if (p[0] != TS_SYNC) { p++; n--; t->st.sync_losses++; continue; }
            int fill = (t->head - t->tail + t->cap) % t->cap;
            int pid = ((p[1] & 0x1F) << 8) | p[2];
            if (fill >= t->cap - 1) { t->st.nulls_dropped++; /* Überlauf: Paket verwerfen */ }
            else if (fill > t->high && pid == 0x1FFF) { t->st.nulls_dropped++; }
            else { memcpy(t->ring + (size_t)t->head * TS_PKT, p, TS_PKT); t->head = (t->head + 1) % t->cap; t->st.packets_in++; }
            p += TS_PKT; n -= TS_PKT;
        }
        pthread_cond_signal(&t->cv);
        pthread_mutex_unlock(&t->mx);
    }
    return NULL;
}

static int udp_open(ts_input_t *t, const char *spec)
{
    /* udp://[addr]:port oder udp://port */
    char addr[64] = ""; int port = 0;
    const char *s = spec + 6, *c = strrchr(s, ':');
    if (c) { size_t l = (size_t)(c - s); if (l >= sizeof addr) l = sizeof addr - 1; memcpy(addr, s, l); addr[l] = 0; port = atoi(c + 1); }
    else port = atoi(s);
    if (port <= 0) return -1;
    t->fd = socket(AF_INET, SOCK_DGRAM, 0);
    int one = 1; setsockopt(t->fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    int rcv = 4 << 20; setsockopt(t->fd, SOL_SOCKET, SO_RCVBUF, &rcv, sizeof rcv);
    struct sockaddr_in sa; memset(&sa, 0, sizeof sa); sa.sin_family = AF_INET; sa.sin_port = htons((uint16_t)port);
    sa.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(t->fd, (struct sockaddr *)&sa, sizeof sa) < 0) return -1;
    if (addr[0]) {
        struct in_addr ia; if (inet_aton(addr, &ia) && (ntohl(ia.s_addr) >> 28) == 0xE) {
            struct ip_mreq mr; mr.imr_multiaddr = ia; mr.imr_interface.s_addr = htonl(INADDR_ANY);
            setsockopt(t->fd, IPPROTO_IP, IP_ADD_MEMBERSHIP, &mr, sizeof mr);
        }
    }
    struct timeval tv = {1, 0}; setsockopt(t->fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    t->running = 1;
    pthread_mutex_init(&t->mx, NULL); pthread_cond_init(&t->cv, NULL);
    pthread_create(&t->thr, NULL, udp_thread, t);
    return 0;
}

ts_input_t *ts_input_open(const char *spec, int loop, int buffer_packets, int realtime_fill)
{
    ts_input_t *t = (ts_input_t *)calloc(1, sizeof *t);
    t->loop = loop; t->prbs = 0x12345678u; t->fd = -1;
    (void)realtime_fill;
    if (!spec || !strcmp(spec, "-")) { t->kind = SRC_STDIN; t->fd = 0; }
    else if (!strncmp(spec, "udp://", 6)) {
        t->kind = SRC_UDP; t->cap = buffer_packets > 16 ? buffer_packets : 2000;
        t->ring = (uint8_t *)malloc((size_t)t->cap * TS_PKT);
        t->low = t->cap / 4; t->high = t->cap * 3 / 4;
        if (udp_open(t, spec) < 0) { free(t->ring); free(t); return NULL; }
    } else if (!strncmp(spec, "prbs", 4)) {
        t->kind = SRC_PRBS; t->prbs_limit = (spec[4] == ':') ? strtoull(spec + 5, NULL, 10) : 0;
    } else if (!strcmp(spec, "null")) t->kind = SRC_NULL;
    else {
        t->kind = SRC_FILE; t->fd = open(spec, O_RDONLY);
        if (t->fd < 0) { free(t); return NULL; }
    }
    t->st.capacity = t->cap;
    return t;
}

int ts_input_packet(ts_input_t *t, uint8_t *pkt)
{
    switch (t->kind) {
    case SRC_NULL: make_null(pkt); t->st.nulls_stuffed++; t->st.packets_out++; return 1;
    case SRC_PRBS: {
        if (t->prbs_limit && t->st.packets_out >= t->prbs_limit) { t->st.eof = 1; return 0; }
        pkt[0] = TS_SYNC; pkt[1] = 0x01; pkt[2] = 0x00; pkt[3] = (uint8_t)(0x10 | (t->cc & 15)); t->cc++;
        for (int i = 4; i < TS_PKT; i++) { t->prbs = t->prbs * 1664525u + 1013904223u; pkt[i] = (uint8_t)(t->prbs >> 24); }
        t->st.packets_in++; t->st.packets_out++; return 1;
    }
    case SRC_FILE: case SRC_STDIN:
        if (t->st.eof) return 0;
        if (file_packet(t, pkt)) { t->st.packets_in++; t->st.packets_out++; return 1; }
        t->st.eof = 1; return 0;
    case SRC_UDP: {
        pthread_mutex_lock(&t->mx);
        int fill = (t->head - t->tail + t->cap) % t->cap;
        t->st.fill = fill;
        /* Vorfüllung: erst ausgeben, wenn der Ring halb voll ist (Jitter-Reserve in beide Richtungen);
           nach einem Leerlauf erneut vorfüllen */
        if (!t->primed && fill >= t->cap / 2) t->primed = 1;
        if (t->primed && fill == 0) { t->primed = 0; t->st.sync_losses++; }
        if (t->primed && fill > 0) {
            memcpy(pkt, t->ring + (size_t)t->tail * TS_PKT, TS_PKT); t->tail = (t->tail + 1) % t->cap;
            pthread_mutex_unlock(&t->mx);
        } else {
            pthread_mutex_unlock(&t->mx);
            make_null(pkt); t->st.nulls_stuffed++;
        }
        t->st.packets_out++; return 1;
    }
    }
    return 0;
}

void ts_input_stats(ts_input_t *t, ts_stats_t *st)
{
    if (t->kind == SRC_UDP) { pthread_mutex_lock(&t->mx); t->st.fill = (t->head - t->tail + t->cap) % t->cap; pthread_mutex_unlock(&t->mx); }
    *st = t->st;
}

void ts_input_close(ts_input_t *t)
{
    if (!t) return;
    if (t->kind == SRC_UDP) { t->running = 0; pthread_join(t->thr, NULL); close(t->fd); free(t->ring); }
    else if (t->kind == SRC_FILE) close(t->fd);
    free(t);
}
