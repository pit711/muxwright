/*
 * j83b.h – ITU-T J.83 Annex B (SCTE 07, US-Kabel) Kanalcodierung: MPEG-Framing (Paritäts-Prüfsumme statt Sync-Byte)
 * → RS(128,122) über GF(128) → Faltungsinterleaver (Level 2, I/J per Steuerwort) → Randomizer (GF(128)) →
 * FEC-Rahmen mit Sync-Trailer → Trellis-codierte 64-/256-QAM (Präcoder, 2× BCC 16 Zustände, Punktierung 4/5) → Labels.
 * Eigene Implementierung nach Rec. ITU-T J.83 (12/2007) B.4–B.5; gegen gr-dtv (catv_*) stufenweise geprüft.
 * Lizenz: MIT (siehe LICENSE).
 */
#ifndef J83B_H
#define J83B_H
#include <stdint.h>
#include <stddef.h>

#define J83B_SR64        5056941.0     /* Symbolrate 64-QAM (Tabelle B.3) */
#define J83B_SR256       5360537.0     /* Symbolrate 256-QAM */
#define J83B_MAX_SYM_PER_PKT 512       /* Obergrenze QAM-Symbole je TS-Paket (64-QAM ≈ 282) */

typedef struct {
    int qam, m;                 /* 64/256, Bits je Symbol 6/8 */
    int cw, I, J;               /* Steuerwort und Interleaver-Parameter */
    int nblk, N;                /* RS-Blöcke je Rahmen (60/88), Datensymbole je Rahmen (7680/11264) */
    /* MPEG-Framing */
    uint8_t chk_nib[374][16];   /* Beitrag (Halbbyte-Position, Wert) zum 8-Bit-Syndrom */
    uint8_t chk_fix[256];       /* Syndromrest → Prüfbyte */
    /* Byte → 7-Bit-Symbole */
    uint32_t bacc; int bn;
    /* RS */
    uint8_t rsbuf[122]; int rsn;
    /* Interleaver */
    uint8_t *ilmem; int ilD, ilrow;  /* Ring aus ilD Zyklen à I Symbole, aktueller Zyklus */
    /* Rahmen / Randomizer */
    int fpos; uint8_t rnd[88 * 128];
    /* Trellis */
    uint8_t g4[4]; int g4n;     /* 64-QAM: 4 Symbole je Gruppe */
    uint64_t tacc; int tn;      /* 256-QAM: Datenbits; */
    int grp;                    /* 256-QAM: Gruppe im Rahmen 0..2075 */
    uint8_t trail[6];           /* 64-QAM: 6 Trailer-Symbole; 256-QAM: 5 Sync-Bytes S0..S7 */
    int pstate, su, sv;         /* Präcoder (x | y<<1), BCC-Zustände */
    uint16_t pre[4][16][16];    /* X4 | Y4<<4 | neuer Zustand<<8 */
    uint16_t bcc[16][16];       /* 5 Ausgangsbits (Bit k = U_{k+1}) | neuer Zustand<<5 */
    uint32_t a64[2][128];       /* 64-QAM: 7-Bit-Symbol (1./2. A- bzw. B-Symbol) → Bitwort A_i (Bit i) */
    uint64_t a256[5][256];      /* 256-QAM: Byte k des 38-Bit-Worts (MSB zuerst; k = 4: letzte 6 Bit) → A | B<<19 */
    /* Mapper */
    int16_t re[256], im[256];
    uint32_t iq32[256];         /* re/im als ein Wort */
} j83b_enc_t;

/* qam 64|256, cw = Steuerwort 0..14 (gerade oder 1,3,5,7,9; nicht 11/13/15), A = Skalierung wie dvbc_map_init */
int    j83b_init(j83b_enc_t *e, int qam, int cw, int A);
void   j83b_free(j83b_enc_t *e);
/* ein TS-Paket (188 Byte, Byte 0 wird ignoriert) → QAM-Symbole (I/Q int16 interleaved); labels optional (Index 0..M-1) */
size_t j83b_packet(j83b_enc_t *e, const uint8_t *pkt, int16_t *iq, uint8_t *labels);
/* dasselbe in zwei Stufen (für zwei Threads; Vorder- und Hinterteil benutzen getrennte Zustandsfelder):
   j83b_front: Paket → 0..2 fertige Blöcke à J83B_BLK Byte (128 verwürfelte 7-Bit-Symbole + Rahmenende-Flag)
   j83b_back:  ein Block → QAM-Symbole (≤ J83B_MAX_SYM_PER_BLK) */
#define J83B_BLK 129
#define J83B_MAX_SYM_PER_BLK 176
int    j83b_front(j83b_enc_t *e, const uint8_t *pkt, uint8_t *blks);
size_t j83b_back(j83b_enc_t *e, const uint8_t *blk, int16_t *iq, uint8_t *labels);
double j83b_ts_rate(int qam, double sr);     /* Nutzrate in b/s (TS inkl. Sync-Byte-Position) */
int    j83b_cw_valid(int cw, int *I, int *J);

#endif
