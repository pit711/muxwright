#!/usr/bin/env python3
"""Unabhängiger ISDB-T-Empfang einer isdbt_mod-IQ-Datei mit gr-isdbt (git-artes/gr-isdbt, GNU Radio 3.10):
ofdm_synchronization → tmcc_decoder → frequency_deinterleaver → time_deinterleaver → symbol_demapper
→ je Schicht bit_deinterleaver → viterbi_decoder → byte_deinterleaver → energy_descrambler → reed_solomon_dec.

Aufruf: isdbt_rx_grisdbt.py IQ_INT16 OUTPREFIX --mode 3 --guard 1/8 --layers 2 --a 4,1,4 --b 64,2,2
        (--a/--b: Konstellationsgröße, Coderate-Index 0..4 (1/2..7/8), Zeitverschachtelung I)
Ausgabe: OUTPREFIX_a.ts, OUTPREFIX_b.ts, Konstellation (Datenzellen nach Entzerrung) OUTPREFIX_const.c64,
TMCC-Ausgabe von gr-isdbt auf stdout."""
import argparse
from gnuradio import gr, blocks, isdbt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('iq'); ap.add_argument('out')
    ap.add_argument('--mode', type=int, default=3)
    ap.add_argument('--guard', default='1/8')
    ap.add_argument('--layers', type=int, default=2)
    ap.add_argument('--a', default='4,1,4'); ap.add_argument('--b', default='64,2,2')
    ap.add_argument('--const-symbols', type=int, default=0)
    ap.add_argument('--max-items', type=int, default=0, help='max_noutput_items (0 = GR-Standard)')
    ap.add_argument('--only', choices=['a', 'b'], help='nur diese Schicht dekodieren. Nötig bei 2 Schichten: der gr-isdbt-'
                    'Viterbi hält Pfadmetriken in statischen Klassenmitgliedern – zwei Instanzen in einem Graphen stören sich')
    ap.add_argument('--const-pre-ti', action='store_true', help='Konstellation vor der Zeit-Entschachtelung')
    ap.add_argument('--dump-vit', action='store_true', help='Viterbi-Ausgang je Schicht zusätzlich speichern')
    a = ap.parse_args()
    num, den = a.guard.split('/')
    cp = float(num) / float(den)
    mode = a.mode
    cps = 96 * 2 ** (mode - 1)
    total_data = 13 * cps
    ca, ra, ia = [int(x) for x in a.a.split(',')]
    cb, rb, ib = [int(x) for x in a.b.split(',')]
    if a.layers == 1:
        sa, sb = 13, 0
    else:
        sa, sb = 1, 12
    tb = gr.top_block()
    src = blocks.file_source(gr.sizeof_short, a.iq, False)
    s2c = blocks.interleaved_short_to_complex(False, False, 32768.0)
    sync = isdbt.ofdm_synchronization(mode, cp, False)
    tmcc = isdbt.tmcc_decoder(mode, True)
    fdi = isdbt.frequency_deinterleaver(a.layers == 2, mode)
    tdi = isdbt.time_deinterleaver(mode, sa, ia, sb, ib if sb else 0, 0, 0)
    dem = isdbt.symbol_demapper(mode, sa, ca, sb, cb if sb else 64, 0, 64)
    tb.connect(src, s2c, sync, tmcc, fdi)
    # Symbolzähler: je Symbol der erste Träger nach Synchronisation (Anzahl = Dateigröße/8)
    K = 13 * 108 * 2 ** (mode - 1) + 1
    tb.connect(sync, blocks.vector_to_stream(gr.sizeof_gr_complex, K), blocks.keep_one_in_n(gr.sizeof_gr_complex, K),
               blocks.file_sink(gr.sizeof_gr_complex, a.out + '_symcount.c64'))
    if a.const_symbols > 0:
        # Nur Konstellation: entzerrte Datenzellen (nach Zeit-Entschachtelung, Segmentnummernfolge), dann Ende.
        # (Ein head()-Block beendet den ganzen Graphen – darum getrennter Lauf.)
        v2s = blocks.vector_to_stream(gr.sizeof_gr_complex, total_data)
        head = blocks.head(gr.sizeof_gr_complex, total_data * a.const_symbols)
        if not a.const_pre_ti: tb.connect(fdi, tdi)
        tb.connect(fdi if a.const_pre_ti else tdi, v2s, head, blocks.file_sink(gr.sizeof_gr_complex, a.out + '_const.c64'))
        tb.run()
        return
    tb.connect(fdi, tdi, dem)
    layers = [('a', sa, ca, ra)] + ([('b', sb, cb, rb)] if sb else [])
    for port, (name, segs, csize, rate) in enumerate(layers):
        if a.only and name != a.only:
            tb.connect((dem, port), blocks.null_sink(gr.sizeof_char * segs * cps))
            continue
        bdi = isdbt.bit_deinterleaver(mode, segs, csize)
        vit = isdbt.viterbi_decoder(csize, rate)
        byd = isdbt.byte_deinterleaver()
        eds = isdbt.energy_descrambler()
        rs = isdbt.reed_solomon_dec_isdbt()
        v2 = blocks.vector_to_stream(gr.sizeof_char, 188)
        tb.connect((dem, port), bdi, vit, byd, eds, rs, v2, blocks.file_sink(gr.sizeof_char, a.out + '_%s.ts' % name))
        if a.dump_vit:
            tb.connect(vit, blocks.file_sink(gr.sizeof_char, a.out + '_%s_vit.bin' % name))
            tb.connect(bdi, blocks.file_sink(gr.sizeof_char, a.out + '_%s_bdi.bin' % name))
    if len(layers) == 1:
        # Ausgang B des Demappers bleibt offen (gr-isdbt prüft output_items.size())
        pass
    if a.max_items: tb.run(a.max_items)
    else: tb.run()


if __name__ == '__main__':
    main()
