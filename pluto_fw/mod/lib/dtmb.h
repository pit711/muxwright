/*
 * dtmb.h – DTMB (GB 20600-2006) Bausteine für dtmb_mod.
 * Copyright (c) 2026 DAB-Lab. Lizenz: MIT (siehe LICENSE). Eigene Implementierung nach GB 20600-2006.
 * Normkonstanten (PN-Folgen, Systeminformation Anhang G, Frequenzverschachtelung Anhang F, NR-Code, Mapping,
 * Verwürfler) gegen den freien Empfänger dtmb-sdr (MIT, github.com/ningzichun/dtmb-sdr) abgeglichen; die LDPC-
 * Generatormatrix ist aus dessen Prüfmatrizen (Anhang B) abgeleitet (tools/gen_dtmb_ldpc.py), kein Code übernommen.
 *
 * Kette: TS → Verwürfelung (1+x^14+x^15, Reset je Signalrahmen) → BCH(762,752) → LDPC(7493→7488, k = 3048/4572/6096)
 *        → Mapping (4QAM-NR, 4QAM, 16QAM, 32QAM, 64QAM) → Faltungs-Zeitverschachtelung (B = 52, M = 240/720;
 *        bei 4QAM-NR auf Bitebene vor dem NR-Code) → Rahmenkörper 3780 = 36 SI + 3744 Daten (C = 3780: Frequenz-
 *        verschachtelung + IDFT; C = 1: Zeitbereich) → Rahmenkopf PN420/PN595/PN945 → 7,56 MS/s → SRRC α = 0,05.
 */
#ifndef DTMB_H
#define DTMB_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define DTMB_SYMBOL_RATE   7560000.0
#define DTMB_BODY          3780
#define DTMB_DATA          3744
#define DTMB_SI            36
#define DTMB_CW_BITS       7488
#define DTMB_CW_WORDS      (DTMB_CW_BITS / 32)
#define DTMB_MAX_HDR       945
#define DTMB_MAX_FRAME     (DTMB_MAX_HDR + DTMB_BODY)

enum dtmb_qam { DTMB_4QAMNR = 0, DTMB_4QAM, DTMB_16QAM, DTMB_32QAM, DTMB_64QAM };

typedef struct {
    int carriers;     /* 3780 oder 1 */
    int qam;          /* enum dtmb_qam */
    int rate;         /* 0 = 0,4, 1 = 0,6, 2 = 0,8 */
    int pn;           /* 420, 595, 945 */
    int ti;           /* 240 oder 720 */
    int pn_rotate;    /* 1 = Kopf-PN-Phase wechselt je Rahmen (PN420/945), 0 = konstant */
} dtmb_cfg_t;

/* ---------------------------------------------------------------- Parameter */
int         dtmb_cfg_check(const dtmb_cfg_t *c, char *err, size_t errlen);   /* 0 = gültig */
int         dtmb_si_index(const dtmb_cfg_t *c);       /* Systeminformations-Index 3..24 (Anhang G) */
int         dtmb_frame_len(int pn);                   /* Symbole je Signalrahmen (Kopf + 3780) */
int         dtmb_frames_per_superframe(int pn);       /* 225 / 216 / 200 (je 125 ms) */
int         dtmb_frames_per_group(const dtmb_cfg_t *c);   /* 2 bei 4QAM-NR und 32QAM, sonst 1 */
int         dtmb_cw_per_group(const dtmb_cfg_t *c);       /* LDPC-Codewörter je Gruppe */
int         dtmb_packets_per_frame2(const dtmb_cfg_t *c); /* TS-Pakete je 2 Signalrahmen */
double      dtmb_ts_bps(const dtmb_cfg_t *c);         /* Nutzrate (TS inkl. Sync-Byte) */
const char *dtmb_qam_name(int q);
extern const char *const dtmb_rate_name[3];

/* ---------------------------------------------------------------- LDPC (QC, Kreisgröße 127) */
typedef struct dtmb_ldpc dtmb_ldpc_t;
dtmb_ldpc_t *dtmb_ldpc_create(int rate);
void         dtmb_ldpc_free(dtmb_ldpc_t *l);
int          dtmb_ldpc_kb(const dtmb_ldpc_t *l);      /* Informationsblöcke (24/36/48) */
int          dtmb_ldpc_cb(const dtmb_ldpc_t *l);      /* Paritätsblöcke (35/23/11) */
/* info: K = kb·127 Bits LSB-zuerst (Bit t in Wort t/32, Stelle t%32); cw: 7488 Bits LSB-zuerst =
   [Parität 5..c·127−1 | Information]. */
void         dtmb_ldpc_encode(dtmb_ldpc_t *l, const uint32_t *info, uint32_t *cw);
void         dtmb_ldpc_encode_ref(const dtmb_ldpc_t *l, const uint32_t *info, uint32_t *cw);  /* langsam (Test) */
const char  *dtmb_ldpc_backend(void);

/* ---------------------------------------------------------------- Sender */
typedef struct dtmb_tx dtmb_tx_t;
/* Paketquelle: 188 Byte nach pkt, 1 = ok, 0 = Ende */
typedef int (*dtmb_pkt_fn)(void *ctx, uint8_t *pkt);

