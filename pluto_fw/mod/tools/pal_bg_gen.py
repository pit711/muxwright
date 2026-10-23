#!/usr/bin/env python3
"""PAL-B/G-Analog-TV-Generator (CCIR 625/50, negative AM-Restseitenband + FM-Ton) für den ADALM-Pluto.

Erzeugt eine nahtlose Schleife aus 4 Vollbildern (PAL-8-Halbbild-Sequenz, 160 ms): alle Träger
(Farbhilfsträger 4,43361875 MHz, Ton 5,5 MHz, Tonfrequenz, LO-Versatz) haben darin ganzzahlige Periodenzahlen.
Ausgabe: komplexes int16 (I/Q interleaved) bei 8 MS/s, Bildträger bei −OFFSET (Standard −2,25 MHz), d. h.
TX_LO = Bildträger + 2,25 MHz.

Kette: Bild (ffmpeg pal75bars + Text, 720×576 RGB) → Y/U/V → PAL-Kodierung (Burst ±135°, V-Schalter) mit
Zeilen-/Halbbildsynchronisation nach ITU-R BT.470 bei 32 MS/s → Hüllkurve negativ (Synchronboden 100 %,
Austast 73 %, Weiß 10 %) → Restseitenbandfilter im Frequenzbereich (−0,75 … +5,0 MHz, Kosinusflanken) →
Verschiebung um −OFFSET → Bandbegrenzung auf ±4 MHz → 8 MS/s → FM-Tonträger +5,5 MHz (Hub ±50 kHz, 1 kHz,
50-µs-Preemphase, −13 dB) → int16.

  python3 pal_bg_gen.py -o pal_k36.iq [--text "DABLAB Test"] [--offset 2.25e6] [--level 0.9]
"""
import argparse, subprocess, sys, json
import numpy as np

FS_HI = 32e6            # Erzeugungsrate (Farbträger 4,43 MHz braucht > 8,9 MS/s reell)
FS = 8e6                # Ausgaberate
LINE = 64e-6
NL = 625
FSC = 4433618.75
FRAMES = 4

