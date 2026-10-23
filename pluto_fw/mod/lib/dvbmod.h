/*
 * libdvbmod_lab – gemeinsame Bausteine für DVB-C (EN 300 429) und DVB-T (EN 300 744)
 * Labor-Modulatoren (ADALM-Pluto, Cortex-A9) – portable C99, keine Floats im Coder.
 *
 * Copyright (c) 2026 DAB-Lab. Lizenz: MIT (siehe LICENSE). Eigene Implementierung nach den
 * ETSI-Normen; Tabellen gegen gr-dtv (GPL-3.0, GNU Radio) gegengeprüft, kein Code übernommen.
 */
#ifndef DVBMOD_H
#define DVBMOD_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define TS_PKT      188
#define RS_PKT      204
#define TS_SYNC     0x47
#define TS_NSYNC    0xB8   /* invertiertes Sync-Byte alle 8 Pakete */

/* ---------------------------------------------------------------- Energieverwürfelung */
typedef struct {
    uint8_t seq[8 * TS_PKT];   /* vorberechnete XOR-Sequenz für 8 Pakete (Sync-Positionen = 0) */
    int     pkt;               /* Paketzähler 0..7 */
} dvb_scrambler_t;

void dvb_scrambler_init(dvb_scrambler_t *s);
/* in-place; schreibt 0xB8 ins erste Paket jeder 8er-Gruppe, XOR der Nutzbytes */
void dvb_scramble_packet(dvb_scrambler_t *s, uint8_t *pkt);

/* ---------------------------------------------------------------- RS(204,188), T=8, GF(2^8) 0x11d */
void dvb_rs_init(void);                                  /* Tabellen aufbauen (einmalig) */
void dvb_rs_encode(const uint8_t *in188, uint8_t *out204); /* out darf == in sein (in liegt in out[0..187]) */
int  dvb_rs_check(const uint8_t *pkt204);                /* 1 = Syndrom null (nur Test) */

/* ---------------------------------------------------------------- Faltungsinterleaver I=12, M=17 */
typedef struct {
    uint8_t hist[12][RS_PKT];
    int     pos;
} dvb_convint_t;

void dvb_convint_init(dvb_convint_t *ci);
/* ein 204-Byte-Paket rein, ein 204-Byte-Paket raus (Byte i verzögert um (i mod 12) Pakete) */
void dvb_convint_packet(dvb_convint_t *ci, const uint8_t *in204, uint8_t *out204);

/* ---------------------------------------------------------------- Faltungscoder K=7 + Punktierung */
enum dvb_coderate { CR_1_2 = 0, CR_2_3, CR_3_4, CR_5_6, CR_7_8 };

typedef struct {
    int      cr;              /* enum dvb_coderate */
    int      period;          /* Punktierperiode in Eingangsbits (1,2,3,5,7) */
    int      nout;            /* Ausgangsbits je Periode (2,3,4,6,8) */
    int      phase;           /* 0..period-1 */
    unsigned state;           /* 6 Bit Coderzustand */
    /* Punktiertabellen: pro Phase (0..6) eine 16-Bit-Maske über (X0 Y0 X1 Y1 .. X7 Y7) */
    uint16_t mask16[7];
    uint8_t  mask_idx_hi[7], mask_idx_lo[7];
    uint8_t  cmp_val[14][256]; /* Kompression: kept bits von (maskbyte, byte) rechtsbündig */
    uint8_t  cmp_cnt[14];
    uint8_t  next_phase[7];
    /* Bitausgabe */
    uint64_t acc; int accn;
} dvb_convcode_t;

void dvb_convcode_init(dvb_convcode_t *c, int coderate);
/* codiert n Eingangsbytes, hängt punktierte Bits (MSB zuerst) an out an; gibt Anzahl geschriebener Bytes zurück.
   Reste < 8 Bit bleiben im Akkumulator. out muss Platz für 2*n+2 Bytes bieten. */
size_t dvb_convcode_bytes(dvb_convcode_t *c, const uint8_t *in, size_t n, uint8_t *out);
/* nützliche Konstanten */
extern const char *dvb_coderate_name[5];
extern const int dvb_coderate_num[5], dvb_coderate_den[5];

/* ---------------------------------------------------------------- DVB-T innerer Interleaver + Mapper */
enum dvbt_mode { DVBT_2K = 0, DVBT_8K = 1 };
enum dvbt_const { DVBT_QPSK = 0, DVBT_16QAM = 1, DVBT_64QAM = 2 };

