# -*- coding: utf-8 -*-
"""PAL-Erzeugung (CCIR 625/50, negative AM mit Restseitenband, FM-Ton).

1. loop_iq(): Standbild-Schleife aus 4 Vollbildern (PAL-8-Halbbild-Sequenz, 160 ms). Verfahren 1:1 aus
   pluto_fw/mod/tools/pal_bg_gen.py (Modulator-Agent, am LG-Fernseher erfolgreich): Composite bei 4·fs, Huellkurve,
   Restseitenbandfilter und Verschiebung im Frequenzbereich ueber die ganze Schleife (periodisch -> nahtlos), Ton
   als FM-Traeger mit ganzzahliger Periodenzahl. Erweitert um Normen D/K und I, Tonquelle, Bildquelle.
2. StreamEncoder: Bild fuer Bild (40 ms) fuer bewegtes Video. Composite bei 2·fs, Restseitenband + Verschiebung +
   Dezimierung 2:1 mit einem komplexen FIR (Overlap-Save). Jedes Vollbild ist unabhaengig, weil die letzten Zeilen
   eines Vollbilds (623-625) reine Synchronzeilen sind -> der Filtervorlauf ist konstant. Damit laesst sich die
   Arbeit auf mehrere Prozesse verteilen. Ton (FM, Preemphase 50 us) im Hauptprozess mit stetiger Phase.
"""
import os
import subprocess

import numpy as np

from .systems import FSC, LINE_S, LINES, SYSTEMS

FRAMES = 4                 # 4 Vollbilder = 8 Halbbilder: Farbtraegerphase wiederholt sich exakt (FSC * 0,16 s ganzzahlig)
LOOP_S = FRAMES / 25.0