def make_image(text, w=720, h=576):
    vf = ("drawbox=x=0:y=400:w=720:h=176:color=black:t=fill,"
          f"drawtext=text='{text}':fontcolor=white:fontsize=56:x=(w-text_w)/2:y=430,"
          "drawtext=text='PAL B/G K36 591.25 MHz':fontcolor=yellow:fontsize=30:x=(w-text_w)/2:y=505")
    cmd = ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"pal75bars=size={w}x{h}:rate=25", "-vf", vf,
           "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    try:
        raw = subprocess.run(cmd, check=True, capture_output=True).stdout
        img = np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(np.float64) / 255.0
    except Exception as ex:  # Fallback: 75-%-Farbbalken ohne Text
        print("ffmpeg/drawtext nicht verfügbar:", ex, file=sys.stderr)
        bars = np.array([[1, 1, 1], [1, 1, 0], [0, 1, 1], [0, 1, 0], [1, 0, 1], [1, 0, 0], [0, 0, 1], [0, 0, 0]]) * 0.75
        bars[0] = 1.0
        img = np.repeat(np.repeat(bars[None, :, :], h, 0), w // 8, 1)
    return img

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--text", default="DABLAB Test")
    ap.add_argument("--offset", type=float, default=2.25e6, help="Bildträger liegt bei −offset im Basisband")
    ap.add_argument("--level", type=float, default=0.9, help="Spitzenwert relativ zu Vollaussteuerung")
    ap.add_argument("--sound-db", type=float, default=-13.0)
    ap.add_argument("--dev", type=float, default=50e3)
    ap.add_argument("--tone", type=float, default=1000.0)
    a = ap.parse_args()

    spl = int(round(LINE * FS_HI))                 # 2048 Samples je Zeile
    half = spl // 2
    us = FS_HI * 1e-6
    N = FRAMES * NL * spl                          # 5 120 000
    t = np.arange(N) / FS_HI

    # ---------------- Bildinhalt → Y, U, V je Bildzeile (576 aktive Zeilen, 52 µs aktiv)
    rgb = make_image(a.text)
    R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    Y = 0.299 * R + 0.587 * G + 0.114 * B
    U = 0.493 * (B - Y); V = 0.877 * (R - Y)
    act0, act_len = int(round(10.5 * us)), int(round(52.0 * us))
    xs = np.linspace(0, 719, act_len)              # 720 Pixel (702 aktiv) auf 52 µs
    def row_interp(M, r):
        return np.interp(xs, np.arange(720), M[r])

    v = np.zeros(N)                                # Videosignal in Volt: Sync −0,3, Austast 0, Weiß 0,7
    sync, eq, broad = int(round(4.7 * us)), int(round(2.35 * us)), int(round(27.3 * us))
    burst0, burst_len = int(round(5.6 * us)), int(round(2.25 * us))
    vswitch = np.zeros(N)                          # ±1 je Zeile (PAL-Schalter)
    burst_mask = np.zeros(N)
    active_mask = np.zeros(N)
    Yb = np.zeros(N); Ub = np.zeros(N); Vb = np.zeros(N)

    def pulses_for_line(L):
        """Synchronimpulse einer Zeile L (1..625) als Liste (start, länge) in Samples relativ zum Zeilenanfang."""
        # Halbzeilen-Positionen: Vorausgleich, Hauptimpulse (breit), Nachausgleich (BT.470)
        def kind(pos2):  # pos2 = 2*(Zeile-1) + Halbzeile (0/1), Zeilen 1..625 → 0..1249
            p = pos2 / 2 + 1
            if 623.5 <= p or p < 1.0: return "eq" if (p >= 623.5) else None
            if 1.0 <= p < 3.5: return "broad"
            if 3.5 <= p < 6.0: return "eq"
            if 311.0 <= p < 313.5: return "eq"
            if 313.5 <= p < 316.0: return "broad"
            if 316.0 <= p < 318.5: return "eq"
            return None
        out = []
        for h2 in (0, 1):
            k = kind(2 * (L - 1) + h2)
            if k == "eq": out.append((h2 * half, eq))
            elif k == "broad": out.append((h2 * half, broad))
            elif h2 == 0: out.append((0, sync))
        return out

    for f in range(FRAMES):
        for L in range(1, NL + 1):
            base = (f * NL + L - 1) * spl
            for s0, ln in pulses_for_line(L):
                v[base + s0: base + s0 + ln] = -0.3
            sw = 1.0 if ((f * NL + L) % 2) else -1.0
            vswitch[base: base + spl] = sw
            in_vsync = (L <= 6) or (310 <= L <= 318) or (L >= 623)
            if not in_vsync:
                burst_mask[base + burst0: base + burst0 + burst_len] = 1.0
            # aktive Zeilen: Halbbild 1 Zeilen 24..310, Halbbild 2 Zeilen 336..622
            if 24 <= L <= 310: r = 2 * (L - 24)
            elif 336 <= L <= 622: r = 2 * (L - 336) + 1
            else: r = None
            if r is not None and r < 576:
                sl = slice(base + act0, base + act0 + act_len)
                Yb[sl] = row_interp(Y, r); Ub[sl] = row_interp(U, r); Vb[sl] = row_interp(V, r)
                active_mask[sl] = 1.0

    # Bandbegrenzung von Y (5 MHz) und Chroma (1,3 MHz) per FFT (periodisch → nahtlos)
    f_hi = np.fft.fftfreq(N, 1 / FS_HI)
    def lp(x, fc, tr):
        X = np.fft.fft(x)
        H = np.clip((fc + tr / 2 - np.abs(f_hi)) / tr, 0, 1)
        H = 0.5 - 0.5 * np.cos(np.pi * H)
        return np.real(np.fft.ifft(X * H))
    Yb = lp(Yb, 5.0e6, 0.5e6); Ub = lp(Ub, 1.3e6, 0.6e6); Vb = lp(Vb, 1.3e6, 0.6e6)
    am = lp(active_mask, 2.5e6, 1e6)                # weiche Ränder der aktiven Zeile
    ph = 2 * np.pi * FSC * t
    chroma = Ub * np.sin(ph) + vswitch * Vb * np.cos(ph)
    burst = 0.15 * (-np.sin(ph) + vswitch * np.cos(ph)) / np.sqrt(2) * lp(burst_mask, 2e6, 1e6)
    v = v + (Yb * 0.7 + chroma * 0.7) * am + burst
    # Hüllkurve negativ: −0,3 V → 100 %, 0 V → 73 %, 0,7 V → 10 %
    env = np.clip(0.73 - 0.9 * v, 0.05, 1.0)
    del Yb, Ub, Vb, chroma, burst, am, active_mask, burst_mask, vswitch

    # Restseitenband: Durchlass −0,75 … +5,0 MHz um den Bildträger (Basisband 0)
    X = np.fft.fft(env)
    def edge(x, a0, a1):  # 0 bei a0, 1 bei a1 (Kosinus)
        y = np.clip((x - a0) / (a1 - a0), 0, 1); return 0.5 - 0.5 * np.cos(np.pi * y)
    H = edge(f_hi, -1.25e6, -0.75e6) * (1 - edge(f_hi, 5.0e6, 5.2e6))
    X = X * H
    # Verschiebung um −offset (ganzzahlige Bins) und Schnitt auf ±4 MHz
    shift = int(round(a.offset * N / FS_HI))
    X = np.roll(X, -shift)
    Nout = int(N * FS / FS_HI)
    keep = np.concatenate([X[: Nout // 2], X[-Nout // 2:]])
    fo = np.fft.fftfreq(Nout, 1 / FS)
    keep *= (1 - edge(np.abs(fo), 3.85e6, 4.0e6))
    vid = np.fft.ifft(keep) * (Nout / N)
    # Ton: FM 5,5 MHz über dem Bildträger → +5,5e6 − offset; 1 kHz, Preemphase 50 µs (Betrag bei 1 kHz)
    to = np.arange(Nout) / FS
    pre = abs(1 + 1j * 2 * np.pi * a.tone * 50e-6)
    beta = a.dev * pre / a.tone
    fsnd = 5.5e6 - a.offset
    snd = 10 ** (a.sound_db / 20) * np.exp(1j * (2 * np.pi * fsnd * to + beta * np.sin(2 * np.pi * a.tone * to)))
    s = vid + snd
    peak = np.max(np.abs(s))
    s = s / peak * a.level * 32767
    iq = np.empty(2 * Nout, np.int16)
    iq[0::2] = np.clip(np.round(s.real), -32768, 32767); iq[1::2] = np.clip(np.round(s.imag), -32768, 32767)
    iq.tofile(a.out)
    # Prüfung: Spektrum
    S = np.abs(np.fft.fftshift(np.fft.fft(s))) ** 2; fsh = np.fft.fftshift(fo)
    def lvl(f0, bw=20e3):
        m = np.abs(fsh - f0) < bw; return 10 * np.log10(S[m].sum() + 1e-30)
    ref = lvl(-a.offset)
    rep = {"samples": Nout, "rate": FS, "loop_s": Nout / FS, "vision_carrier_bb_hz": -a.offset, "sound_carrier_bb_hz": fsnd,
           "rms_dbfs": float(10 * np.log10(np.mean(np.abs(s) ** 2) / 32768 ** 2)), "peak_dbfs": float(20 * np.log10(a.level)),
           "rel_db": {"vision": 0.0, "sound": lvl(fsnd, 150e3) - ref, "chroma_+4.43": lvl(-a.offset + FSC, 300e3) - ref,
                      "lower_vestige_-1.0MHz": lvl(-a.offset - 1.0e6, 100e3) - ref, "lower_-2.0MHz": lvl(-a.offset - 2.0e6, 100e3) - ref,
                      "upper_+2MHz": lvl(-a.offset + 2.0e6, 100e3) - ref}}
    print(json.dumps(rep, indent=1))

if __name__ == "__main__":
    main()
