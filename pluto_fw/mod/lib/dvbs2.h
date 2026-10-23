/*
 * dvbs2.h – DVB-S2 (ETSI EN 302 307-1 V1.4.1) Kodierkette für Broadcast-TS (CCM, SIS):
 * BBFRAME (Mode-Adaptation + Stream-Adaptation, BB-Scrambling), BCH, LDPC (normal 64800 / short 16200),
 * Bit-Interleaver, QPSK/8PSK/16APSK/32APSK, PL-Framing (PLHEADER, Pilots, PL-Scrambling mit Gold-Code n).
 * Schneller Pfad: gepackte Bytes, BCH mit 4-Byte-Tabellen, LDPC quasi-zyklisch (360-Bit-Rotationen),
 * Mapping + Scrambling über eine Tabelle. Bitweise Referenz (dvbs2_ref_*) nur für die Prüfung.
 * LDPC-Adressen in dvbs2_tables.h (aus der Norm extrahiert, tools/gen_s2_tables.py).
 */
#ifndef DVBS2_H
#define DVBS2_H
#include <stdint.h>
#include <stddef.h>

enum s2_mod { S2_QPSK = 0, S2_8PSK, S2_16APSK, S2_32APSK };
/* Coderaten-Index (MODCOD-Reihenfolge) */
enum s2_rate { R1_4 = 0, R1_3, R2_5, R1_2, R3_5, R2_3, R3_4, R4_5, R5_6, R8_9, R9_10, S2_NRATE };
enum s2_rolloff { S2_RO_035 = 0, S2_RO_025 = 1, S2_RO_020 = 2 };

extern const char *const s2_rate_name[S2_NRATE];
extern const char *const s2_mod_name[4];

typedef struct dvbs2 dvbs2_t;
/* Liefert ein TS-Paket (188 Byte, Sync 0x47). Rückgabe 1 = Paket, 0 = kein Paket (Aufrufer stopft dann selbst). */
typedef int (*dvbs2_pkt_fn)(void *ctx, uint8_t *pkt188);

/* mod/rate/short/pilots/rolloff/gold (PL-Scrambling-Code n, 0 … 262141), A = Amplitude |s| der PSK-Punkte
   (APSK: mittlere Energie A²). NULL bei unzulässiger Kombination (z. B. 8PSK 1/2, short 9/10). */
dvbs2_t *dvbs2_create(int mod, int rate, int short_frame, int pilots, int rolloff, int gold, int A);
void     dvbs2_free(dvbs2_t *s);
void     dvbs2_set_input(dvbs2_t *s, dvbs2_pkt_fn fn, void *ctx);

int    dvbs2_kbch(const dvbs2_t *s);
int    dvbs2_nbch(const dvbs2_t *s);
int    dvbs2_nldpc(const dvbs2_t *s);
int    dvbs2_dfl_bits(const dvbs2_t *s);          /* Kbch − 80 */
int    dvbs2_plframe_symbols(const dvbs2_t *s);   /* PLHEADER + XFECFRAME + Pilots */
double dvbs2_ts_bitrate(const dvbs2_t *s, double symbol_rate);   /* TS-Nutzrate (188-Byte-Pakete) */
const char *dvbs2_name(const dvbs2_t *s);
int    dvbs2_modcod(const dvbs2_t *s);
double dvbs2_peak(const dvbs2_t *s);              /* größter Punktbetrag / A */

/* Nächstes FECFRAME (gepackt, MSB zuerst, nldpc/8 Byte): BBHEADER+DATAFIELD (verwürfelt) | BCH | LDPC. */
void dvbs2_fecframe(dvbs2_t *s, uint8_t *cw);
/* Nächster PLFRAME: plframe_symbols() komplexe int16-Samples (I,Q) nach out. */
void dvbs2_frame(dvbs2_t *s, int16_t *out_iq);
/* Wie dvbs2_frame, aber Symbolzuordnung aus einem vorhandenen FECFRAME (für Tests). */
void dvbs2_map_frame(dvbs2_t *s, const uint8_t *cw, int16_t *out_iq);

/* Bitweise Referenz (Bits als 0/1-Bytes), nur für Selbsttests */
void dvbs2_ref_bch(const dvbs2_t *s, const uint8_t *in_bits, uint8_t *out_bits);   /* Kbch → Nbch */
void dvbs2_ref_ldpc(const dvbs2_t *s, const uint8_t *in_bits, uint8_t *out_bits);  /* Nbch → nldpc */

/* Zeitmessung je Stufe (Summe in Sekunden seit create), für --bench */
typedef struct { double bb, bch, ldpc, map; long frames; } dvbs2_prof_t;
void dvbs2_profile(dvbs2_t *s, int on, dvbs2_prof_t *out);

#endif