typedef struct {
    int mode, v, ncells, nfft, K, Kmax;
    uint16_t *H;              /* Symbol-Interleaver-Permutation H(q), ncells Einträge */
    uint8_t   demux_pos[6];   /* Bitposition (vom MSB=0) im x-Wort für Substream e */
    uint8_t   he_off[6];      /* H_e(w) = (w + off) mod 126 */
    int16_t   map_re[64], map_im[64]; /* Konstellation × Verstärkung A (Index = y-Wort) */
    uint32_t  map_iq[64];             /* dieselben Punkte als (re | im<<16) für einen 32-Bit-Store */
    int       A;              /* Zellverstärkung (Datenzelle, mittlere Leistung A^2) */
} dvbt_inner_t;

int  dvbt_inner_init(dvbt_inner_t *in, int mode, int constellation, int A);
void dvbt_inner_free(dvbt_inner_t *in);
/* punktierte Bits (ncells*v/8 Bytes) → y'-Wörter (ncells Bytes): Demux + Bit-Interleaver */
void dvbt_bit_interleave(const dvbt_inner_t *in, const uint8_t *bits, uint8_t *yp);
/* Symbol-Interleaver: l gerade: y[H(q)] = y'[q]; ungerade: y[q] = y'[H(q)] */
void dvbt_symbol_interleave(const dvbt_inner_t *in, const uint8_t *yp, uint8_t *y, int odd);

/* ---------------------------------------------------------------- DVB-T Rahmen (Piloten, TPS, Trägerbelegung) */
enum dvbt_guard { GI_1_32 = 0, GI_1_16, GI_1_8, GI_1_4 };

typedef struct {
    int mode, nfft, K, guard_len, guard;
    int constellation, coderate_hp, coderate_lp, cell_id, hierarchy;
    uint8_t  *wk;             /* PRBS w_k, K Einträge */
    uint16_t *data_bin[4];    /* FFT-Bin-Indizes der Datenträger je (l mod 4), ncells Einträge */
    int       ndata;
    uint16_t *pil_bin[4];     /* Bins der Piloten (verstreut ∪ kontinuierlich) je (l mod 4) */
    int16_t  *pil_val[4];     /* Pilotwert (±4/3·A) */
    uint32_t *pil_iq[4];      /* Pilotwert als (re | 0<<16) */
    int       npil[4];
    uint16_t  tps_bin[68];    /* Bins der TPS-Träger */
    int       ntps;
    int8_t    tps_sign[68];   /* aktueller DBPSK-Zustand (±1) je TPS-Träger */
    uint8_t   tps_bits[68];   /* s0..s67 des laufenden Rahmens */
    int       A;              /* wie dvbt_inner_t.A */
    int       frame;          /* 0..3 im Superrahmen */
    int       symbol;         /* 0..67 im Rahmen */
    double    mean_cell_power;/* mittlere Σ|X_k|²/A² je Symbol (für Pegelrechnung) */
} dvbt_frame_t;

int  dvbt_frame_init(dvbt_frame_t *f, int mode, int guard, int constellation, int coderate,
                     int cell_id, int A);
void dvbt_frame_free(dvbt_frame_t *f);
/* baut Frequenzbereichsvektor (interleaved I/Q int16, nfft Einträge, Bin = (k - K/2) mod nfft) für das nächste
   OFDM-Symbol aus den gemappten Datenzellen y (ncells Wörter) und schaltet Symbol/Rahmenzähler weiter. */
void dvbt_frame_build(dvbt_frame_t *f, const dvbt_inner_t *in, const uint8_t *y, int16_t *iq);
void dvbt_tps_generate(dvbt_frame_t *f);  /* s0..s67 für f->frame (inkl. BCH) – wird intern aufgerufen */

/* ---------------------------------------------------------------- Festkomma-IFFT int16 (radix-4 Stockham) */
typedef struct fft16 fft16_t;
fft16_t *fft16_create(int n);          /* n = 2048, 4096 oder 8192 (2·4^k) */
void     fft16_free(fft16_t *f);
int      fft16_shift_bits(const fft16_t *f);   /* Gesamtskalierung 2^-S (S = Anzahl Radix-4-Stufen) */
/* inverse FFT: Eingang interleaved I/Q int16 (n Bins), Ausgang interleaved I/Q int16 mit Guard (n+guard Samples) */
void     fft16_ifft_ofdm(fft16_t *f, const int16_t *in_iq, int16_t *out_iq, int guard);
const char *fft16_backend(void);

/* ---------------------------------------------------------------- OFDM-Symbolfensterung (Raised Cosine)
   Jedes Symbol wird um W Samples zyklisch verlängert (Suffix = erste W Samples des Nutzteils) und mit dem
   Anfang des Folgesymbols überlappt: y[i] = x_k[i]·r[i] + suffix_{k-1}[i]·(1−r[i]), i < W (im Guard).
   Senkt die Nebenaussendung (1/f² → steiler); die nutzbare Guard-Länge sinkt um W. */
