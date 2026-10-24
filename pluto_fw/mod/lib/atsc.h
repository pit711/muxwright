/*
 * atsc.h – ATSC 1.0 (A/53 Part 2, 8-VSB) Bausteine für atsc_mod.
 * Copyright (c) 2026 DAB-Lab. Lizenz: MIT (siehe LICENSE). Eigene Implementierung nach ATSC A/53 Part 2;
 * Randomizer-/Trellis-/Feldsync-Tabellen gegen gr-dtv (GPL-3.0) gegengeprüft, kein Code übernommen.
 */
#ifndef ATSC_H
#define ATSC_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define ATSC_SYMBOL_RATE   (4.5e6 / 286.0 * 684.0)   /* 10 762 237,76 Bd */
#define ATSC_SEG_SYMS      832                         /* Symbole je Segment (4 Sync + 828 Daten) */
#define ATSC_DATA_SEGS     312                         /* Datensegmente je Feld */
#define ATSC_RS_LEN        207                         /* RS(207,187) */
#define ATSC_DATA_LEN      187                         /* TS-Paket ohne Sync-Byte */
#define ATSC_NCODERS       12
#define ATSC_GROUP_SEGS    12                          /* Trellis-Verschachtelung über 12 Segmente */
#define ATSC_MAX_SEGS_OUT  (ATSC_GROUP_SEGS + 1)       /* + Feldsync */

/* ---------------------------------------------------------------- Kanalcodierung bis zum Symbolindex 0..7 */
typedef struct {
    uint8_t  rand_seq[ATSC_DATA_SEGS * ATSC_DATA_LEN];  /* Randomizer-XOR-Folge eines Felds */
    uint32_t rs_tab[256][5];                            /* fb·g(x) gepackt (20 Paritätsbytes in 5 Wörtern) */
    uint8_t  il_buf[16384];                             /* Faltungs-Byte-Interleaver B=52, M=4 (Ring) */
    uint32_t il_t;                                      /* Bytezähler */
    uint16_t tt[8][256];                                /* Trellis: (Zustand, Byte) → 4 Symbole (12 Bit) | Zustand<<12 */
    uint8_t  tstate[ATSC_NCODERS];                      /* Precoder + Faltungscoder (3 Bit) je Encoder */
    uint8_t  grp[ATSC_GROUP_SEGS * ATSC_RS_LEN];        /* verschachtelte Segmente der laufenden 12er-Gruppe */
    int      grp_n;                                     /* Segmente in grp */
    int      seg;                                       /* Datensegment im Feld 0..311 (nächstes) */
    int      field;                                     /* 0 = Feld 1, 1 = Feld 2 (PN63 Mitte invertiert) */
    uint8_t  last12[12];                                /* letzte 12 Symbole des Vorfelds (Feldsync-Ende) */
    uint8_t  fsync[2][ATSC_SEG_SYMS];                   /* Feldsync-Segmente (ohne last12) */
} atsc_enc_t;

void atsc_enc_init(atsc_enc_t *e);
/* ein TS-Paket (188 Byte, Sync 0x47 an [0]) rein; gibt die Zahl fertiger Segmente zurück (0, 12 oder 13 inkl.
   Feldsync) und schreibt deren Symbolindizes (0..7 → Pegel 2·i−7) nach seg_out (Platz für 13·832). */
int  atsc_enc_packet(atsc_enc_t *e, const uint8_t *ts188, uint8_t *seg_out);
/* 1 = vor dem nächsten Paket beginnt ein neues Feld (keine angefangene Gruppe) */
static inline int atsc_enc_field_boundary(const atsc_enc_t *e) { return e->seg == 0 && e->grp_n == 0; }

/* ---------------------------------------------------------------- VSB-Formung (1 Sample/Symbol, komplex)
   Symbole (+ Pilot 1,25) werden um −fs/4 gedreht (Pilot auf −2,690559 MHz = 309,44 kHz über Kanalunterkante,
   LO = Kanalmitte) und mit einem reellen RRC-Tiefpass (Halbband-Nyquist, α = 0,1152 bezogen auf fs/2,
   Kaiser-gefenstert) gefiltert. Da die Drehung abwechselnd rein reelle und rein imaginäre Werte liefert,
   kostet jedes Ausgabesample (I+Q) genau ntaps Multiplikationen. */
typedef struct {
    int      ntaps;
    int16_t *h;                 /* Q15, größter Tap 32767 */
    int16_t *he, *ho;           /* gerade/ungerade Taps, je 4·ngroups, mit Nullen aufgefüllt (NEON) */
    int      ngroups;
    int16_t *buf;               /* Arbeitspuffer für atsc_vsb_run: [ntaps-1 Historie | Block] gedrehte reelle Folge w[m] */
    int16_t *hist;              /* letzte ntaps-1 Werte von w */
    int      cap;               /* max. Blocklänge */
    int16_t  wtab[4][8];        /* w = c[m mod 4]·(2i−7+1,25)·A, c = +1,−1,−1,+1 */
    double   A;                 /* Amplitude je Pegeleinheit (int16) */
    double   rms;               /* erwarteter Ausgangs-RMS (int16) */
    double   peak_w;            /* max |w| */
} atsc_vsb_t;

int    atsc_vsb_init(atsc_vsb_t *v, int ntaps, double kaiser_beta, double rms_target, int max_block);
void   atsc_vsb_free(atsc_vsb_t *v);
/* n Symbolindizes (n Vielfaches von 8) → n komplexe Samples (int16 I/Q interleaved) */
size_t atsc_vsb_run(atsc_vsb_t *v, const uint8_t *sym, size_t n, int16_t *out_iq);
/* zerlegt für zwei Threads: map (sequentiell, schreibt Historie + Block nach wbuf) und filter (Ausgänge i0..i1,
   Vielfache von 8, beliebig parallel auf demselben wbuf); out_iq zeigt auf den Blockanfang. */
static inline size_t atsc_vsb_wbuf_len(const atsc_vsb_t *v, int max_block) { return (size_t)(v->ntaps - 1 + max_block + 32); }
void   atsc_vsb_map(atsc_vsb_t *v, const uint8_t *sym, size_t n, int16_t *wbuf);
void   atsc_vsb_filter(const atsc_vsb_t *v, const int16_t *wbuf, size_t i0, size_t i1, int16_t *out_iq);
const char *atsc_vsb_backend(void);

#ifdef __cplusplus
}
#endif
#endif