# --------------------------------------------------------------------------------------------- Bild
def ffmpeg_image(host, img, w=720, h=576, log=None):
    """Standbild als RGB float (h, w, 3) 0..1. img: {"kind":"testpattern"|"file", "pattern", "file", "text", "text2"}."""
    ff = host.tool("ffmpeg")
    vf = []
    font = _font()
    if img.get("text") or img.get("text2"):
        vf.append("drawbox=x=0:y=%d:w=%d:h=%d:color=black:t=fill" % (int(h * 0.70), w, int(h * 0.30)))
    for txt, size, y, col in ((img.get("text"), 52, 0.74, "white"), (img.get("text2"), 30, 0.87, "yellow")):
        if txt and font:
            vf.append("drawtext=fontfile=%s:text='%s':fontcolor=%s:fontsize=%d:x=(w-text_w)/2:y=%d"
                      % (font, _esc(txt), col, size, int(h * y)))
    if img.get("kind") == "file" and img.get("file"):
        src = ["-i", host.media_path(img["file"])]
        vf.insert(0, "scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2" % (w, h, w, h))
    else:
        src = ["-f", "lavfi", "-i", "%s=size=%dx%d:rate=25" % (img.get("pattern", "pal75bars"), w, h)]
    cmd = [ff, "-nostdin", "-loglevel", "error"] + src + (["-vf", ",".join(vf)] if vf else []) + \
          ["-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return np.frombuffer(raw[: w * h * 3], np.uint8).reshape(h, w, 3).astype(np.float64) / 255.0


def _font():
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.isfile(f):
            return f
    return None


def _esc(s):
    s = str(s)
    for ch in ("\\", "'", ":", "%", ",", "[", "]", ";"):
        s = s.replace(ch, "\\" + ch)
    return s


# --------------------------------------------------------------------------------------------- Zeilenaufbau
def _kind(p):
    """Halbzeilen-Position p (1.0 ... 625.5) -> Impulsart nach BT.470 (Vor-/Nachausgleich, Hauptimpulse)."""
    if p >= 623.5:
        return "eq"
    if 1.0 <= p < 3.5:
        return "broad"
    if 3.5 <= p < 6.0:
        return "eq"
    if 311.0 <= p < 313.5:
        return "eq"
    if 313.5 <= p < 316.0:
        return "broad"
    if 316.0 <= p < 318.5:
        return "eq"
    return None


def line_pulses(L, spl):
    half = spl // 2
    us = spl / 64.0
    sync, eq, broad = int(round(4.7 * us)), int(round(2.35 * us)), int(round(27.3 * us))
    out = []
    for h2 in (0, 1):
        k = _kind((2 * (L - 1) + h2) / 2 + 1)
        if k == "eq":
            out.append((h2 * half, eq))
        elif k == "broad":
            out.append((h2 * half, broad))
        elif h2 == 0:
            out.append((0, sync))
    return out


def in_vsync(L):
    return (L <= 6) or (310 <= L <= 318) or (L >= 623)


def active_row(L):
    """Bildzeile (0..575) einer Zeilennummer L oder None (Halbbild 1: 24..310 gerade, Halbbild 2: 336..622 ungerade)."""
    if 24 <= L <= 310:
        r = 2 * (L - 24)
    elif 336 <= L <= 622:
        r = 2 * (L - 336) + 1
    else:
        return None
    return r if r < 576 else None


# --------------------------------------------------------------------------------------------- 1. Standbild-Schleife
def loop_iq(p, rgb):
    """p: Parameter (system, fs, offset_hz, level, sound{...}). rgb: (576, 720, 3) 0..1.
    -> (komplexes Signal (np.complex128, Laenge fs*0,16), Bericht)."""
    sysd = SYSTEMS[p["system"]]
    FS = float(p["fs"])
    FS_HI = 4 * FS
    spl = int(round(LINE_S * FS_HI))
    half = spl // 2
    us = FS_HI * 1e-6
    N = FRAMES * LINES * spl
    t = np.arange(N) / FS_HI
    h, w = rgb.shape[:2]
    R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    Y = 0.299 * R + 0.587 * G + 0.114 * B
    U = 0.493 * (B - Y)
    V = 0.877 * (R - Y)
    act0, act_len = int(round(10.5 * us)), int(round(52.0 * us))
    xs = np.linspace(0, w - 1, act_len)

    v = np.zeros(N)
    burst0, burst_len = int(round(5.6 * us)), int(round(2.25 * us))
    vswitch = np.zeros(N)
    burst_mask = np.zeros(N)
    active_mask = np.zeros(N)
    Yb = np.zeros(N)
    Ub = np.zeros(N)
    Vb = np.zeros(N)
    rows = {}
    for f in range(FRAMES):
        for L in range(1, LINES + 1):
            base = (f * LINES + L - 1) * spl
            for s0, ln in line_pulses(L, spl):
                v[base + s0: base + s0 + ln] = -0.3
            sw = 1.0 if ((f * LINES + L) % 2) else -1.0
            vswitch[base: base + spl] = sw
            if not in_vsync(L):
                burst_mask[base + burst0: base + burst0 + burst_len] = 1.0
            r = active_row(L)
            if r is not None:
                if r not in rows:
                    rows[r] = (np.interp(xs, np.arange(w), Y[r]), np.interp(xs, np.arange(w), U[r]),
                               np.interp(xs, np.arange(w), V[r]))
                sl = slice(base + act0, base + act0 + act_len)
                Yb[sl], Ub[sl], Vb[sl] = rows[r]
                active_mask[sl] = 1.0
    f_hi = np.fft.fftfreq(N, 1 / FS_HI)

    def lp(x, fc, tr):
        X = np.fft.fft(x)
        H = np.clip((fc + tr / 2 - np.abs(f_hi)) / tr, 0, 1)
        H = 0.5 - 0.5 * np.cos(np.pi * H)
        return np.real(np.fft.ifft(X * H))

    Yb = lp(Yb, sysd["video_mhz"] * 1e6, 0.5e6)
    Ub = lp(Ub, 1.3e6, 0.6e6)
    Vb = lp(Vb, 1.3e6, 0.6e6)
    am = lp(active_mask, 2.5e6, 1e6)
    ph = 2 * np.pi * FSC * t
    chroma = Ub * np.sin(ph) + vswitch * Vb * np.cos(ph)
    burst = 0.15 * (-np.sin(ph) + vswitch * np.cos(ph)) / np.sqrt(2) * lp(burst_mask, 2e6, 1e6)
    v = v + (Yb * 0.7 + chroma * 0.7) * am + burst
    env = np.clip(0.73 - 0.9 * v, 0.05, 1.0)
    del Yb, Ub, Vb, chroma, burst, am, active_mask, burst_mask, vswitch, v, t, ph

    X = np.fft.fft(env)

    def edge(x, a0, a1):
        y = np.clip((x - a0) / (a1 - a0), 0, 1)
        return 0.5 - 0.5 * np.cos(np.pi * y)

    vest = sysd["vestige_mhz"] * 1e6
    vid_hi = sysd["video_mhz"] * 1e6
    H = edge(f_hi, -vest - 0.5e6, -vest) * (1 - edge(f_hi, vid_hi, vid_hi + 0.2e6))
    X = X * H
    off = float(p["offset_hz"])
    shift = int(round(off * N / FS_HI))
    X = np.roll(X, -shift)
    Nout = int(N * FS / FS_HI)
    keep = np.concatenate([X[: Nout // 2], X[-Nout // 2:]])
    fo = np.fft.fftfreq(Nout, 1 / FS)
    keep *= (1 - edge(np.abs(fo), FS / 2 - 0.15e6, FS / 2))
    vid = np.fft.ifft(keep) * (Nout / N)
    s = vid + sound_loop(p, Nout, FS)
    peak = np.max(np.abs(s))
    s = s / peak * float(p["level"])
    return s, spectrum_report(s, p, FS)


def sound_loop(p, n, fs):
    """FM-Ton fuer die Schleife: Tonfrequenz auf ganze Periodenzahl je 160 ms gerundet (Vielfache von 6,25 Hz)."""
    sd = p["sound"]
    if sd.get("kind") == "off":
        return np.zeros(n, complex)
    sysd = SYSTEMS[p["system"]]
    to = np.arange(n) / fs
    fsnd = sysd["sound_mhz"] * 1e6 - float(p["offset_hz"])
    amp = 10 ** (float(sd.get("level_db", -13)) / 20)
    if sd.get("kind") == "silence":
        return amp * np.exp(2j * np.pi * fsnd * to)
    tone = round(float(sd.get("freq_hz", 1000)) / (1 / LOOP_S)) * (1 / LOOP_S)
    tau = float(sd.get("preemphasis_us", 50)) * 1e-6
    pre = abs(1 + 1j * 2 * np.pi * tone * tau)
    beta = float(sd.get("deviation_hz", 50000)) * float(sd.get("tone_level", 1.0)) * pre / tone
    return amp * np.exp(1j * (2 * np.pi * fsnd * to + beta * np.sin(2 * np.pi * tone * to)))


def spectrum_report(s, p, fs):
    n = len(s)
    S = np.abs(np.fft.fftshift(np.fft.fft(s))) ** 2
    fsh = np.fft.fftshift(np.fft.fftfreq(n, 1 / fs))
    off = float(p["offset_hz"])
    sysd = SYSTEMS[p["system"]]

    def lvl(f0, bw=20e3):
        m = np.abs(fsh - f0) < bw
        return 10 * np.log10(S[m].sum() + 1e-30)

    ref = lvl(-off)
    fsnd = sysd["sound_mhz"] * 1e6 - off
    return {"samples": n, "rate": fs, "loop_s": n / fs, "vision_carrier_bb_hz": -off, "sound_carrier_bb_hz": fsnd,
            "rms_dbfs": round(float(10 * np.log10(np.mean(np.abs(s) ** 2))), 2),
            "peak_dbfs": round(float(20 * np.log10(np.max(np.abs(s)))), 2),
            "rel_db": {"sound": round(lvl(fsnd, 150e3) - ref, 2), "chroma_+4.43": round(lvl(-off + FSC, 300e3) - ref, 2),
                       "lower_vestige_-1.0MHz": round(lvl(-off - 1.0e6, 100e3) - ref, 2),
                       "lower_-2.0MHz": round(lvl(-off - 2.0e6, 100e3) - ref, 2) if off + 2.0e6 < fs / 2 else None,
                       "upper_+2MHz": round(lvl(-off + 2.0e6, 100e3) - ref, 2)}}


def to_int16(s, out_path=None, fmt="s16"):
    if fmt == "cf32":
        a = s.astype(np.complex64)
        if out_path:
            a.tofile(out_path)
        return a
    iq = np.empty(2 * len(s), np.int16)
    iq[0::2] = np.clip(np.round(s.real * 32767), -32768, 32767)
    iq[1::2] = np.clip(np.round(s.imag * 32767), -32768, 32767)
    if out_path:
        iq.tofile(out_path)
    return iq


# --------------------------------------------------------------------------------------------- 2. Strom-Encoder
class StreamEncoder:
    """Ein Vollbild (Y, Pb, Pr als float32 (576, act_len)) -> komplexes Basisband (fs*0,04 Samples, ohne Ton)."""

    def __init__(self, p, taps=191):
        from scipy import signal  # noqa: F401  (Pruefung, dass scipy da ist)
        self.p = p
        sysd = SYSTEMS[p["system"]]
        self.fs = float(p["fs"])
        self.fh = 2 * self.fs
        self.spl = int(round(LINE_S * self.fh))
        us = self.fh * 1e-6
        self.act0, self.act_len = int(round(10.5 * us)), int(round(52.0 * us))
        self.n = LINES * self.spl                      # Samples je Vollbild bei fh
        off = float(p["offset_hz"])
        if abs(off * 0.04 - round(off * 0.04)) > 1e-6:
            raise ValueError("offset_hz muss ein Vielfaches von 25 Hz sein")
        # statische Teile je Vollbild k (0..3): Synchron, Burst; Farbtraeger fuer den aktiven Bereich
        self.base = []
        self.S = []
        self.C = []
        bw = np.zeros(self.spl, np.float32)
        b0, bl = int(round(5.6 * us)), int(round(2.25 * us))
        bw[b0:b0 + bl] = 1.0
        bw = np.convolve(bw, np.hanning(int(us * 0.6)) / np.hanning(int(us * 0.6)).sum(), "same").astype(np.float32)
        aw = np.ones(self.act_len, np.float32)
        ramp = int(us * 0.3)
        aw[:ramp] = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, ramp))
        aw[-ramp:] = aw[:ramp][::-1]
        self.aw = aw
        self.lines_idx = np.array([L - 1 for L in range(1, LINES + 1) if active_row(L) is not None])
        self.rows_idx = np.array([active_row(L) for L in range(1, LINES + 1) if active_row(L) is not None])
        tl = np.arange(self.spl) / self.fh
        ta = (self.act0 + np.arange(self.act_len)) / self.fh
        for k in range(FRAMES):
            v = np.zeros((LINES, self.spl), np.float32)
            for L in range(1, LINES + 1):
                for s0, ln in line_pulses(L, self.spl):
                    v[L - 1, s0:s0 + ln] = -0.3
            t0 = k * LINES * LINE_S + np.arange(LINES)[:, None] * LINE_S
            sw = np.where(((k * LINES + np.arange(1, LINES + 1)) % 2) == 1, 1.0, -1.0)[:, None]
            ph_l = 2 * np.pi * FSC * (t0 + tl[None, :])
            vs = np.array([not in_vsync(L) for L in range(1, LINES + 1)])[:, None]
            burst = 0.15 * (-np.sin(ph_l) + sw * np.cos(ph_l)) / np.sqrt(2) * bw[None, :] * vs
            v += burst.astype(np.float32)
            self.base.append(v)
            ph_a = 2 * np.pi * FSC * (t0[self.lines_idx] + ta[None, :])
            self.S.append((np.sin(ph_a) * aw * 0.7).astype(np.float32))
            self.C.append((sw[self.lines_idx] * np.cos(ph_a) * aw * 0.7).astype(np.float32))
        # Mischer (periodisch je Vollbild) und komplexes FIR: Restseitenband + Antialiasing fuer 2:1
        n_idx = np.arange(self.n)
        self.mix = np.exp(-2j * np.pi * off * n_idx / self.fh).astype(np.complex64)
        vest = sysd["vestige_mhz"] * 1e6
        vid_hi = sysd["video_mhz"] * 1e6
        nf = 8192
        f = np.fft.fftfreq(nf, 1 / self.fh)

        def edge(x, a0, a1):
            y = np.clip((x - a0) / (a1 - a0), 0, 1)
            return 0.5 - 0.5 * np.cos(np.pi * y)

        fb = f + off                                   # Frequenz relativ zum Bildtraeger
        Hd = edge(fb, -vest - 0.5e6, -vest) * (1 - edge(fb, vid_hi, vid_hi + 0.25e6))
        Hd *= (1 - edge(np.abs(f), self.fs / 2 - 0.2e6, self.fs / 2))    # nichts ueber fs/2 (Dezimierung)
        hh = np.fft.fftshift(np.fft.ifft(Hd))
        c = nf // 2
        h = hh[c - taps // 2: c + taps // 2 + 1] * np.kaiser(taps, 7.0)
        self.h = h.astype(np.complex64)
        self.taps = len(self.h)
        # Filtervorlauf: Ende eines Vollbilds (Zeilen 623-625 = reine Synchronzeilen) ist statisch
        tail_v = self.base[3][-3:].reshape(-1)
        tail_env = np.clip(0.73 - 0.9 * tail_v, 0.05, 1.0)
        self.hist = (tail_env[-(self.taps - 1):] * self.mix[-(self.taps - 1):]).astype(np.complex64)
        self.vid_scale = 1.0

    def frame(self, Y, Pb, Pr, k):
        """Y (576, act_len) 0..1, Pb/Pr (576, act_len) -0,5..0,5 (bereits tiefpassgefiltert). -> complex64 (fs*0,04)."""
        from scipy import signal
        k %= FRAMES
        v = self.base[k].copy()
        U = 0.8736 * Pb[self.rows_idx]
        V = 1.2296 * Pr[self.rows_idx]
        act = (Y[self.rows_idx] * (0.7 * self.aw)) + U * self.S[k] + V * self.C[k]
        v[self.lines_idx, self.act0:self.act0 + self.act_len] += act
        env = np.clip(0.73 - 0.9 * v.reshape(-1), 0.05, 1.0)
        x = np.concatenate([self.hist, env * self.mix])
        y = signal.oaconvolve(x, self.h, mode="valid")[::2]
        return y.astype(np.complex64)


class SoundFM:
    """FM-Tontraeger mit stetiger Phase. audio: float Mono 48 kHz (-1..1, 1 = Nennhub)."""

    def __init__(self, p, fs, sr=48000):
        sysd = SYSTEMS[p["system"]]
        sd = p["sound"]
        self.fs, self.sr = fs, sr
        self.f = sysd["sound_mhz"] * 1e6 - float(p["offset_hz"])
        self.dev = float(sd.get("deviation_hz", 50000))
        self.amp = 10 ** (float(sd.get("level_db", -13)) / 20) if sd.get("kind") != "off" else 0.0
        self.phase = 0.0
        self.pos = 0                       # globaler Ausgabe-Sampleindex
        tau = float(sd.get("preemphasis_us", 50)) * 1e-6
        from scipy import signal
        if tau > 0:
            # Preemphase (1 + s*tau) mit Begrenzung bei 21 kHz (Pol), bilinear
            t2 = 1 / (2 * np.pi * 21000)
            self.b, self.a = signal.bilinear([tau, 1], [t2, 1], fs=sr)
            g = abs(np.polyval(self.b, 1) / np.polyval(self.a, 1))
            self.b = self.b / g
        else:
            self.b, self.a = np.array([1.0]), np.array([1.0])
        self.zi = np.zeros(max(len(self.a), len(self.b)) - 1)
        self.apos = 0                      # Index des naechsten Audiosamples

    def block(self, audio, n):
        """audio: Audiosamples fuer genau diesen Block (Laenge passend zu n Ausgabesamples). -> complex64 (n)."""
        from scipy import signal
        if self.amp == 0:
            self.pos += n
            return np.zeros(n, np.complex64)
        m, self.zi = signal.lfilter(self.b, self.a, audio, zi=self.zi)
        # Halteglied: Ausgabesample i gehoert zum Audiosample floor(i*sr/fs)
        gi = self.pos + np.arange(n)
        ai = (gi * self.sr) // int(self.fs) - self.apos
        ai = np.clip(ai, 0, len(m) - 1)
        inc = 2 * np.pi * (self.f + self.dev * m[ai]) / self.fs
        ph = self.phase + np.cumsum(inc)
        out = (self.amp * np.exp(1j * (ph - inc))).astype(np.complex64)
        self.phase = float(np.mod(ph[-1], 2 * np.pi))
        self.pos += n
        self.apos += len(audio)
        return out

    def samples_needed(self, n):
        """Wie viele Audiosamples der naechste Block mit n Ausgabesamples braucht (ganzzahlig, ohne Drift)."""
        end = ((self.pos + n) * self.sr) // int(self.fs)
        return int(end - self.apos)
