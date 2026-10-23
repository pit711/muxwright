/* dvbs2_stage – Prüfwerkzeug für lib/dvbs2.c
 *   dvbs2_stage --mod M --rate R [--short 1] [--pilots 1] [--ro 0|1|2] [--gold N] [--A 8000] --stage bb|bch|ldpc|frame --frames N
 *       TS von stdin (fehlende Pakete → Nullpakete), Ausgabe nach stdout:
 *       bb/bch/ldpc: je FECFRAME die ersten Kbch/Nbch/nldpc Bits als gepackte Bytes (MSB zuerst); frame: int16 I/Q.
 *   dvbs2_stage --selftest [N]   schneller Pfad (BCH/LDPC gepackt) gegen bitweise Referenz, alle MODCODs, N Rahmen
 *   dvbs2_stage --bench SEK --mod M --rate R [...]   Durchsatz je Stufe (Nullpakete), ohne Ausgabe
 */
#include "../lib/dvbs2.h"
#include "../lib/dvbmod.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdint.h>

static int stdin_pkt(void *ctx, uint8_t *p)
{
    (void)ctx; size_t got = 0;
    while (got < 188) { ssize_t r = read(0, p + got, 188 - got); if (r <= 0) return 0; got += (size_t)r; }
    return 1;
}
static uint32_t rng = 12345;
static int rand_pkt(void *ctx, uint8_t *p)
{
    (void)ctx; p[0] = 0x47;
    for (int i = 1; i < 188; i++) { rng = rng * 1103515245u + 12345u; p[i] = (uint8_t)(rng >> 23); }
    return 1;
}

static int selftest(int nfr)
{
    int bad = 0, n = 0;
    for (int shortf = 0; shortf < 2; shortf++)
        for (int mod = 0; mod < 4; mod++)
            for (int rate = 0; rate < S2_NRATE; rate++) {
                dvbs2_t *s = dvbs2_create(mod, rate, shortf, 0, 0, 0, 8000);
                if (!s) continue;
                if (mod != 0) { dvbs2_free(s); continue; }   /* FEC hängt nicht von der Modulation ab */
                dvbs2_set_input(s, rand_pkt, NULL);
                int nl = dvbs2_nldpc(s), kb = dvbs2_kbch(s), nb = dvbs2_nbch(s);
                uint8_t *cw = malloc(nl / 8), *bits = malloc(nl), *ref = malloc(nl);
                for (int f = 0; f < nfr; f++) {
                    dvbs2_fecframe(s, cw);
                    for (int i = 0; i < nl; i++) bits[i] = (cw[i >> 3] >> (7 - (i & 7))) & 1;
                    dvbs2_ref_bch(s, bits, ref);
                    dvbs2_ref_ldpc(s, ref, ref);
                    int d = 0; for (int i = 0; i < nl; i++) d += bits[i] != ref[i];
                    int dbch = 0; for (int i = kb; i < nb; i++) dbch += bits[i] != ref[i];
                    if (d) { bad++; printf("FAIL %s Rahmen %d: %d Bit verschieden (BCH %d)\n", dvbs2_name(s), f, d, dbch); }
                    n++;
                }
                printf("%-22s kbch=%5d nbch=%5d nldpc=%5d: %s\n", dvbs2_name(s), kb, nb, nl, bad ? "?" : "ok");
                free(cw); free(bits); free(ref); dvbs2_free(s);
            }
    printf("selftest: %d Rahmen, %d Fehler → %s\n", n, bad, bad ? "FAIL" : "OK");
    return bad ? 1 : 0;
}

int main(int argc, char **argv)
{
    int mod = 0, rate = R3_4, pilots = 0, shortf = 0, A = 8000, ro = 0, gold = 0, frames = 1;
    double bench = 0; const char *stage = "frame";
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--selftest")) return selftest(i + 1 < argc ? atoi(argv[i + 1]) : 3);
        else if (!strcmp(argv[i], "--mod") && i + 1 < argc) mod = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--rate") && i + 1 < argc) rate = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--stage") && i + 1 < argc) stage = argv[++i];
        else if (!strcmp(argv[i], "--pilots") && i + 1 < argc) pilots = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--short") && i + 1 < argc) shortf = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--ro") && i + 1 < argc) ro = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--gold") && i + 1 < argc) gold = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--A") && i + 1 < argc) A = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--frames") && i + 1 < argc) frames = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--bench") && i + 1 < argc) bench = atof(argv[++i]);
        else { fprintf(stderr, "unbekannt: %s\n", argv[i]); return 2; }
    }
    dvbs2_t *s = dvbs2_create(mod, rate, shortf, pilots, ro, gold, A);
    if (!s) { fprintf(stderr, "ungültige Kombination\n"); return 2; }
    int nl = dvbs2_nldpc(s), nsym = dvbs2_plframe_symbols(s);
    uint8_t *cw = malloc(nl / 8);
    int16_t *iq = malloc((size_t)nsym * 4);
    if (bench > 0) {
        dvbs2_profile(s, 1, NULL);
        double t0 = dvb_now(), c0 = dvb_cpu_time(); long nf = 0;
        while (dvb_now() - t0 < bench) { dvbs2_frame(s, iq); nf++; }
        double cpu = dvb_cpu_time() - c0; dvbs2_prof_t p; dvbs2_profile(s, 0, &p);
        double fs = cpu / nf * 1e6;
        printf("{\"modcod\":\"%s\",\"frames\":%ld,\"us_per_frame\":%.1f,\"bb_us\":%.1f,\"bch_us\":%.1f,\"ldpc_us\":%.1f,\"map_us\":%.1f,"
               "\"symbols_per_frame\":%d,\"max_symbol_rate\":%.0f}\n",
               dvbs2_name(s), nf, fs, p.bb / nf * 1e6, p.bch / nf * 1e6, p.ldpc / nf * 1e6, p.map / nf * 1e6, nsym, nsym / (cpu / nf));
        return 0;
    }
    dvbs2_set_input(s, stdin_pkt, NULL);
    int nbits = !strcmp(stage, "bb") ? dvbs2_kbch(s) : !strcmp(stage, "bch") ? dvbs2_nbch(s) : nl;
    for (int f = 0; f < frames; f++) {
        if (!strcmp(stage, "frame")) { dvbs2_frame(s, iq); fwrite(iq, 4, (size_t)nsym, stdout); }
        else { dvbs2_fecframe(s, cw); fwrite(cw, 1, (size_t)nbits / 8, stdout); }
    }
    dvbs2_free(s); free(cw); free(iq);
    return 0;
}
