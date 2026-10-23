# -*- coding: utf-8 -*-
"""PAL-Pruefdemodulator (nach pluto_fw/mod/tools/pal_check.py, erweitert).

Huellkurve um den Bildtraeger (nur der Zweiseitenband-Bereich +-1 MHz, dadurch robust gegen das Restseitenband)
-> Zeilenraster ueber die Synchronimpulse -> Graustufenbild (PNG) und Kennwerte: Zeilenfrequenz, Synchronboden,
Austast-/Weisspegel relativ, Bildtraeger-Feinfrequenz, Tontraegerabstand und -pegel, FM-Demodulation des Tons
(Tonfrequenz, Hub), Farbburst-Pegel, Korrelation mit dem Sollbild (Luminanz)."""
import subprocess

import numpy as np

from .systems import FSC, SYSTEMS


def load_iq(path, fmt="s16", skip=0, n=None):
    if fmt == "cf32":
        x = np.fromfile(path, np.complex64, count=-1 if n is None else n + skip, offset=0)[skip:]
        return x.astype(np.complex128)
    r = np.fromfile(path, np.int16, count=-1 if n is None else 2 * (n + skip))[2 * skip:].astype(np.float64) / 32768.0
    return r[0::2] + 1j * r[1::2]


def analyze(x, fs, carrier_hz, system="BG", png=None, ref_luma=None, ffmpeg="ffmpeg"):
    """x: komplexes Basisband (>= 0,16 s empfohlen). carrier_hz: Bildtraeger im Basisband (z. B. -2,25e6)."""
    sysd = SYSTEMS[system]
    n = len(x)
    t = np.arange(n) / fs
    X = np.fft.fft(x * np.exp(-2j * np.pi * carrier_hz * t))
    f = np.fft.fftfreq(n, 1 / fs)
    P = np.abs(X) ** 2

    def wrap(f0):
        """Frequenz relativ zum Bildtraeger -> FFT-Achse nach dem Mischen (Ueberlauf ueber fs/2 faltet)."""
        return ((f0 + fs / 2) % fs) - fs / 2

    def lvl(f0, bw):
        m = np.abs(f - wrap(f0)) < bw
        return 10 * np.log10(P[m].sum() + 1e-30)

    rep = {"carrier_bb_hz": carrier_hz}
    m = np.abs(f) < 50e3
    k = int(np.argmax(P * m))
    rep["vision_carrier_fine_hz"] = float(carrier_hz + f[k])
    fs_snd = sysd["sound_mhz"] * 1e6
    snd_ok = abs(carrier_hz + fs_snd) < fs / 2
    if snd_ok:
        rep["sound_rel_db"] = round(lvl(fs_snd, 150e3) - lvl(0, 20e3), 2)
        # Tontraeger finden und FM demodulieren
        ms = np.abs(f - wrap(fs_snd)) < 200e3
        ks = int(np.argmax(P * ms))
        rep["sound_carrier_offset_hz"] = float(f[ks])
        Xs = X.copy()
        Xs[np.abs(f - f[ks]) > 120e3] = 0
        s = np.fft.ifft(Xs) * np.exp(-2j * np.pi * f[ks] * t)
        dph = np.angle(s[1:] * np.conj(s[:-1])) * fs / (2 * np.pi)       # Momentanfrequenz in Hz
        dec = max(1, int(fs // 48000))
        aud = dph[: len(dph) // dec * dec].reshape(-1, dec).mean(1)
        aud -= aud.mean()
        A = np.abs(np.fft.rfft(aud * np.hanning(len(aud))))
        fa = np.fft.rfftfreq(len(aud), dec / fs)
        A[fa < 50] = 0
        ka = int(np.argmax(A))
        rep["audio_tone_hz"] = round(float(fa[ka]), 1)
        rep["deviation_peak_hz"] = round(float(np.percentile(np.abs(aud), 99.5)), 0)
        rep["deviation_rms_hz"] = round(float(np.sqrt(np.mean(aud ** 2))), 0)
    # Farbburst/Farbtraeger: Pegel bei +4,43 MHz relativ zum Bildtraeger
    if abs(carrier_hz + FSC) < fs / 2:
        rep["chroma_rel_db"] = round(lvl(FSC, 300e3) - lvl(0, 20e3), 2)
    X2 = X.copy()
    X2[np.abs(f) > 1.0e6] = 0
    env = np.abs(np.fft.ifft(X2))
    spl = int(round(64e-6 * fs))
    nl = n // spl
    prof = env[: nl * spl].reshape(-1, spl)
    phase = int(np.argmax(np.convolve(prof.mean(0), np.ones(int(4.7e-6 * fs)), "same")))
    start = (phase - int(2.35e-6 * fs)) % spl
    e = np.roll(env, -start)[: nl * spl].reshape(-1, spl)
    synctip = float(np.percentile(e[:, : int(4e-6 * fs)], 50))
    blank = float(np.median(e[:, int(5e-6 * fs): int(5.5e-6 * fs)]))
    white = float(np.percentile(e, 1))
    rep.update({"lines": int(nl), "line_rate_hz": round(fs / spl, 1), "sync_tip": round(synctip, 5),
                "blank_rel": round(blank / synctip, 4), "white_rel": round(white / synctip, 4),
                "mean_env_rel": round(float(env.mean()) / synctip, 4)})
    if "sound_rel_db" in rep:
        # Ton-/Bildtraeger-Verhaeltnis bezogen auf die Synchronspitze (so ist es in der Norm definiert, B/G: -13 dB);
        # der unmodulierte Bildtraeger hat die mittlere Huellkurve als Amplitude
        rep["sound_rel_peak_db"] = round(rep["sound_rel_db"] + 20 * np.log10(float(env.mean()) / synctip), 2)
    # Halbbild-Synchronisation: Zeilen mit breiten Impulsen finden -> Vollbildanfang
    wide = (e[:, int(10e-6 * fs): int(25e-6 * fs)].mean(1) > 0.9 * synctip)
    starts = [i for i in range(1, len(wide)) if wide[i] and not wide[i - 1]]
    rep["vsync_lines"] = starts[:6]
    # Bild: 625 Zeilen ab dem ersten Vollbild-Anfang, Halbbilder verschraenkt
    img = None
    if len(starts) >= 2 and starts[0] + 625 <= nl:
        f1 = starts[0]
        # erstes Halbbild beginnt mit breiten Impulsen in Zeile 1 (Index f1); aktive Zeilen 24..310 bzw. 336..622
        a0, a1 = int(10.5e-6 * fs), int(62.5e-6 * fs)
        L = np.arange(625)
        fld = e[f1: f1 + 625, a0:a1]
        rows = np.zeros((576, a1 - a0))
        for Ln in range(24, 311):
            r = 2 * (Ln - 24)
            if r < 576:
                rows[r] = fld[Ln - 1]
        for Ln in range(336, 623):
            r = 2 * (Ln - 336) + 1
            if r < 576:
                rows[r] = fld[Ln - 1]
        img = np.clip((blank - rows) / (blank - white + 1e-12), 0, 1)
    else:
        a0, a1 = int(10.5e-6 * fs), int(62.5e-6 * fs)
        img = np.clip((blank - e[:576, a0:a1]) / (blank - white + 1e-12), 0, 1)
    rep["image_size"] = list(img.shape)
    if ref_luma is not None:
        # Sollbild (576 x 720, 0..1) auf die gemessene Breite bringen; die ersten/letzten Zeilen (teils Austastung) weg
        hh, ww = img.shape
        xs = np.linspace(0, ref_luma.shape[1] - 1, ww)
        ref = np.array([np.interp(xs, np.arange(ref_luma.shape[1]), r) for r in ref_luma[:hh]])
        # Sollbild auf dieselbe Bandbreite bringen wie die Huellkurven-Demodulation (+-1 MHz)
        from scipy.ndimage import uniform_filter1d
        ref = uniform_filter1d(ref, max(1, int(round(fs / 1.0e6))), axis=1, mode="nearest")
        a = img[4:-4, 8:-8].ravel()
        b = ref[4:-4, 8:-8].ravel()
        best = -1.0
        for dx in range(-6, 7):                         # Zeilenversatz durch Filterlaufzeit zulassen
            bb = np.roll(ref, dx, axis=1)[4:-4, 8:-8].ravel()
            c = float(np.corrcoef(a, bb)[0, 1])
            if c > best:
                best, bdx = c, dx
        rep["luma_correlation"] = round(best, 4)
        rep["luma_shift_px"] = bdx
        rep["luma_mean_abs_err"] = round(float(np.mean(np.abs(img[4:-4, 8:-8] - np.roll(ref, bdx, axis=1)[4:-4, 8:-8]))), 4)
    if png:
        h, w = img.shape
        raw = (img * 255).astype(np.uint8).tobytes()
        subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "%dx%d" % (w, h), "-i", "-",
                        "-vf", "scale=768:576", png], input=raw, check=True)
        rep["png"] = png
    return rep
