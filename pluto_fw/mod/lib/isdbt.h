/*
 * isdbt.h – ISDB-T (ARIB STD-B31 / ABNT NBR 15601-1) Senderbausteine: Schichtcodierer (TSP → Datenzellen)
 * und OFDM-Rahmenaufbau (Piloten, TMCC, AC1, Frequenzverschachtelung). Nur synchrone Modulation
 * (QPSK/16QAM/64QAM), Schicht A + optional B, 6-MHz-Kanal (Abtastrate 512/63 MHz).
 *
 * Copyright (c) 2026 DAB-Lab. Lizenz: MIT (siehe LICENSE). Eigene Implementierung; Normtabellen und
 * Verzögerungs-/Ausrichtungskonventionen gegen den Empfänger gr-isdbt (GPL-3.0) gegengeprüft, kein Code übernommen.
 */
#ifndef ISDBT_H
#define ISDBT_H
#include "dvbmod.h"

#ifdef __cplusplus
extern "C" {
#endif

enum isdbt_mod { ISDBT_QPSK = 0, ISDBT_16QAM = 1, ISDBT_64QAM = 2 };

typedef struct {
    int mod;      /* enum isdbt_mod */
    int cr;       /* enum dvb_coderate */
    int ti;       /* Zeitverschachtelungslänge I (Modus 3: 0,1,2,4; Modus 2: 0,2,4,8; Modus 1: 0,4,8,16) */
    int nseg;     /* Segmente der Schicht */
} isdbt_layer_cfg_t;

typedef struct {
    int mode;     /* 1, 2, 3 */
    int guard;    /* enum dvbt_guard: 0 = 1/32, 1 = 1/16, 2 = 1/8, 3 = 1/4 */
    int nlayers;  /* 1 (A = 13 Seg.) oder 2 (A = 1 Seg. Teilempfang, B = 12 Seg.) */
    int partial;  /* Teilempfangs-Flag (Segment 0 = 1seg) */
    isdbt_layer_cfg_t L[2];
    int seg_type; /* TMCC B17..B19 (Standard 0 = synchron) */
} isdbt_cfg_t;

#define ISDBT_FS (512e6 / 63.0)      /* Abtastrate 6-MHz-Kanal */
#define ISDBT_SYMS_PER_FRAME 204
#define ISDBT_BIT_SYMS 2             /* Bitverschachtelung inkl. Laufzeitausgleich (Sender+Empfänger) = 2 Symbole */

int    isdbt_cps(int mode);                           /* Datenträger je Segment: 96·2^(Modus−1) */
int    isdbt_nfft(int mode);                          /* 2048 / 4096 / 8192 */
int    isdbt_bits(int mod);                           /* 2, 4, 6 */
int    isdbt_ti_code(int mode, int I);                /* TMCC-Code 0..3, −1 = ungültig */
int    isdbt_ti_adjust(int I);                        /* Laufzeitausgleich Zeitverschachtelung in Symbolen */
int    isdbt_layer_bytes_per_symbol(const isdbt_cfg_t *c, int l);  /* = TSP je Rahmen; −1 wenn nicht ganzzahlig */
double isdbt_layer_bitrate(const isdbt_cfg_t *c, int l);           /* nutzbare TS-Rate (188-Byte-Pakete) in bit/s */
int    isdbt_cfg_check(const isdbt_cfg_t *c, char *err, int errlen);

/* ------------------------------------------------------------ Schichtcodierer */
/* Lieferfunktion: genau ein 188-Byte-TS-Paket (Sync 0x47) für Schicht l */
typedef void (*isdbt_pkt_fn)(void *ctx, int l, uint8_t *pkt);

typedef struct {
    int l, m, cells, bps, N, cps, nseg, I, D, Gm, q;
    uint8_t *prbs;            /* N·204 Bytes Verwürfelungsmuster eines Rahmens (Sync-Platz = 0) */
    int frame_pos;            /* Einheit im Rahmen (Energieverwürfelung) */
    dvb_convint_t ci;
    uint8_t *bytes; int nbytes;   /* Eingang Faltungscoder */
    dvb_convcode_t cc;
    uint8_t *punct;
    uint8_t *bring, *wtmp; uint32_t bmask, bt; int X; int dl[6];   /* Bitverschachtelung */
    uint16_t *store_of;       /* natürlicher Zellindex → Speicherindex (nach m_i gruppiert) */
    uint8_t *ti; int tdepth, tpos;  /* Zeitverschachtelung: Ring aus Symbolzeilen */
    uint8_t *out;             /* Zellen des aktuellen Symbols im Speicherindex (m-Bit-Wörter) */
    uint64_t packets;
    double t_fec, t_bit, t_ti;
} isdbt_layer_t;

int  isdbt_layer_init(isdbt_layer_t *L, const isdbt_cfg_t *c, int l, int bit_syms);
void isdbt_layer_free(isdbt_layer_t *L);
/* erzeugt die Zellen des nächsten OFDM-Symbols in L->out */
void isdbt_layer_symbol(isdbt_layer_t *L, isdbt_pkt_fn fn, void *ctx, int profile);

/* ------------------------------------------------------------ OFDM-Rahmen */
typedef struct {
    isdbt_cfg_t cfg;
    int nfft, K, guard_len, cps, ndata, nlayers;
    uint8_t  *wk;
    uint16_t *data_bin[4];    /* globaler Speicherindex (A, dann B) → FFT-Bin, je (Symbol mod 4) */
    uint16_t *pil_bin[4]; uint32_t *pil_iq[4]; int npil[4];
    uint16_t  tmcc_bin[52]; int8_t tmcc_sign[52]; int ntmcc;
    uint16_t  ac_bin[104];  int8_t ac_sign[104];  int nac;
    uint8_t   tmcc_bits[204];
    uint32_t  map_iq[2][64];
    int       lay_off[2], lay_cells[2];
    int       A, frame, symbol;
    double    mean_cell_power;
} isdbt_frame_t;

int  isdbt_frame_init(isdbt_frame_t *f, const isdbt_cfg_t *c, isdbt_layer_t *layers, int A);
void isdbt_frame_free(isdbt_frame_t *f);
void isdbt_tmcc_generate(const isdbt_cfg_t *c, int odd_frame, uint8_t *bits204);
/* baut den Frequenzbereichsvektor (nfft Bins, I/Q int16) aus layers[l].out und schaltet weiter */
void isdbt_frame_build(isdbt_frame_t *f, isdbt_layer_t *layers, int16_t *iq);

#ifdef __cplusplus
}
#endif
#endif
