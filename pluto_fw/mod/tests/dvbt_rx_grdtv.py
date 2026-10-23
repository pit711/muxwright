#!/usr/bin/env python3
"""DVB-T-Prüfempfänger mit gr-dtv (GNU Radio 3.10, GPL-3.0) – ohne GUI.
IQ int16 interleaved → gr-dtv-Empfangskette (OFDM-Akquisition, FFT, Referenzsignale/TPS, Demapper,
Deinterleaver, Viterbi, Faltungs-Deinterleaver, RS-Decoder, Entwürfelung) → TS-Datei.

  python3 dvbt_rx_grdtv.py --iq out.iq --mode 8k --const 64qam --rate 2/3 --guard 1/32 -o rx.ts
"""
import argparse, sys, time
from gnuradio import gr, blocks, fft, dtv
from gnuradio.fft import window

MODE = {"2k": dtv.T2k, "8k": dtv.T8k}
CONST = {"qpsk": dtv.MOD_QPSK, "16qam": dtv.MOD_16QAM, "64qam": dtv.MOD_64QAM}
RATE = {"1/2": dtv.C1_2, "2/3": dtv.C2_3, "3/4": dtv.C3_4, "5/6": dtv.C5_6, "7/8": dtv.C7_8}
GUARD = {"1/32": dtv.GI_1_32, "1/16": dtv.GI_1_16, "1/8": dtv.GI_1_8, "1/4": dtv.GI_1_4}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iq", required=True); ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--mode", default="8k"); ap.add_argument("--const", default="64qam")
    ap.add_argument("--rate", default="2/3"); ap.add_argument("--guard", default="1/32")
    ap.add_argument("--snr", type=float, default=30.0)
    a = ap.parse_args()
    factor = 4 if a.mode == "8k" else 1
    nfft, carriers, ncells = 2048 * factor, 1705 if factor == 1 else 6817, 1512 * factor
    gi = nfft // {"1/32": 32, "1/16": 16, "1/8": 8, "1/4": 4}[a.guard]
    tb = gr.top_block()
    src = blocks.file_source(gr.sizeof_short, a.iq, False)
    s2c = blocks.interleaved_short_to_complex(False, False, 32768.0)
    acq = dtv.dvbt_ofdm_sym_acquisition(1, nfft, carriers, gi, a.snr)
    fftb = fft.fft_vcc(nfft, True, window.rectangular(nfft), True, 1)
    ref = dtv.dvbt_demod_reference_signals(gr.sizeof_gr_complex, nfft, ncells, CONST[a.const], dtv.NH,
                                           RATE[a.rate], RATE[a.rate], GUARD[a.guard], MODE[a.mode], 0, 0)
    demap = dtv.dvbt_demap(ncells, CONST[a.const], dtv.NH, MODE[a.mode], 1.0)
    symdeint = dtv.dvbt_symbol_inner_interleaver(ncells, MODE[a.mode], 0)
    bitdeint = dtv.dvbt_bit_inner_deinterleaver(ncells, CONST[a.const], dtv.NH, MODE[a.mode])
    v2s = blocks.vector_to_stream(gr.sizeof_char, ncells)
    vit = dtv.dvbt_viterbi_decoder(CONST[a.const], dtv.NH, RATE[a.rate], 768)
    convdeint = dtv.dvbt_convolutional_deinterleaver(136, 12, 17)
    rsdec = dtv.dvbt_reed_solomon_dec(2, 8, 0x11d, 255, 239, 8, 51, 8)
    descr = dtv.dvbt_energy_descramble(8)
    sink = blocks.file_sink(gr.sizeof_char, a.out, False)
    tb.connect(src, s2c, acq, fftb, ref, demap, symdeint, bitdeint, v2s, vit, convdeint, rsdec, descr, sink)
    t0 = time.time(); tb.run(); print(f"gr-dtv rx done in {time.time() - t0:.1f} s", file=sys.stderr)

if __name__ == "__main__":
    main()
