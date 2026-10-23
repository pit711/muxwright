#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Signalverarbeitung des UKW-Testsenders (numpy/scipy), blockweise mit Zustaenden ueber Blockgrenzen.

Kette je Traeger (FmCarrier):
  Audio 48 kHz Stereo float -> Preemphasis (IIR, 50/75 us/aus) -> Limiter (Blockverstaerkung + Clipper)
  -> 15-kHz-FIR -> x19/4 auf 228 kHz -> MPX [kHz Hub]:
     dev_audio*((L+R)/2 + (L-R)/2*sin38) + pilot*sin19 + rds*RDS(t)*{sin|cos}57
  -> x1024/57 auf 4,096 MS/s -> Phasenakkumulator -> cos/sin (float32)
Raten: 228 kHz = 192 Samples je RDS-Bit, 12/6/4 Samples je 19/38/57-kHz-Periode; Blocklaenge 18240 MPX-Samples
(80 ms) ist Vielfaches von 57, 192 und 12, die Blockgrenzen fallen also immer auf Phase 0 aller Tabellen.
Pulsformung des RDS nach PiFmRds/MiniRDS (GPL-3.0): Impulspaar + Formfilter cos(pi*f*td/4), 0 <= f <= 2/td.
"""
import math

import numpy as np
from scipy import signal

from . import rds as rdsmod

FS_AUDIO = 48000
FS_MPX = 228000
FS_OUT = 4096000
N_MPX = 18240                    # 80 ms
N_AUDIO = N_MPX * 4 // 19        # 3840
N_OUT = N_MPX * 1024 // 57       # 327680
SPB = FS_MPX // 1187.5           # 192 Samples je RDS-Bit
assert SPB == 192 and N_MPX % 192 == 0 and N_MPX % 57 == 0 and N_MPX % 12 == 0 and N_AUDIO * 19 == N_MPX * 4
BITS_PER_BLOCK = N_MPX // 192    # 95


# --------------------------------------------------------------------------------------------
# Zustandsbehafteter rationaler Resampler (upfirdn mit Historie)
# --------------------------------------------------------------------------------------------
class Resampler:
    """y = Polyphasen-FIR (up/down) ohne Blockartefakte. Eingabeblock muss ein Vielfaches von down/gcd sein,
    Filter h liegt auf der nullgestopften Rate und ist bereits um `up` verstaerkt."""

    def __init__(self, up, down, h, channels=1):
        self.up, self.down = int(up), int(down)
        self.h = np.asarray(h, dtype=np.float64)
        g = math.gcd(self.up, self.down)
        step = self.down // g
        need = -(-(len(self.h) - 1) // self.up)              # Historie in Eingangssamples
        self.H = -(-need // step) * step
        self.channels = channels
        shape = (self.H,) if channels == 1 else (self.H, channels)
        self.hist = np.zeros(shape, dtype=np.float64)
        self.delay_out = (len(self.h) - 1) / 2.0 / self.down   # Gruppenlaufzeit in Ausgangssamples

    def process(self, x):
        x = np.asarray(x)
        x = x.astype(np.complex128, copy=False) if np.iscomplexobj(x) else x.astype(np.float64, copy=False)
        if self.hist.dtype != x.dtype:
            self.hist = self.hist.astype(x.dtype)
        n = x.shape[0]
        if (n * self.up) % self.down:
            raise ValueError("Blocklaenge %d nicht mit %d/%d vertraeglich" % (n, self.up, self.down))
        xx = np.concatenate([self.hist, x], axis=0)
        y = signal.upfirdn(self.h, xx, self.up, self.down, axis=0)
        a = self.H * self.up // self.down
        b = (self.H + n) * self.up // self.down
        self.hist = xx[-self.H:] if self.H else xx[:0]
        return y[a:b]


def design_resampler(up, down, fs_in, pass_hz, stop_hz, atten_db=80.0, taps_multiple=None):
    """Kaiser-FIR auf der nullgestopften Rate fs_in*up, Durchlass bis pass_hz, Sperrbereich ab stop_hz."""
    fs_z = fs_in * up
    beta = 0.1102 * (atten_db - 8.7) if atten_db > 50 else 0.5842 * (atten_db - 21) ** 0.4 + 0.07886 * (atten_db - 21)
    width = (stop_hz - pass_hz) / fs_z
    n = int(math.ceil((atten_db - 8) / (2.285 * 2 * math.pi * width)))
    m = taps_multiple or up
    n = -(-n // m) * m
    if n % 2 == 0:
        n += 1 if m == 1 else 0
    h = signal.firwin(n, (pass_hz + stop_hz) / 2.0, window=("kaiser", beta), fs=fs_z)
    return h * up


# --------------------------------------------------------------------------------------------
# Audio-Stufen
# --------------------------------------------------------------------------------------------
class Preemphasis:
    """Preemphasis als linearphasige FIR mit dem Betragsgang |H(f)| = sqrt(1 + (2*pi*f*tau)^2) (BS.450) bis 15,5 kHz,
    darueber Abfall auf 0 bei 19 kHz. Eine bilineare IIR erster Ordnung waere bei 48 kHz Abtastrate durch die
    Frequenzverzerrung bei 15 kHz um 3 dB zu stark. DC-Verstaerkung 1. tau_us = 0 -> Durchreichen."""

    def __init__(self, tau_us, fs=FS_AUDIO, channels=2, taps=191):
        self.enabled = bool(tau_us)
        self.channels = channels
        self.fs = fs
        if not self.enabled:
            return
        tau = tau_us * 1e-6
        f = np.linspace(0, 15500.0, 80)
        g = np.sqrt(1.0 + (2 * math.pi * f * tau) ** 2)
        freq = list(f) + [19000.0, fs / 2.0]
        gain = list(g) + [0.0, 0.0]
        self.b = signal.firwin2(taps, freq, gain, fs=fs, window=("kaiser", 6.0))
        self.zi = np.zeros((taps - 1, channels)) if channels > 1 else np.zeros(taps - 1)

    def gain_at(self, f):
        if not self.enabled:
            return 1.0
        w, h = signal.freqz(self.b, [1.0], worN=[2 * math.pi * f / self.fs])
        return float(abs(h[0]))

    def process(self, x):
        if not self.enabled:
            return x
        y, self.zi = signal.lfilter(self.b, [1.0], x, axis=0, zi=self.zi)
        return y


class FirFilter:
    def __init__(self, b, channels=2):
        self.b = np.asarray(b, dtype=np.float64)
        self.zi = np.zeros((len(self.b) - 1, channels)) if channels > 1 else np.zeros(len(self.b) - 1)
        self.channels = channels

    def process(self, x):
        y, self.zi = signal.lfilter(self.b, [1.0], x, axis=0, zi=self.zi)
        return y


def lowpass_15k(fs=FS_AUDIO, cutoff=15500.0, taps=127):
    return signal.firwin(taps, cutoff, window=("kaiser", 7.86), fs=fs)


class Limiter:
    """Spitzenbegrenzer: Blockverstaerkung (linear ueber den Block ueberblendet) + Clipper als Sicherung.
    threshold = Spitzenwert nach Preemphasis (1,0 = 100 % Audiohub); release in dB/s."""

    def __init__(self, enable=True, threshold=1.0, release_db_s=3.0, block_s=N_AUDIO / FS_AUDIO, clip=True):
        self.enable, self.thr, self.clip = bool(enable), float(threshold), bool(clip)
        self.rel = 10 ** (release_db_s * block_s / 20.0)
        self.g = 1.0
        self.clipped = 0
        self.gain_min_db = 0.0

    def process(self, x):
        if self.enable:
            p = float(np.max(np.abs(x))) if x.size else 0.0
            g_prev = self.g
            g_new = min(1.0, g_prev * self.rel)
            if p * g_new > self.thr and p > 0:
                g_new = self.thr / p
            if g_new != g_prev or g_new != 1.0:
                # Halbcosinus-Ueberblendung: stetige Ableitung an den Blockgrenzen (keine 12,5-Hz-Linien)
                k = np.arange(x.shape[0]) / float(x.shape[0])
                ramp = g_prev + (g_new - g_prev) * 0.5 * (1.0 - np.cos(math.pi * k))
                x = x * ramp[:, None]
            self.g = g_new
            self.gain_min_db = min(self.gain_min_db, 20 * math.log10(max(g_new, 1e-6)))
        if self.clip:
            over = np.count_nonzero(np.abs(x) > self.thr)
            if over:
                self.clipped += int(over)
                x = np.clip(x, -self.thr, self.thr)
        return x


# --------------------------------------------------------------------------------------------
# RDS-Pulsformung
# --------------------------------------------------------------------------------------------
def rds_shaping_filter(taps=769, fs=FS_MPX):
    """H_T(f) = cos(pi*f*td/4) fuer 0 <= f <= 2/td (td = 1/1187,5 s), darueber 0 (EN 50067 1.7, Gl. 3)."""
    td = 1.0 / 1187.5
    fmax = 2.0 / td   # 2375 Hz
    f = np.linspace(0, fmax, 64)
    gains = np.cos(math.pi * f * td / 4.0)
    freq = list(f) + [fmax + 1.0, fs / 2.0]
    gain = list(gains) + [0.0, 0.0]
    return signal.firwin2(taps, freq, gain, fs=fs)


class RdsShaper:
    """Bits -> Differenzcodierung -> Biphase-Impulspaar -> Formfilter (Overlap-Add, zustandsbehaftet).
    Normierung: Daueraussendung von Nullen ergibt Amplitude 1,0 (EN 50067: Nennhub bezogen auf dieses Signal)."""

    def __init__(self):
        self.h = rds_shaping_filter()
        self.tail = np.zeros(len(self.h) - 1)
        self.diff_state = 0
        # Normierung mit 40 Nullbits (nach Differenzcodierung konstant -> 1187,5-Hz-Sinus)
        imp = self._impulses(np.zeros(40, dtype=np.uint8), reset=False)
        y = np.convolve(imp, self.h)
        self.norm = 1.0 / float(np.max(np.abs(y[len(y) // 3: 2 * len(y) // 3])))
        self.h *= self.norm
        self.diff_state = 0
        # Effektivwert fuer Zufallsdaten (fuer die Pegelmessung im Pruefbericht)
        rnd = (np.random.RandomState(1).rand(4000) > 0.5).astype(np.uint8)
        imp = self._impulses(rnd, reset=False)
        y = np.convolve(imp, self.h)[len(self.h):-len(self.h)]
        self.rms_random = float(np.sqrt(np.mean(y ** 2)))
        self.peak_random = float(np.max(np.abs(y)))
        self.diff_state = 0

    def _impulses(self, bits, reset=False):
        d, self.diff_state = rdsmod.diff_encode(bits, self.diff_state)
        s = 1.0 - 2.0 * np.asarray(d, dtype=np.float64)
        n = len(bits) * 192
        imp = np.zeros(n)
        pos = np.arange(len(bits)) * 192
        imp[pos] += s
        imp[pos + 96] -= s
        return imp

    def process(self, bits):
        imp = self._impulses(bits)
        y = signal.fftconvolve(imp, self.h)
        n = len(imp)
        y[:len(self.tail)] += self.tail
        self.tail = y[n:].copy()
        return y[:n]


# --------------------------------------------------------------------------------------------
# MPX und FM je Traeger
# --------------------------------------------------------------------------------------------
def carrier_tables(n=N_MPX):
    k = np.arange(n)
    return {"sin19": np.sin(2 * math.pi * k / 12.0), "sin38": np.sin(2 * math.pi * k / 6.0),
            "sin57": np.tile([0.0, 1.0, 0.0, -1.0], n // 4), "cos57": np.tile([1.0, 0.0, -1.0, 0.0], n // 4)}


class FmCarrier:
    """Ein komplettes Programm: Audioverarbeitung, MPX in kHz Hub, FM bei 4,096 MS/s.
    p = normalisierter fm-Block (deviation_khz, pilot_khz, preemphasis_us, stereo, limiter{}, mpx_power_dbr,
    rds{enable, level_khz, phase_deg, ...}, audio_gain_db), rds_encoder optional, freq_offset_hz fuer Zweittraeger."""

    def __init__(self, p, rds_encoder=None, freq_offset_hz=0.0, t0_utc=None):
        self.p = p
        self.dev = float(p["deviation_khz"])
        self.stereo = bool(p.get("stereo", True))
        self.pilot = float(p.get("pilot_khz", 6.75)) if self.stereo else 0.0
        r = p.get("rds") or {}
        self.rds_on = bool(r.get("enable", True)) and rds_encoder is not None
        self.rds_level = float(r.get("level_khz", 2.0)) if self.rds_on else 0.0
        self.rds_phase = int(r.get("phase_deg", 0) or 0)
        self.enc = rds_encoder
        self.audio_dev = max(0.0, self.dev - self.pilot - self.rds_level) if p.get("audio_deviation_khz") in (None, "auto") \
            else float(p["audio_deviation_khz"])
        self.gain = 10 ** (float(p.get("audio_gain_db", 0) or 0) / 20.0)
        self.pre = Preemphasis(float(p.get("preemphasis_us", 50) or 0))
        lim = p.get("limiter") or {}
        if isinstance(lim, bool):
            lim = {"enable": lim}
        self.lim = Limiter(lim.get("enable", True), float(lim.get("threshold", 1.0)),
                           float(lim.get("release_db_s", 3.0)), clip=lim.get("clip", True))
        self.lpf = FirFilter(lowpass_15k(), channels=2)
        self.up1 = Resampler(19, 4, design_resampler(19, 4, FS_AUDIO, 15500.0, 32500.0), channels=2)
        self.up2 = Resampler(1024, 57, design_resampler(1024, 57, FS_MPX, 66000.0, 162000.0))
        self.tab = carrier_tables()
        self.c57 = self.tab["cos57"] if self.rds_phase == 90 else self.tab["sin57"]
        self.shaper = RdsShaper() if self.rds_on else None
        self.phase = 0.0
        self.freq_offset_hz = float(freq_offset_hz)
        self.t0_utc = t0_utc
        self.samples_mpx = 0
        self.mpx_limit = p.get("mpx_power_dbr")
        self.mpx_gain = 1.0
        self.mpx_pow_hist = []       # Blockleistungen (linear, dBr-Bezug) fuer 60-s-Mittel
        self.stats = {"blocks": 0, "mpx_peak_khz": 0.0, "mpx_power_dbr": None, "audio_peak": 0.0}
        self.mute_audio = False

    # Audio (N_AUDIO, 2) float in [-1, 1] -> MPX (N_MPX,) in kHz
    def mpx_block(self, audio):
        p = self.p
        x = np.asarray(audio, dtype=np.float64)
        if x.ndim == 1:
            x = np.stack([x, x], axis=1)
        if self.gain != 1.0:
            x = x * self.gain
        if self.mute_audio:
            x = np.zeros_like(x)
        x = self.pre.process(x)
        x = self.lim.process(x)
        x = self.lpf.process(x)
        if self.mpx_gain != 1.0:
            x = x * self.mpx_gain
        self.stats["audio_peak"] = max(self.stats["audio_peak"], float(np.max(np.abs(x))) if x.size else 0.0)
        xu = self.up1.process(x)                      # (N_MPX, 2)
        m = 0.5 * (xu[:, 0] + xu[:, 1])
        if self.stereo:
            s = 0.5 * (xu[:, 0] - xu[:, 1])
            mpx = self.audio_dev * (m + s * self.tab["sin38"]) + self.pilot * self.tab["sin19"]
        else:
            mpx = self.audio_dev * m
        if self.rds_on:
            t_first = (self.t0_utc or 0.0) + self.samples_mpx / float(FS_MPX)
            bits = self.enc.bits(BITS_PER_BLOCK, t_first)
            r = self.shaper.process(bits)
            mpx = mpx + self.rds_level * r * self.c57
        self.samples_mpx += len(mpx)
        # MPX-Leistung nach BS.412: 0 dBr = Sinus mit 19 kHz Hub -> Bezug 19^2/2
        pw = float(np.mean(mpx ** 2)) / (19.0 ** 2 / 2.0)
        self.mpx_pow_hist.append(pw)
        if len(self.mpx_pow_hist) > int(60.0 / (N_MPX / FS_MPX)):
            self.mpx_pow_hist.pop(0)
        avg = sum(self.mpx_pow_hist) / len(self.mpx_pow_hist)
        self.stats["mpx_power_dbr"] = round(10 * math.log10(max(avg, 1e-12)), 2)
        self.stats["mpx_power_block_dbr"] = round(10 * math.log10(max(pw, 1e-12)), 2)
        if self.mpx_limit is not None:
            # Regelung: kurzfristiges Mittel (2 s) auf den Grenzwert, Anstieg langsam
            short = self.mpx_pow_hist[-25:]
            sdb = 10 * math.log10(max(sum(short) / len(short), 1e-12))
            lim = float(self.mpx_limit)
            if sdb > lim:
                self.mpx_gain = max(0.05, self.mpx_gain * 10 ** (-min(0.5, sdb - lim) / 20.0))
            elif sdb < lim - 0.5:
                self.mpx_gain = min(1.0, self.mpx_gain * 10 ** (0.1 / 20.0))
            self.stats["mpx_gain_db"] = round(20 * math.log10(self.mpx_gain), 2)
        self.stats["mpx_peak_khz"] = max(self.stats["mpx_peak_khz"], float(np.max(np.abs(mpx))))
        self.stats["blocks"] += 1
        return mpx

    def iq_block(self, audio, extra_offset_hz=0.0):
        """Komplexe Huellkurve (cos, sin) als zwei float32-Arrays der Laenge N_OUT."""
        mpx = self.mpx_block(audio)
        dev_hz = self.up2.process(mpx) * 1000.0
        off = self.freq_offset_hz + extra_offset_hz
        if off:
            dev_hz += off
        ph = np.cumsum(dev_hz) * (2 * math.pi / FS_OUT) + self.phase
        self.phase = float(ph[-1]) % (2 * math.pi)
        ph32 = np.mod(ph, 2 * math.pi).astype(np.float32)
        return np.cos(ph32), np.sin(ph32)

    def snapshot(self):
        d = dict(self.stats)
        d.update({"deviation_khz": self.dev, "audio_deviation_khz": round(self.audio_dev, 3), "pilot_khz": self.pilot,
                  "rds_level_khz": self.rds_level, "rds_phase_deg": self.rds_phase, "stereo": self.stereo,
                  "preemphasis_us": float(self.p.get("preemphasis_us", 50) or 0),
                  "limiter_gain_min_db": round(self.lim.gain_min_db, 2), "clipped_samples": self.lim.clipped})
        if self.enc is not None:
            d["rds"] = self.enc.stats()
        return d


def interleave_int16(cos1, sin1, a1, cos2=None, sin2=None, a2=0.0):
    """Zwei Traeger addieren und als int16 I/Q (interleaved) ausgeben; a1/a2 = Amplituden in LSB."""
    out = np.empty(2 * len(cos1), dtype=np.float32)
    if cos2 is None:
        out[0::2] = cos1 * a1
        out[1::2] = sin1 * a1
    else:
        out[0::2] = cos1 * a1 + cos2 * a2
        out[1::2] = sin1 * a1 + sin2 * a2
    np.clip(out, -32767, 32767, out=out)
    return out.astype("<i2").tobytes()


def selftest():
    """Blockkontinuitaet: Resampler blockweise == am Stueck (bis auf Rundung); Preemphasis-Gain; RDS-Normierung."""
    r = {}
    rs_a = Resampler(19, 4, design_resampler(19, 4, FS_AUDIO, 15500.0, 32500.0))
    x = np.sin(2 * math.pi * 1000 * np.arange(4 * N_AUDIO) / FS_AUDIO)
    ya = np.concatenate([rs_a.process(x[i * N_AUDIO:(i + 1) * N_AUDIO]) for i in range(4)])
    rs_b = Resampler(19, 4, design_resampler(19, 4, FS_AUDIO, 15500.0, 32500.0))
    yb = rs_b.process(x)
    r["resampler_block_vs_whole_maxdiff"] = float(np.max(np.abs(ya - yb)))
    rs2 = Resampler(1024, 57, design_resampler(1024, 57, FS_MPX, 66000.0, 162000.0))
    xm = np.sin(2 * math.pi * 57000 * np.arange(2 * N_MPX) / FS_MPX)
    y2 = np.concatenate([rs2.process(xm[:N_MPX]), rs2.process(xm[N_MPX:])])
    seg = y2[N_OUT - 2000:N_OUT + 2000]
    # Sprungpruefung an der Blockgrenze: zweite Differenz darf nicht ausreissen
    d2 = np.abs(np.diff(seg, 2))
    r["upsampler_boundary_jump"] = float(d2.max() / (np.median(d2) + 1e-12))
    pre = Preemphasis(50)
    r["preemph_gain_db_1k_15k"] = [round(20 * math.log10(pre.gain_at(1000)), 2), round(20 * math.log10(pre.gain_at(15000)), 2)]
    sh = RdsShaper()
    r["rds_norm"] = round(sh.norm, 4)
    r["rds_rms_random"] = round(sh.rms_random, 4)
    r["rds_peak_random"] = round(sh.peak_random, 4)
    r["ok"] = r["resampler_block_vs_whole_maxdiff"] < 1e-9 and r["upsampler_boundary_jump"] < 20 \
        and 13.0 < r["preemph_gain_db_1k_15k"][1] < 14.5
    return r


if __name__ == "__main__":
    import json
    print(json.dumps(selftest(), indent=1))