typedef struct {
    int W;
    int16_t *rise;            /* Q15, r[i] = 0,5·(1 − cos(π(i+0,5)/W)) */
    int16_t *suffix;          /* W komplexe Samples (interleaved) des Vorgängers */
    int have_prev;
} ofdm_window_t;
int  ofdm_window_init(ofdm_window_t *w, int W);
void ofdm_window_free(ofdm_window_t *w);
/* sym: interleaved I/Q, guard+n Samples (CP zuerst); in-place */
void ofdm_window_apply(ofdm_window_t *w, int16_t *sym, int guard);

/* ---------------------------------------------------------------- DVB-C Mapper (EN 300 429) */
typedef struct {
    int m;                    /* Bits je Symbol 4..8 */
    int16_t re[256], im[256]; /* Konstellation (Index: m-Bit-Wort nach Differenzcodierung) × A */
    int ip, qp;               /* Differenzcoder-Zustand */
    uint32_t bitacc; int bitn;/* Byte→m-Tupel */
} dvbc_map_t;

int  dvbc_map_init(dvbc_map_t *m, int qam /*16,32,64,128,256*/, int A);
/* Bytes → Symbole (I/Q int16 interleaved); gibt Anzahl Symbole zurück; Reste bleiben im Akkumulator */
size_t dvbc_map_bytes(dvbc_map_t *m, const uint8_t *in, size_t n, int16_t *out_iq);
double dvbc_map_rms(int qam);  /* RMS der Konstellation (ungeradzahlige Koordinaten) */

/* ---------------------------------------------------------------- RRC-Filter (Festkomma, 2 Samples/Symbol) */
typedef struct {
    int ntaps, sps;
    int32_t *taps;            /* Q15 */
    int16_t *hist_i, *hist_q; int hpos;
} rrc_t;
int    rrc_design(double alpha, int sps, int ntaps, double *h);   /* normiert: Σh = sps (DC-Verstärkung 1 je Phase) */
int    rrc_init(rrc_t *r, double alpha, int sps, int ntaps);
void   rrc_free(rrc_t *r);
/* n Symbole rein → n*sps Samples raus (interleaved I/Q), Polyphase */
size_t rrc_run(rrc_t *r, const int16_t *sym_iq, size_t n, int16_t *out_iq);

/* ---------------------------------------------------------------- TS-Eingang */
typedef struct ts_input ts_input_t;
typedef struct {
    uint64_t packets_in, packets_out, nulls_stuffed, nulls_dropped, sync_losses, rtp_packets, bytes_in;
    int      fill, capacity;       /* Pufferfüllstand in Paketen */
    int      eof;
} ts_stats_t;
/* spec: "-" stdin, "udp://[addr]:port", "udp://port", sonst Dateiname. loop: Datei wiederholen. */
ts_input_t *ts_input_open(const char *spec, int loop, int buffer_packets, int realtime_fill);
/* liefert immer ein 188-Byte-Paket: Nutzdaten, sonst Nullpaket; 0 = Ende (Datei/stdin ohne loop) */
int   ts_input_packet(ts_input_t *t, uint8_t *pkt);
void  ts_input_stats(ts_input_t *t, ts_stats_t *st);
void  ts_input_close(ts_input_t *t);

/* ---------------------------------------------------------------- Ausgabe (Datei/stdout/IIO) */
typedef struct out_sink out_sink_t;
typedef struct {
    const char *path;         /* "-" = stdout, "iio:" = lokaler IIO-Kontext, "iio:uri" sonst Datei */
    double sample_rate;
    double lo_hz;             /* nur IIO */
    double atten_db;          /* nur IIO: hardwaregain = -atten (Start immer -89.75) */
    double rf_bw_hz;          /* nur IIO */
    int    cyclic;            /* 1 = zyklischer Puffer (einmal füllen) */
    int    buf_samples;       /* IIO-Puffergröße in Samples */
    int    kernel_buffers;
    int    enable;            /* 0 = Pluto stumm lassen (Dämpfung bleibt) */
    int    fir_int;           /* 0 = kein FIR; sonst Datei mit FIR-Konfiguration laden */
    const char *fir_file;
} out_params_t;

out_sink_t *out_open(const out_params_t *p);
int   out_write(out_sink_t *o, const int16_t *iq, size_t nsamples);  /* blockierend */
void  out_set_realtime(out_sink_t *o, int on);   /* Datei/stdout mit Echtzeitbremse (nominale Rate) */
void  out_stats(out_sink_t *o, uint64_t *samples, uint64_t *underflows);
void  out_close(out_sink_t *o);

/* ---------------------------------------------------------------- Zeitmessung */
double  dvb_now(void);              /* Sekunden (monotonic) */
double  dvb_cpu_time(void);         /* Thread-CPU-Zeit in s */
uint64_t dvb_cycles(void);          /* grobe Zyklen (x86 rdtsc / ARM Zeit*Takt) */

#ifdef __cplusplus
}
#endif
#endif
