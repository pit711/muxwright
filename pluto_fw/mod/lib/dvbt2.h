/*
 * dvbt2.h – DVB-T2-Sender (ETSI EN 302 755 V1.4.1), T2-base, SISO, ein PLP (Input Mode A, Typ 1), TS in HEM
 * (High Efficiency Mode, ohne ISSY/NPD), keine FEF, kein T2-Lite, PAPR aus, T2_VERSION 1.1.1.
 *
 * Kette: BBFRAME (HEM, CRC-8/MODE, BB-Verwürfelung) → BCH → LDPC (T2-Tabellen, 64800/16200) → Bit-Interleaver
 * (Paritäts-/Column-Twist) + Demux → Mapping (QPSK…256-QAM, Rotation + zyklische Q-Verzögerung) → Zell-Interleaver →
 * Zeit-Interleaver (N_TI TI-Blöcke je Rahmen) → Rahmenbildung (L1-pre/L1-post mit verkürzter/punktierter 16K-LDPC,
 * P2, Dummy-Zellen, Frame-Closing-Symbol) → Frequenz-Interleaver → Piloten (SP/CP/Edge/P2/FC) → int16-IFFT + Guard,
 * P1-Symbol vorab berechnet. Ausgabe IQ int16 (I, Q), Kanalmitte = Träger (Kmin+Kmax)/2 auf 0 Hz.
 *
 * Zwei Hälften für zwei Threads: t2_encode_frame() (Kodierung bis Frequenz-Interleaver, Kern 0) und
 * t2_render_*() (Piloten, IFFT, Guard, Kern 1). Beide nutzen getrennte Arbeitsspeicher.
 */
#ifndef DVBT2_H
#define DVBT2_H
#include <stdint.h>
#include <stddef.h>

enum t2_mod  { T2_QPSK = 0, T2_16QAM, T2_64QAM, T2_256QAM };                 /* = PLP_MOD */
enum t2_rate { T2_R1_2 = 0, T2_R3_5, T2_R2_3, T2_R3_4, T2_R4_5, T2_R5_6 };    /* = PLP_COD */
enum t2_gi   { T2_GI_1_32 = 0, T2_GI_1_16, T2_GI_1_8, T2_GI_1_4, T2_GI_1_128, T2_GI_19_128, T2_GI_19_256 }; /* Tab. 22 */
enum t2_l1   { T2_L1_BPSK = 0, T2_L1_QPSK, T2_L1_16QAM, T2_L1_64QAM };         /* = L1_MOD */

extern const char *const t2_mod_name[4], *const t2_rate_name[6], *const t2_gi_name[7], *const t2_l1_name[4];

typedef struct {
    int bw10;              /* Kanalbandbreite × 10 MHz: 17, 50, 60, 70, 80, 100 */
    int fft;               /* 1024 … 32768 */
    int ext;               /* Extended-Carrier-Modus (8K/16K/32K) */
    int mod, rate, fec16k; /* fec16k: 1 = kurzer FECFRAME (16200) */
    int gi, pp, rot;       /* Guard (enum t2_gi), Pilotmuster 1…8, Konstellationsrotation */
    int l1mod;             /* Modulation L1-post */
    int ldata, nfec, nti, nt2;   /* 0 = automatisch: max. Datensymbole, max. FEC-Blöcke, min. TI-Blöcke, 2 Rahmen */
    int cell_id, network_id, t2_system_id, plp_group_id;
    uint32_t freq_hz;      /* L1-post FREQUENCY */
} t2_cfg_t;

/* abgeleitete Größen (nach t2_cfg_resolve) */
typedef struct {
    double T_us, fs, tf_s, ts_bps;        /* Elementarperiode, Abtastrate, Rahmendauer, TS-Nutzrate (188-Byte-Pakete) */
    int N, guard, ktotal, kext, np2, lf, ldata, fc;
    int cp2, cdata, nfc, cfc, ctot;
    int ncells, kbch, nbch, nldpc, eta;
    int nfec, nti, nfec_ti_max, nt2;
    int dl1pre, dl1post, dplp, ndummy;
    int l1post_info, npost, npunc_post, s2field1;
    int ifft_scale;                       /* 2^-S der Festkomma-IFFT */
} t2_info_t;

/* Prüft/ergänzt die Konfiguration (Tab. 46/58/67, TI-Speicher, 250 ms). 0 = gültig, sonst Text in err. */
int t2_cfg_resolve(t2_cfg_t *c, t2_info_t *inf, char *err, size_t errn);

typedef struct t2_tx t2_tx_t;
typedef struct t2_frame t2_frame_t;
typedef int (*t2_pkt_fn)(void *ctx, uint8_t *pkt188);    /* 1 = Paket (Sync 0x47), 0 = keins → Nullpaket */

/* rms: Ziel-Effektivwert der Datensymbole in int16-Einheiten (Normierung 5/√(27·Ktotal) wie EN 302 755 9.5) */
t2_tx_t *t2_tx_create(const t2_cfg_t *c, double rms);
void     t2_tx_free(t2_tx_t *t);
const t2_info_t *t2_tx_info(const t2_tx_t *t);
const char *t2_ifft_backend(const t2_tx_t *t);
int      t2_frame_samples(const t2_tx_t *t);              /* 2048 + LF·(N+GI) */

t2_frame_t *t2_frame_alloc(t2_tx_t *t);
void        t2_frame_free(t2_frame_t *f);
/* Kodiert den nächsten T2-Rahmen (Zellen nach Frequenz-Interleaver). Thread A. */
void t2_encode_frame(t2_tx_t *t, t2_pkt_fn fn, void *ctx, t2_frame_t *f);
int  t2_frame_index(const t2_frame_t *f);                 /* FRAME_IDX */
/* Thread B: P1 (2048 Samples) bzw. Symbol l (N+GI Samples, Guard zuerst) nach out (I,Q int16) */
void t2_render_p1(t2_tx_t *t, int16_t *out);
void t2_render_symbol(t2_tx_t *t, const t2_frame_t *f, int l, int16_t *out);

/* Zwischenstufen in Dateien PREFIX.{ldpc,lab,map,ti,frm,fi} (nur Prüfung, langsam) */
int  t2_set_dump(t2_tx_t *t, const char *prefix);
/* Laufzeitanteile Thread A in s: [0] BB [1] BCH [2] LDPC [3] Bit-IL [4] Map+Zell-IL [5] TI-Lesen [6] Rahmen+FI */
void t2_profile(t2_tx_t *t, int on, double pr[8]);
/* Selbsttest: schnelle Pfade (BCH, LDPC, Bit-IL, IFFT-Varianten) gegen bitweise Referenz; 0 = ok */
int  t2_selftest(int verbose);
/* IFFT-Mikrobenchmark (stderr, JSON): Radix-8 gegen fft16 */
void t2_bench_ifft(int N, double secs);

#endif
