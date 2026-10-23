#!/usr/bin/env python3
"""Referenzsender mit gr-dtv (GNU Radio 3.10, GPL-3.0): TS → IQ (complex64) zum
Sample-genauen Vergleich mit dvbt_mod (tests/iq_compare.py).
  python3 dvbt_tx_grdtv.py --ts in.ts --mode 8k --const 64qam --rate 2/3 --guard 1/32 -o ref.cf32
"""
import argparse
from gnuradio import gr, blocks, fft, dtv, digital
from gnuradio.fft import window

MODE = {"2k": dtv.T2k, "8k": dtv.T8k}
CONST = {"qpsk": dtv.MOD_QPSK, "16qam": dtv.MOD_16QAM, "64qam": dtv.MOD_64QAM}
RATE = {"1/2": dtv.C1_2, "2/3": dtv.C2_3, "3/4": dtv.C3_4, "5/6": dtv.C5_6, "7/8": dtv.C7_8}
GUARD = {"1/32": dtv.GI_1_32, "1/16": dtv.GI_1_16, "1/8": dtv.GI_1_8, "1/4": dtv.GI_1_4}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ts", required=True); ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--mode", default="8k"); ap.add_argument("--const", default="64qam")
    ap.add_argument("--rate", default="2/3"); ap.add_argument("--guard", default="1/32")
    a = ap.parse_args()
    f = 4 if a.mode == "8k" else 1
    nfft, carriers, ncells = 2048 * f, 1705 * f + (3 * f - 3), 1512 * f   # 2k: 1705, 8k: 6817
    carriers = 6817 if f == 4 else 1705
    gi = nfft // {"1/32": 32, "1/16": 16, "1/8": 8, "1/4": 4}[a.guard]
    tb = gr.top_block()
    src = blocks.file_source(gr.sizeof_char, a.ts, False)
    chain = [src,
             dtv.dvbt_energy_dispersal(1 * f),
             dtv.dvbt_reed_solomon_enc(2, 8, 0x11d, 255, 239, 8, 51, 8 * f),
             dtv.dvbt_convolutional_interleaver(136 * f, 12, 17),
             dtv.dvbt_inner_coder(1, ncells, CONST[a.const], dtv.NH, RATE[a.rate]),
             dtv.dvbt_bit_inner_interleaver(ncells, CONST[a.const], dtv.NH, MODE[a.mode]),
             dtv.dvbt_symbol_inner_interleaver(ncells, MODE[a.mode], 1),
             dtv.dvbt_map(ncells, CONST[a.const], dtv.NH, MODE[a.mode], 1),
             dtv.dvbt_reference_signals(gr.sizeof_gr_complex, ncells, nfft, CONST[a.const], dtv.NH,
                                        RATE[a.rate], RATE[a.rate], GUARD[a.guard], MODE[a.mode], 0, 0),
             # gr-dtv 3.10: dvbt_reference_signals enthält bereits die IFFT (Ausgang Zeitbereich, nfft Samples)
             digital.ofdm_cyclic_prefixer(nfft, nfft + gi, 0, ""),
             blocks.file_sink(gr.sizeof_gr_complex, a.out, False)]
    tb.connect(*chain)
    tb.run()

if __name__ == "__main__":
    main()