/* Arbeitsauftrag zwischen Codier- und Ausgabestufe (ein Signalrahmen). C = 3780: die Codierstufe liefert die Trägerwerte
   nach Abbildung und Frequenzverschachtelung (x), die Ausgabestufe rechnet IDFT + Rahmenkopf; C = 1: Labels, die Ausgabe-
   stufe bildet ab. So sind beide Kerne bei der Höchstrate ähnlich ausgelastet. */
typedef struct {
    int     frame_no;          /* Rahmenzähler ab 0 */
    int     pn_phase;
    uint8_t lab[DTMB_BODY];    /* Rahmenkörper in logischer Reihenfolge: 0..63 Datenlabel, 64..99 SI-Symbol 0..35 */
    uint32_t x[DTMB_BODY + 16]; /* C = 3780: Trägerwerte (re | im << 16) in physikalischer Reihenfolge (IDFT-Eingang) */
} dtmb_job_t;

/* gain: komplexer Effektivwert des Rahmenkörpers in int16-Einheiten (Rahmenkopf entsprechend ×√2 bei PN420/945) */
dtmb_tx_t *dtmb_tx_create(const dtmb_cfg_t *c, double rms);
void       dtmb_tx_free(dtmb_tx_t *t);
dtmb_job_t *dtmb_job_alloc(const dtmb_tx_t *t);
void       dtmb_job_free(dtmb_job_t *j);
/* Codierstufe: einen Signalrahmen erzeugen (holt bei Gruppenbeginn alle Pakete der Gruppe). 0 = Quelle zu Ende
   (nur an Gruppengrenzen; innerhalb einer Gruppe wird mit Nullpaketen aufgefüllt). */
int        dtmb_tx_encode(dtmb_tx_t *t, dtmb_pkt_fn src, void *ctx, dtmb_job_t *job);
int        dtmb_tx_group_boundary(const dtmb_tx_t *t);
/* Ausgabestufe: Rahmenkopf + Körper → frame_len komplexe Samples (int16, 1 Sample/Symbol). Thread-sicher
   gegenüber dtmb_tx_encode (eigene Arbeitspuffer je Aufrufer über scratch, dtmb_tx_scratch_len int16-Werte). */
size_t     dtmb_tx_scratch_len(const dtmb_tx_t *t);
int        dtmb_tx_render(const dtmb_tx_t *t, const dtmb_job_t *job, int16_t *scratch, int16_t *out_iq);
int        dtmb_tx_frame_len(const dtmb_tx_t *t);
double     dtmb_tx_peak_ratio(const dtmb_tx_t *t);    /* Kopfamplitude / RMS (Information) */
/* Profil: Sekunden je Stufe (Verwürfelung+BCH+Bitpacken, LDPC, Labels, Zeitverschachtelung+SI) */
void       dtmb_tx_profile(const dtmb_tx_t *t, double out[4]);
void       dtmb_tx_set_profile(dtmb_tx_t *t, int on);

/* ---------------------------------------------------------------- 3780-Punkt-IDFT (int16 Q15, gemischte Basis 4·3·3·3·5·7) */
typedef struct dtmb_fft dtmb_fft_t;
dtmb_fft_t *dtmb_fft_create(void);
void        dtmb_fft_free(dtmb_fft_t *f);
/* inverse DFT ohne 1/N: out[n] = scale·Σ_k x[k]·e^{+j2πkn/N}; x und out I/Q verschränkt (int16, sättigend), x mit
   2·dtmb_fft_buflen() Werten (gepolstert); re/im/wr/wi Arbeitspuffer je dtmb_fft_buflen(); scale Q15 (32767 = 1) */
size_t      dtmb_fft_buflen(void);
void        dtmb_ifft(const dtmb_fft_t *f, const int16_t *x, int16_t *re, int16_t *im, int16_t *wr, int16_t *wi, int16_t *out, int16_t scale);
const char *dtmb_fft_backend(void);

/* ---------------------------------------------------------------- SRRC α = 0,05 (2 Samples/Symbol) */
typedef struct {
    int      ntaps;          /* Vielfaches von 16, ≤ 128 für den AD9361 */
    int16_t  h[256];         /* Q15, größter Tap 32767 */
    double   phase_gain[2];  /* Summe der Taps je Phase / 32768 */
    double   power_gain;     /* Σh²/(2·32768²): Leistungsverstärkung bei Interpolation ×2 */
    /* Polyphasenfilter (Software, sps 2) */
    int16_t  hp[2][128];
    int16_t *hist;           /* 2·(ntaps/2) komplexe Historie */
} dtmb_srrc_t;
int    dtmb_srrc_init(dtmb_srrc_t *s, int ntaps, double alpha, double kaiser_beta);
void   dtmb_srrc_free(dtmb_srrc_t *s);
/* n Symbole (int16 I/Q) → 2n Samples */
void   dtmb_srrc_run(dtmb_srrc_t *s, const int16_t *in, size_t n, int16_t *out);
/* AD9361-FIR-Datei (TX INT 2, RX DEC 2) als Text */
int    dtmb_srrc_fir_text(const dtmb_srrc_t *s, char *buf, size_t len);

/* ---------------------------------------------------------------- Selbsttest */
int    dtmb_selftest(int verbose);

#ifdef __cplusplus
}
#endif
#endif
