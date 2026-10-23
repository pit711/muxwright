#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verifikation des UKW-Senders: FM-Demodulation (numpy), Messungen am MPX, Stereo-Decoder, redsea (RDS).

analyze(path, rate, fmt, cfg, ...)  -> Pruefbericht (JSON-faehig) mit Soll/Ist je Feld und `mismatches`
measure_iq(iq, fs, cfg, ...)        -> dasselbe fuer ein Array im Speicher (Selbsttest)
looks_like_fm(path, rate, fmt)      -> True, wenn der Mitschnitt schmalbandig (UKW) statt DAB (1,5 MHz) ist

Messkette: IQ -> [Verschiebung] -> komplexe Dezimation auf 512 kHz (+-150 kHz) -> Quadraturdemodulator
dphi*fs/2pi -> Dezimation auf 256 kHz = MPX in kHz Hub -> Messungen; redsea bekommt das MPX als int16 (-r 256000).
"""
import json
import math
import os
import subprocess
import time

import numpy as np
from scipy import signal

from . import dsp
from . import rds as rdsmod

FS_IF = 512000
FS_MPX = 256000
DEEMPH_TAU = {50: 50e-6, 75: 75e-6}

# ECC + Laenderkennung (PI-Nibble) -> ISO (EN 50067 Annex D / ETSI TS 101 756, Auszug Europa) wie redsea "country"
COUNTRY = {
    0xE0: {1: "de", 2: "dz", 3: "ad", 4: "il", 5: "it", 6: "be", 7: "ru", 8: "ps", 9: "al", 10: "at", 11: "hu", 12: "mt",
           13: "de", 15: "eg"},
    0xE1: {1: "gr", 2: "cy", 3: "sm", 4: "ch", 5: "jo", 6: "fi", 7: "lu", 8: "bg", 9: "dk", 10: "gi", 11: "iq", 12: "gb",
           13: "ly", 14: "ro", 15: "fr"},
    0xE2: {1: "ma", 2: "cz", 3: "pl", 4: "va", 5: "sk", 6: "sy", 7: "tn", 9: "li", 10: "is", 11: "mc", 12: "lt", 13: "rs",
           14: "es", 15: "no"},
    0xE3: {1: "me", 2: "ie", 3: "tr", 4: "mk", 8: "nl", 9: "lv", 10: "lb", 11: "az", 12: "hr", 13: "kz", 14: "se", 15: "by"},
    0xE4: {1: "md", 2: "ee", 3: "kg", 6: "ua", 8: "pt", 9: "si", 10: "am", 11: "uz", 12: "ge", 14: "tm", 15: "ba"},
}


# --------------------------------------------------------------------------------------------
# Einlesen
# --------------------------------------------------------------------------------------------
def iq_chunks(path, fmt, max_samples, chunk=1 << 21):
    bps = 4 if fmt == "s16" else 8
    done = 0
    with open(path, "rb") as f:
        while done < max_samples:
            n = min(chunk, max_samples - done)
            b = f.read(n * bps)
            if len(b) < bps:
                break
            b = b[:len(b) // bps * bps]
            if fmt == "s16":
                x = np.frombuffer(b, dtype="<i2").astype(np.float32) / 32768.0
            else:
                x = np.frombuffer(b, dtype="<f4")
            c = (x[0::2] + 1j * x[1::2]).astype(np.complex64)
            done += len(c)
            yield c


def looks_like_fm(path, rate, fmt):
    try:
        bps = 4 if fmt == "s16" else 8
        size = os.path.getsize(path)
        n = min(1 << 18, size // bps)
        off = (size // bps // 2 - n // 2) * bps
        with open(path, "rb") as f:
            f.seek(max(0, off))
            b = f.read(n * bps)
        if fmt == "s16":
            x = np.frombuffer(b, dtype="<i2").astype(np.float32) / 32768.0
        else:
            x = np.frombuffer(b, dtype="<f4")
        c = x[0::2] + 1j * x[1::2]
        f_, p = signal.welch(c, fs=rate, nperseg=4096, return_onesided=False)
        p = p - np.median(p) * 0.5
        p[p < 0] = 0
        inband = p[np.abs(f_) <= 150e3].sum()
        return float(inband / max(p.sum(), 1e-20)) > 0.85
    except Exception:
        return False


# --------------------------------------------------------------------------------------------
# Demodulation
# --------------------------------------------------------------------------------------------
class Demod:
    """IQ (beliebige Rate aus {512k, 1024k, 2048k, 4096k}) -> MPX 256 kHz in kHz Hub, blockweise."""

    def __init__(self, rate, shift_hz=0.0, if_bw_hz=150e3):
        self.rate = int(rate)
        if self.rate % FS_IF:
            raise ValueError("Abtastrate %d nicht durch 512000 teilbar" % self.rate)
        d1 = self.rate // FS_IF
        self.d1 = d1
        self.shift = float(shift_hz)
        self.phase = 0.0
        self.dec1 = dsp.Resampler(1, d1, dsp.design_resampler(1, d1, self.rate, if_bw_hz, FS_IF - if_bw_hz, 70.0,
                                                               taps_multiple=1)) if d1 > 1 else None
        # Dezimation 512k -> 256k mit Entzerrung des Quadraturdemodulators: die Phasendifferenz zweier Samples
        # entspricht einem gleitenden Mittel ueber eine Abtastperiode, |H(f)| = sinc(f/512 kHz) (-0,08 dB bei 38 kHz,
        # -0,18 dB bei 57 kHz). Der Durchlass bis 100 kHz bekommt 1/sinc, darueber Sperrbereich.
        fq = np.linspace(0, 100e3, 51)
        gain = list(1.0 / np.sinc(fq / FS_IF)) + [0.0, 0.0]
        self.dec2 = dsp.Resampler(1, 2, signal.firwin2(255, list(fq) + [156e3, FS_IF / 2.0], gain, fs=FS_IF,
                                                       window=("kaiser", 7.0)))
        self.last = None
        self.pre_lpf = None
        if d1 == 1:
            # bei 512 kHz Eingang: Bandbegrenzung ohne Dezimation
            self.pre_lpf = dsp.FirFilter(signal.firwin(127, if_bw_hz, fs=FS_IF), channels=1)

    def process(self, c):
        c = np.asarray(c, dtype=np.complex128)
        if self.shift:
            n = np.arange(len(c))
            w = -2 * math.pi * self.shift / self.rate
            c = c * np.exp(1j * (self.phase + w * n))
            self.phase = (self.phase + w * len(c)) % (2 * math.pi)
        if self.dec1 is not None:
            m = len(c) // (2 * self.d1) * (2 * self.d1)
            c = c[:m]
            x = self.dec1.process(c)
        else:
            x = c[:len(c) // 2 * 2]
        if self.pre_lpf is not None:
            x = self.pre_lpf.process(x)
        if self.last is None:
            self.last = x[:1]
            x_prev = np.concatenate([x[:1], x[:-1]])
        else:
            x_prev = np.concatenate([self.last, x[:-1]])
        dphi = np.angle(x * np.conj(x_prev))
        self.last = x[-1:]
        dev_khz = dphi * (FS_IF / (2 * math.pi)) / 1000.0
        return self.dec2.process(dev_khz)


def demod_file(path, rate, fmt, seconds, shift_hz=0.0, skip_s=0.3):
    dm = Demod(rate, shift_hz)
    parts = []
    for c in iq_chunks(path, fmt, int((seconds + skip_s) * rate)):
        parts.append(dm.process(c))
    mpx = np.concatenate(parts) if parts else np.zeros(0)
    return mpx[int(skip_s * FS_MPX):]


def demod_array(iq, rate, shift_hz=0.0, skip_s=0.3):
    dm = Demod(rate, shift_hz)
    parts = []
    step = 1 << 21
    for i in range(0, len(iq), step):
        parts.append(dm.process(iq[i:i + step]))
    mpx = np.concatenate(parts)
    return mpx[int(skip_s * FS_MPX):]


# --------------------------------------------------------------------------------------------
# Messungen
# --------------------------------------------------------------------------------------------
def tone_estimate(x, fs, f0, bw, nfft=None):
    """Amplitude (Flat-Top-Fenster) und Frequenz (parabolische Interpolation, Hann) der staerksten Linie in f0 +- bw."""
    n = len(x)
    if nfft is None:
        nfft = int(2 ** math.ceil(math.log2(max(n, 2))))
    wf = signal.windows.flattop(n)
    X = np.fft.rfft(x * wf, nfft)
    freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
    sel = (freqs >= f0 - bw) & (freqs <= f0 + bw)
    if not sel.any():
        return {"amplitude": 0.0, "freq_hz": f0}
    idx = np.where(sel)[0]
    k = idx[np.argmax(np.abs(X[idx]))]
    amp = 2.0 * abs(X[k]) / wf.sum()
    wh = np.hanning(n)
    Y = np.abs(np.fft.rfft(x * wh, nfft))
    k2 = idx[np.argmax(Y[idx])]
    if 0 < k2 < len(Y) - 1 and Y[k2] > 0:
        a, b, c = [20 * math.log10(max(v, 1e-20)) for v in (Y[k2 - 1], Y[k2], Y[k2 + 1])]
        den = (a - 2 * b + c)
        p = 0.5 * (a - c) / den if den else 0.0
    else:
        p = 0.0
    return {"amplitude": float(amp), "freq_hz": float((k2 + p) * fs / nfft)}


def _lowpass_complex(z, fs, cutoff, dec):
    """Mischprodukt tiefpassfiltern und dezimieren (fuer Pilot/RDS-Huellkurven)."""
    y = signal.resample_poly(z, 1, dec)
    fs2 = fs / dec
    if cutoff < fs2 / 2 * 0.8:
        h = signal.firwin(101, cutoff, fs=fs2)
        y = signal.fftconvolve(y, h, mode="same")
    return y, fs2


def measure_pilot_rds(mpx, fs, rds_phase_expect=None):
    r = {}
    t = np.arange(len(mpx)) / fs
    est = tone_estimate(mpx, fs, 19000.0, 150.0)
    f19 = est["freq_hz"]
    r["pilot"] = {"level_khz": round(est["amplitude"], 4), "freq_hz": round(f19, 3), "freq_error_hz": round(f19 - 19000.0, 3)}
    # komplexe Huellkurve des Piloten (4 kHz Rate), Phase linear fitten -> genaue Frequenz und Phase
    z19 = mpx * np.exp(-2j * math.pi * f19 * t)
    z19d, fsd = _lowpass_complex(z19, fs, 300.0, 64)
    k = int(0.05 * fsd)
    z19d = z19d[k:-k] if len(z19d) > 3 * k else z19d
    ph = np.unwrap(np.angle(z19d))
    tt = np.arange(len(ph)) / fsd + k / fsd
    slope, icpt = np.polyfit(tt, ph, 1)
    f19_fit = f19 + slope / (2 * math.pi)
    r["pilot"]["freq_hz_fit"] = round(f19_fit, 4)
    r["pilot"]["amp_std_pct"] = round(100 * float(np.std(np.abs(z19d)) / max(np.mean(np.abs(z19d)), 1e-9)), 2)
    theta19 = icpt  # Phase bei t=0 (rad), bezogen auf exp(-j 2 pi f19 t)
    # RDS-Band bei 3*f19
    z57 = mpx * np.exp(-2j * math.pi * (3 * f19_fit) * t)
    z57d, fs57 = _lowpass_complex(z57, fs, 3000.0, 16)
    k = int(0.05 * fs57)
    z57d = z57d[k:-k] if len(z57d) > 3 * k else z57d
    rms_d = 2.0 * float(np.sqrt(np.mean(np.abs(z57d) ** 2)))
    shaper_rms = 0.7073   # Effektivwert des normierten RDS-Signals bei Zufallsdaten (dsp.RdsShaper.rms_random)
    theta57 = 0.5 * math.atan2(float(np.imag(np.mean(z57d ** 2))), float(np.real(np.mean(z57d ** 2))))
    # Referenz fuer 3*theta19 muss mit derselben Frequenz gebildet sein: Pilotphase bei f19_fit
    theta19_fit = icpt - 2 * math.pi * (f19_fit - f19) * 0  # icpt gilt fuer t=0 mit Mischfrequenz f19; Steigung getrennt
    diff = math.degrees(theta57 - 3 * theta19_fit) % 180.0
    if diff > 90:
        diff -= 180.0
    r["rds"] = {"level_khz": round(rms_d / shaper_rms, 4), "band_rms_khz": round(rms_d, 4),
                "phase_vs_pilot_deg": round(diff, 1)}
    if rds_phase_expect is not None:
        e = 0.0 if int(rds_phase_expect) == 0 else 90.0
        err = (diff - e + 90) % 180 - 90
        r["rds"]["phase_error_deg"] = round(err, 1)
    # 38-kHz-Restträger
    est38 = tone_estimate(mpx, fs, 2 * f19_fit, 15.0)
    r["subcarrier_38k_residual_khz"] = round(est38["amplitude"], 4)
    r["_f19"] = f19_fit
    r["_theta19"] = theta19_fit
    r["_z57_abs_peak"] = 2.0 * float(np.max(np.abs(z57d))) if len(z57d) else 0.0
    return r


def deemphasis(x, fs, tau):
    if not tau:
        return x
    b, a = signal.bilinear([1.0], [tau, 1.0], fs)
    return signal.lfilter(b, a, x)


def _tone_stats(x, fs, band=(20.0, 15000.0)):
    """Staerkste Linie, Pegel, SINAD (Linie gegen Rest im Band)."""
    n = len(x)
    w = np.hanning(n)
    X = np.fft.rfft(x * w)
    f = np.fft.rfftfreq(n, 1.0 / fs)
    P = np.abs(X) ** 2
    sel = (f >= band[0]) & (f <= band[1])
    if not sel.any():
        return {}
    idx = np.where(sel)[0]
    k = idx[np.argmax(P[idx])]
    est = tone_estimate(x, fs, f[k], max(5.0, fs / n * 4))
    bw_bins = max(3, int(round(8.0 * n / fs)) )  # +-8 Hz
    tone = P[max(0, k - bw_bins):k + bw_bins + 1].sum()
    rest = P[idx].sum() - tone
    return {"freq_hz": round(est["freq_hz"], 2), "amplitude": est["amplitude"],
            "sinad_db": round(10 * math.log10(tone / max(rest, 1e-20)), 2) if tone > 0 else None,
            "thd_n_pct": round(100 * math.sqrt(max(rest, 0) / max(tone, 1e-20)), 4) if tone > 0 else None,
            "_P": P, "_f": f, "_k": k}


def _amp_at(P, f, freq, n_bins=3):
    k = int(np.argmin(np.abs(f - freq)))
    return float(np.sqrt(P[max(0, k - n_bins):k + n_bins + 1].sum()))


def measure_audio(mpx, fs, f19, theta19, audio_dev_khz, tau_us, stereo=True, block_s=dsp.N_OUT / dsp.FS_OUT):
    """Stereo-Decoder: L+R (0-15 kHz), L-R (38 kHz mit regeneriertem Traeger aus der Pilotphase) -> L/R 48 kHz."""
    r = {}
    n = len(mpx)
    t = np.arange(n) / fs
    h = signal.firwin(1023, 15800.0, window=("kaiser", 8.0), fs=fs)   # 19 kHz um > 80 dB unterdrueckt
    m = signal.fftconvolve(mpx, h, mode="same")
    if stereo and f19 > 0:
        # Pilot = sin(2 pi f19 t + phi), phi = theta19 + 90 deg (Mischung mit exp(-j w t) liefert Phase - 90 deg)
        phi = theta19 + math.pi / 2
        s38 = np.sin(2 * (2 * math.pi * f19 * t + phi))
        s = signal.fftconvolve(mpx * 2.0 * s38, h, mode="same")
    else:
        s = np.zeros_like(m)
    r["mono_peak_khz"] = round(float(np.max(np.abs(m))), 3)
    r["diff_peak_khz"] = round(float(np.max(np.abs(s))), 3)
    L = (m + s) / max(audio_dev_khz, 1e-9)
    R = (m - s) / max(audio_dev_khz, 1e-9)
    tau = (tau_us or 0) * 1e-6
    L = deemphasis(L, fs, tau)
    R = deemphasis(R, fs, tau)
    L48 = signal.resample_poly(L, 3, 16)
    R48 = signal.resample_poly(R, 3, 16)
    k = int(0.1 * 48000)
    L48, R48 = L48[k:-k], R48[k:-k]
    sl, sr = _tone_stats(L48, 48000.0), _tone_stats(R48, 48000.0)
    out = {}
    for name, st, other in (("left", sl, sr), ("right", sr, sl)):
        if not st:
            continue
        d = {"tone_hz": st["freq_hz"], "level_dbfs": round(20 * math.log10(max(st["amplitude"], 1e-9)), 2),
             "sinad_db": st["sinad_db"], "thd_n_pct": st["thd_n_pct"]}
        # Uebersprechen: Amplitude dieser Tonfrequenz im anderen Kanal
        if other:
            a_here = _amp_at(st["_P"], st["_f"], st["freq_hz"])
            a_other = _amp_at(other["_P"], other["_f"], st["freq_hz"])
            d["crosstalk_to_other_db"] = round(20 * math.log10(max(a_other, 1e-12) / max(a_here, 1e-12)), 2)
        out[name] = d
    r.update(out)
    if sl and sr and abs(sl["freq_hz"] - sr["freq_hz"]) > 2.0:
        r["separation_db"] = {"L_to_R": round(-out["left"]["crosstalk_to_other_db"], 1),
                              "R_to_L": round(-out["right"]["crosstalk_to_other_db"], 1)}
    r["rms_dbfs"] = [round(20 * math.log10(max(float(np.sqrt(np.mean(L48 ** 2))), 1e-9)), 2),
                     round(20 * math.log10(max(float(np.sqrt(np.mean(R48 ** 2))), 1e-9)), 2)]
    # Blockgrenzen-Artefakte: Linien im Raster 1/Blocklaenge (12,5 Hz) im L-Spektrum, bezogen auf den Ton (dBc);
    # Harmonische des Tons und Vielfache der RDS-Bitrate (1187,5 Hz) werden ausgenommen
    f_blk = 1.0 / block_s
    if sl:
        P, f = sl["_P"], sl["_f"]
        res = f[1] - f[0]
        peak = float(P.max())
        lines = []
        tone = sl["freq_hz"]
        for kk in range(1, int(15000 / f_blk)):
            fc = kk * f_blk
            if abs(((fc + tone / 2) % tone) - tone / 2) < 3.0:
                continue
            if abs(((fc + 1187.5 / 2) % 1187.5) - 1187.5 / 2) < 3.0:
                continue
            i = int(round(fc / res))
            if i + 2 >= len(P):
                break
            line = P[i - 1:i + 2].max()
            lines.append((10 * math.log10(max(line, 1e-30) / peak), fc))
        if lines:
            lines.sort(reverse=True)
            r["block_grid_lines"] = {"grid_hz": f_blk, "max_dbc": round(lines[0][0], 1), "at_hz": lines[0][1],
                                     "top5_dbc": [[round(a, 1), b] for a, b in lines[:5]], "count": len(lines)}
    return r


def measure_mpx(mpx, fs=FS_MPX, expect=None):
    expect = expect or {}
    r = {}
    r["seconds"] = round(len(mpx) / fs, 2)
    a = np.abs(mpx)
    r["deviation"] = {"total_peak_khz": round(float(a.max()), 3), "p9999_khz": round(float(np.quantile(a, 0.9999)), 3),
                      "rms_khz": round(float(np.sqrt(np.mean(mpx ** 2))), 3)}
    r["mpx_power_dbr"] = round(10 * math.log10(float(np.mean(mpx ** 2)) / (19.0 ** 2 / 2.0)), 2)
    r["dc_offset_khz"] = round(float(np.mean(mpx)), 3)
    pr = measure_pilot_rds(mpx, fs, expect.get("rds_phase_deg"))
    f19, th19 = pr.pop("_f19"), pr.pop("_theta19")
    z57pk = pr.pop("_z57_abs_peak")
    r.update(pr)
    stereo = expect.get("stereo", r["pilot"]["level_khz"] > 1.0)
    audio_dev = expect.get("audio_deviation_khz") or max(1.0, r["deviation"]["total_peak_khz"] - r["pilot"]["level_khz"] - 2.0)
    try:
        r["audio"] = measure_audio(mpx, fs, f19, th19, audio_dev, expect.get("preemphasis_us", 50), stereo)
    except Exception as e:
        r["audio"] = {"error": repr(e)}
    # Audioanteil des Hubs: MPX ohne Pilot (19 kHz +- 60 Hz) und ohne RDS-Band (54..60 kHz) -> Spitze
    X = np.fft.rfft(mpx)
    fr = np.fft.rfftfreq(len(mpx), 1.0 / fs)
    X[(fr > f19 - 60) & (fr < f19 + 60)] = 0
    X[fr > 3 * f19 - 3000] = 0
    audio_mpx = np.fft.irfft(X, len(mpx))
    k = int(0.05 * fs)
    r["deviation"]["audio_peak_khz"] = round(float(np.max(np.abs(audio_mpx[k:-k]))), 3)
    r["deviation"]["components_sum_khz"] = round((r["deviation"]["audio_peak_khz"] or 0) + r["pilot"]["level_khz"]
                                                 + r["rds"]["level_khz"], 3)
    return r


# --------------------------------------------------------------------------------------------
# redsea
# --------------------------------------------------------------------------------------------
def run_redsea(mpx, fs, host=None, timeout=120):
    path = (host.tool("redsea", required=False) if host else None) or _which("redsea")
    if not path:
        return {"available": False, "error": "redsea nicht gefunden"}
    pcm = np.clip(np.round(mpx / 75.0 * 30000.0), -32768, 32767).astype("<i2").tobytes()
    cmd = [path, "--input", "mpx", "-r", str(int(fs)), "-c", "1", "-E", "-p"]
    try:
        pr = subprocess.run(cmd, input=pcm, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except Exception as e:
        return {"available": True, "error": repr(e)}
    agg = {"available": True, "groups": 0, "group_types": {}, "errors": 0, "ps_all": [], "rt_all": [], "ct_all": [],
           "rtplus_tags": {}, "eon": {}, "af_a": set(), "af_b": {}, "bler": [], "partial_ps": None, "partial_rt": None,
           "partial_lps": None}
    for line in pr.stdout.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            j = json.loads(line)
        except Exception:
            agg["errors"] += 1
            continue
        if "group" in j:
            agg["groups"] += 1
            agg["group_types"][j["group"]] = agg["group_types"].get(j["group"], 0) + 1
        if "bler" in j:
            agg["bler"].append(j["bler"])
        for k in ("pi", "ps", "radiotext", "prog_type", "tp", "ta", "is_music", "country", "language",
                  "prog_item_number", "prog_item_started", "pty_name", "long_ps", "clock_time", "rt_ab", "callsign"):
            if k in j:
                agg[k] = j[k]
        if isinstance(j.get("di"), dict):          # DI kommt bitweise je 0A-Segment -> zusammenfuehren
            agg.setdefault("di", {}).update(j["di"])
        if "ps" in j and j["ps"] not in agg["ps_all"]:
            agg["ps_all"].append(j["ps"])
        if "radiotext" in j and j["radiotext"] not in agg["rt_all"]:
            agg["rt_all"].append(j["radiotext"])
        if "clock_time" in j and j["clock_time"] not in agg["ct_all"]:
            agg["ct_all"].append(j["clock_time"])
        if "partial_ps" in j:
            agg["partial_ps"] = j["partial_ps"]
        if "partial_radiotext" in j:
            agg["partial_rt"] = j["partial_radiotext"]
        if "partial_long_ps" in j:
            agg["partial_lps"] = j["partial_long_ps"]
        if "alt_frequencies_a" in j:
            for f in j["alt_frequencies_a"]:
                agg["af_a"].add(_af_mhz(f))
        if "alt_frequencies_b" in j:
            b = j["alt_frequencies_b"]
            agg["af_b"] = {"tuned": _af_mhz(b.get("tuned_frequency")),
                           "same_programme": sorted(set(_af_mhz(x) for x in b.get("same_programme", []))),
                           "regional_variants": sorted(set(_af_mhz(x) for x in b.get("regional_variants", [])))}
        if "radiotext_plus" in j:
            rp = j["radiotext_plus"]
            agg["rtplus_last"] = rp
            for tg in rp.get("tags", []):
                agg["rtplus_tags"][str(tg.get("content-type"))] = tg.get("data")
        if "other_network" in j:
            on = j["other_network"]
            pi = on.get("pi")
            e = agg["eon"].setdefault(pi, {"pi": pi})
            for k, v in on.items():
                if k == "alt_frequencies":          # AF-Liste der anderen Kette (Variante 4), kHz
                    e.setdefault("af", set()).update(_af_mhz(x) for x in (v if isinstance(v, list) else [v]))
                elif k == "kilohertz":              # gemappte Frequenz (Varianten 5-8)
                    e.setdefault("mapped", set()).update(_af_mhz(x) for x in (v if isinstance(v, list) else [v]))
                else:
                    e[k] = v
    agg["af_a"] = sorted(agg["af_a"])
    for e in agg["eon"].values():
        for k in ("af", "mapped"):
            if k in e:
                e[k] = sorted(x for x in e[k] if x)
    agg["eon"] = list(agg["eon"].values())
    if agg["bler"]:
        agg["bler_mean_pct"] = round(float(np.mean(agg["bler"])), 2)
        agg["bler_last_pct"] = agg["bler"][-1]
    agg["bler"] = None
    agg["stderr"] = pr.stderr.decode("utf-8", "replace")[-500:]
    agg["version"] = _redsea_version(path)
    return agg


def _which(name):
    for d in ("/opt/dablab/bin", "/usr/local/bin", "/usr/bin"):
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _redsea_version(path):
    try:
        return subprocess.run([path, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=10).stdout.decode().strip().splitlines()[0]
    except Exception:
        return None


def _af_mhz(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v > 1000:
        return round(v / 1000.0, 1)
    return round(v, 1)


# --------------------------------------------------------------------------------------------
# Soll/Ist
# --------------------------------------------------------------------------------------------
def expectations(cfg, which=0):
    fm = cfg["fm"] if which == 0 else cfg["fm"]["second_carrier"]
    rds = fm["rds"]
    e = {"deviation_khz": fm["deviation_khz"], "stereo": fm["stereo"], "pilot_khz": fm["pilot_khz"] if fm["stereo"] else 0.0,
         "preemphasis_us": fm["preemphasis_us"], "rds_enable": rds["enable"], "rds_level_khz": rds["level_khz"] if rds["enable"] else 0.0,
         "rds_phase_deg": rds["phase_deg"], "source": fm["audio"]["source"]}
    e["audio_deviation_khz"] = max(0.0, fm["deviation_khz"] - e["pilot_khz"] - e["rds_level_khz"]) \
        if fm.get("audio_deviation_khz") in (None, "auto") else float(fm["audio_deviation_khz"])
    if rds["enable"]:
        enc = rdsmod.RdsEncoder(rds)
        e["rds"] = enc.snapshot()
        e["rds"]["pty_name"] = rdsmod.PTY_RDS[int(rds["pty"])]
        e["rds"]["ecc"] = rds["ecc"]
        e["rds"]["di"] = rds["di"]
        if fm["audio"]["source"].get("kind") == "playlist" and rds.get("rt_from_tags", True):
            e["rt_dynamic"] = True
            e["rds"]["rtplus"] = []      # Tags wechseln mit dem Titel
    src = fm["audio"]["source"]
    pre = dsp.Preemphasis(fm["preemphasis_us"])
    if src["kind"] in ("tone", "lr_test"):
        fl = float(src.get("left_hz", src.get("freq_hz", 1000)) if src["kind"] == "lr_test" else src.get("freq_hz", 1000))
        fr = float(src.get("right_hz", 400)) if src["kind"] == "lr_test" else fl
        lvl = float(src.get("level_dbfs", -6)) + fm.get("audio_gain_db", 0)
        gl, gr = 20 * math.log10(pre.gain_at(fl)), 20 * math.log10(pre.gain_at(fr))
        e["tone"] = {"left_hz": fl, "right_hz": fr, "level_dbfs": lvl, "preemph_gain_db": [round(gl, 2), round(gr, 2)],
                     "full_scale": src["kind"] == "tone" and lvl + gl >= -0.6}
        amp = 10 ** (lvl / 20.0)
        if src["kind"] == "tone":
            e["audio_peak_khz"] = round(e["audio_deviation_khz"] * amp * pre.gain_at(fl), 3)
        else:
            # L nur links, R nur rechts: (L+R)/2 und (L-R)/2 je halbe Amplitude, Summe = max(|L|,|R|)
            e["audio_peak_khz"] = round(e["audio_deviation_khz"] * amp * max(pre.gain_at(fl), pre.gain_at(fr)), 3)
    return e


def _txt(s):
    """Textvergleich: redsea gibt 0x8D (EN 50067: 'ß') als 'β' aus -> fuer den Vergleich gleichsetzen."""
    return (s or "").replace("β", "ß").rstrip()


def _field(fields, name, soll, ist, ok, note=None):
    d = {"name": name, "soll": soll, "ist": ist, "ok": bool(ok)}
    if note:
        d["note"] = note
    fields.append(d)
    return ok


def compare(meas, agg, exp, now_utc=None, lenient=False, ct_expected=True):
    """Liefert (fields, mismatches). lenient: Summensignal mit Stoertraeger -> nur Pilot/RDS-Inhalte bewerten."""
    F, M = [], []
    dev = meas["deviation"]
    tol = 0.02 * exp["deviation_khz"]
    full = (exp.get("tone") or {}).get("full_scale", False) and not lenient
    ok = abs(dev["total_peak_khz"] - exp["deviation_khz"]) <= tol
    _field(F, "Hub gesamt (Spitze) kHz", exp["deviation_khz"], dev["total_peak_khz"], ok if full else True,
           "Toleranz +-2 %" if full else "nur informativ (Quelle nicht vollausgesteuert / Stoertraeger)")
    if full and not ok:
        M.append("Hub: soll %.1f kHz +-2 %%, ist %.2f kHz" % (exp["deviation_khz"], dev["total_peak_khz"]))
    if exp.get("audio_peak_khz") is not None and dev.get("audio_peak_khz") is not None and not lenient:
        ok = abs(dev["audio_peak_khz"] - exp["audio_peak_khz"]) <= 0.03 * max(exp["audio_peak_khz"], 1)
        _field(F, "Hub Audioanteil kHz", exp["audio_peak_khz"], dev["audio_peak_khz"], ok, "Toleranz +-3 %")
        if not ok:
            M.append("Audiohub: soll %.2f, ist %.2f kHz" % (exp["audio_peak_khz"], dev["audio_peak_khz"]))
    p = meas["pilot"]["level_khz"]
    if exp["stereo"]:
        ok = abs(p - exp["pilot_khz"]) <= 0.05 * exp["pilot_khz"]
        _field(F, "Pilot kHz", exp["pilot_khz"], p, ok, "Toleranz +-5 %")
        if not ok:
            M.append("Pilot: soll %.2f kHz +-5 %%, ist %.3f kHz" % (exp["pilot_khz"], p))
        ok = abs(meas["pilot"]["freq_error_hz"]) <= 2.0
        _field(F, "Pilotfrequenz Hz", 19000.0, meas["pilot"]["freq_hz_fit"], ok, "Toleranz +-2 Hz (BS.450)")
        if not ok:
            M.append("Pilotfrequenz um %.2f Hz daneben" % meas["pilot"]["freq_error_hz"])
    else:
        ok = p < 0.3
        _field(F, "Pilot kHz (Mono: aus)", 0.0, p, ok)
        if not ok:
            M.append("Mono, aber Pilot %.2f kHz gemessen" % p)
    if exp["rds_enable"]:
        rl = meas["rds"]["level_khz"]
        ok = abs(rl - exp["rds_level_khz"]) <= 0.1 * exp["rds_level_khz"]
        _field(F, "RDS-Hub kHz", exp["rds_level_khz"], rl, ok, "Toleranz +-10 % (Effektivwertmethode)")
        if not ok:
            M.append("RDS-Hub: soll %.2f kHz, ist %.3f kHz" % (exp["rds_level_khz"], rl))
        if exp["stereo"]:
            err = meas["rds"].get("phase_error_deg")
            ok = err is not None and abs(err) <= 10.0
            _field(F, "57-kHz-Phase zum Pilot Grad", exp["rds_phase_deg"], meas["rds"]["phase_vs_pilot_deg"], ok, "Toleranz +-10 Grad")
            if not ok:
                M.append("57-kHz-Phase: soll %d Grad, ist %.1f Grad" % (exp["rds_phase_deg"], meas["rds"]["phase_vs_pilot_deg"]))
    # Audio
    au = meas.get("audio") or {}
    tone = exp.get("tone")
    if tone and "left" in au and not lenient:
        for side in ("left", "right"):
            if side in au and au[side].get("tone_hz") is not None:
                fh = tone["left_hz"] if side == "left" else tone["right_hz"]
                ok = abs(au[side]["tone_hz"] - fh) <= 2.0
                _field(F, "Ton %s Hz" % side, fh, au[side]["tone_hz"], ok)
                if not ok:
                    M.append("Ton %s: soll %.0f Hz, ist %.1f Hz" % (side, fh, au[side]["tone_hz"]))
                ok = abs(au[side]["level_dbfs"] - tone["level_dbfs"]) <= 0.5
                _field(F, "Pegel %s dBFS (nach De-Emphasis)" % side, tone["level_dbfs"], au[side]["level_dbfs"], ok, "Toleranz +-0,5 dB")
                if not ok:
                    M.append("Audiopegel %s: soll %.1f dBFS, ist %.2f dBFS" % (side, tone["level_dbfs"], au[side]["level_dbfs"]))
        if "separation_db" in au:
            sep = au["separation_db"]
            ok = min(sep["L_to_R"], sep["R_to_L"]) >= 40.0
            _field(F, "Kanaltrennung dB (L->R, R->L)", ">= 40", [sep["L_to_R"], sep["R_to_L"]], ok)
            if not ok:
                M.append("Kanaltrennung %s dB" % sep)
    if au.get("block_grid_lines") and tone and not lenient:
        ok = au["block_grid_lines"]["max_dbc"] < -70.0
        _field(F, "Blockgrenzen-Linien (12,5-Hz-Raster) dBc", "< -70", au["block_grid_lines"]["max_dbc"], ok,
               "staerkste bei %.1f Hz" % au["block_grid_lines"]["at_hz"])
        if not ok:
            M.append("Spektrallinie im Blockraster: %.1f dBc bei %.1f Hz" % (au["block_grid_lines"]["max_dbc"], au["block_grid_lines"]["at_hz"]))
    # RDS-Inhalte
    if exp["rds_enable"] and agg and agg.get("available"):
        er = exp["rds"]
        _field(F, "RDS-Gruppen (redsea)", "> 0", agg.get("groups"), (agg.get("groups") or 0) > 0)
        if not agg.get("groups"):
            M.append("redsea hat keine RDS-Gruppen dekodiert")
        ok = (agg.get("pi") or "").upper() == er["pi"].upper()
        _field(F, "PI", er["pi"], agg.get("pi"), ok)
        if not ok:
            M.append("PI: soll %s, ist %s" % (er["pi"], agg.get("pi")))
        ps_soll = _txt(er["ps"])
        ok = _txt(agg.get("ps")) == ps_soll or ps_soll in [_txt(x) for x in agg.get("ps_all", [])]
        _field(F, "PS", ps_soll, agg.get("ps"), ok)
        if not ok:
            M.append("PS: soll '%s', ist '%s' (teilweise: %r)" % (ps_soll, agg.get("ps"), agg.get("partial_ps")))
        if exp.get("rt_dynamic"):
            _field(F, "RT (aus Playlist-Tags)", "Interpret - Titel", agg.get("radiotext"), bool(agg.get("radiotext")),
                   "rt_from_tags: Sollwert wechselt mit dem Titel, nur Vorhandensein geprueft")
            if not agg.get("radiotext"):
                M.append("RT: kein Radiotext dekodiert")
        elif er["rt"]:
            rts = [_txt(x) for x in agg.get("rt_all", [])] + ([_txt(agg.get("radiotext"))] if agg.get("radiotext") else [])
            ok = _txt(er["rt"]) in rts
            _field(F, "RT", er["rt"], agg.get("radiotext"), ok)
            if not ok:
                M.append("RT: soll '%s', ist %r (teilweise %r)" % (er["rt"], agg.get("radiotext"), agg.get("partial_rt")))
        ok = agg.get("prog_type") == er["pty_name"] or (agg.get("prog_type") or "").lower() == er["pty_name"].lower()
        _field(F, "PTY", er["pty_name"], agg.get("prog_type"), ok)
        if not ok:
            M.append("PTY: soll %s, ist %s" % (er["pty_name"], agg.get("prog_type")))
        for k, nm in (("tp", "TP"), ("ta", "TA")):
            ok = agg.get(k) is not None and bool(agg.get(k)) == bool(er[k])
            _field(F, nm, er[k], agg.get(k), ok)
            if not ok:
                M.append("%s: soll %s, ist %s" % (nm, er[k], agg.get(k)))
        ok = agg.get("is_music") is not None and bool(agg.get("is_music")) == bool(er["ms"])
        _field(F, "MS (Musik)", er["ms"], agg.get("is_music"), ok)
        if not ok:
            M.append("MS: soll %s, ist %s" % (er["ms"], agg.get("is_music")))
        di = er.get("di")
        if isinstance(di, dict) and isinstance(agg.get("di"), dict):
            ok = bool(agg["di"].get("stereo")) == bool(di.get("stereo", True))
            _field(F, "DI Stereo-Bit", bool(di.get("stereo", True)), agg["di"].get("stereo"), ok)
            if not ok:
                M.append("DI Stereo: soll %s, ist %s" % (di.get("stereo"), agg["di"]))
        if er["af"]:
            if er["af_method"] == "A":
                ist = agg.get("af_a") or []
                ok = sorted(round(x, 1) for x in er["af"]) == sorted(ist)
                _field(F, "AF (Methode A) MHz", sorted(er["af"]), ist, ok)
            else:
                b = agg.get("af_b") or {}
                ist = sorted(set((b.get("same_programme") or []) + (b.get("regional_variants") or [])
                                 + ([b["tuned"]] if b.get("tuned") else [])))
                ok = bool(b) and sorted(set(round(x, 1) for x in er["af"])) == ist
                _field(F, "AF (Methode B) MHz", sorted(er["af"]), b, ok)
            if not ok:
                M.append("AF: soll %s, ist %s" % (sorted(er["af"]), ist))
        if er.get("ecc") is not None:
            cc = (int(er["pi"], 16) >> 12) & 0xF
            soll = COUNTRY.get(int(er["ecc"]), {}).get(cc)
            ist = agg.get("country")
            ok = ist is not None and (soll is None or ist == soll)
            _field(F, "ECC/Land (1A)", "ECC 0x%02X -> %s" % (int(er["ecc"]), soll), ist, ok)
            if not ok:
                M.append("ECC: 1A nicht dekodiert oder Land %s statt %s" % (ist, soll))
        if er.get("ct"):
            ct = agg.get("clock_time")
            ok = bool(ct)
            if ct and now_utc:
                try:
                    import datetime
                    d = datetime.datetime.fromisoformat(ct.replace("Z", "+00:00"))
                    ok = abs((d - datetime.datetime.fromtimestamp(now_utc, tz=datetime.timezone.utc)).total_seconds()) < 180
                except Exception:
                    pass
            if ct_expected or ct:
                _field(F, "CT (4A)", "Uhrzeit innerhalb 3 min", ct, ok)
                if not ok:
                    M.append("CT: keine oder falsche Uhrzeit (%s)" % ct)
            else:
                _field(F, "CT (4A)", "nur an der Minutenkante", None, True, "Mitschnitt kuerzer als 1 min ohne Minutenkante")
        if er.get("ptyn"):
            ok = _txt(agg.get("pty_name")) == _txt(er["ptyn"])
            _field(F, "PTYN (10A)", er["ptyn"], agg.get("pty_name"), ok)
            if not ok:
                M.append("PTYN: soll '%s', ist %r" % (er["ptyn"], agg.get("pty_name")))
        if er.get("lps"):
            ok = (agg.get("long_ps") or "").rstrip() == er["lps"].rstrip()
            _field(F, "LPS (15A, UTF-8)", er["lps"], agg.get("long_ps"), ok)
            if not ok:
                M.append("LPS: soll '%s', ist %r (teilweise %r)" % (er["lps"], agg.get("long_ps"), agg.get("partial_lps")))
        for tg in er.get("rtplus") or []:
            name = str(tg["type"]).lower()
            ist = (agg.get("rtplus_tags") or {}).get(name)
            ok = ist is not None and _txt(ist).strip() == _txt(tg["text"]).strip()
            _field(F, "RT+ %s" % name, tg["text"], ist, ok)
            if not ok:
                M.append("RT+ %s: soll '%s', ist %r" % (name, tg["text"], ist))
        for eo in er.get("eon") or []:
            ist = next((x for x in agg.get("eon", []) if (x.get("pi") or "").upper() == eo["pi"].upper()), None)
            ok = ist is not None
            if ok and eo.get("ps"):
                ok = _txt(ist.get("ps")) == _txt(eo["ps"])
            if ok and eo.get("af"):
                ok = set(round(x, 1) for x in eo["af"]) <= set(ist.get("af") or [])
            _field(F, "EON %s (14A)" % eo["pi"], {k: eo[k] for k in ("ps", "af", "tp", "ta", "pty")}, ist, ok)
            if not ok:
                M.append("EON %s: soll %s, ist %s" % (eo["pi"], eo, ist))
        bl = agg.get("bler_mean_pct")
        ok = bl is not None and bl <= 2.0
        _field(F, "BLER %", "<= 2", bl, ok or lenient, "Stoertraeger: informativ" if lenient else None)
        if bl is not None and not ok and not lenient:
            M.append("BLER %.1f %%" % bl)
    elif exp["rds_enable"]:
        M.append("redsea nicht verfuegbar: RDS-Inhalte nicht geprueft")
    return F, M


# --------------------------------------------------------------------------------------------
# Gesamtablauf
# --------------------------------------------------------------------------------------------
def _measure(mpx, cfg, which, host, want_rds, now_utc, lenient=False, ct_expected=None):
    exp = expectations(cfg, which) if cfg else None
    meas = measure_mpx(mpx, FS_MPX, exp)
    agg = None
    if want_rds and (exp is None or exp["rds_enable"]) and meas["rds"]["level_khz"] > 0.1:
        agg = run_redsea(mpx, FS_MPX, host)
    out = {"measured": meas, "redsea": agg}
    if exp:
        if ct_expected is None:
            ct_expected = len(mpx) / FS_MPX >= 61.0
        F, M = compare(meas, agg, exp, now_utc, lenient=lenient, ct_expected=ct_expected)
        out["expected"] = {k: v for k, v in exp.items() if k != "source"}
        out["fields"] = F
        out["mismatches"] = M
    return out


def measure_iq(iq, fs, cfg=None, host=None, want_rds=True, shift_hz=0.0):
    mpx = demod_array(iq, fs, shift_hz)
    o = _measure(mpx, cfg, 0, host, want_rds, time.time())
    r = dict(o["measured"])
    r["redsea"] = _rds_summary(o["redsea"])
    r["fields"] = o.get("fields")
    r["mismatches"] = o.get("mismatches")
    return r


def _rds_summary(agg):
    if not agg:
        return None
    return {k: agg.get(k) for k in ("available", "groups", "group_types", "pi", "ps", "radiotext", "prog_type", "tp", "ta",
                                    "is_music", "di", "af_a", "af_b", "country", "language", "clock_time", "pty_name",
                                    "long_ps", "rtplus_tags", "rtplus_last", "eon", "bler_mean_pct", "prog_item_number",
                                    "ps_all", "rt_all", "ct_all", "partial_ps", "partial_rt", "version", "error")}


def analyze(path, rate, fmt, cfg=None, seconds=20.0, host=None, rds_sent=None, shift_hz=0.0, interference=False,
            ct_expected=None, which=0):
    """which = 0: Nutztraeger (Datei ggf. mit Stoertraeger -> interference=True: nachsichtiger Vergleich),
    which = 1: Zweittraeger allein (Datei aus dem Solo-Render, shift_hz = Versatz)."""
    t0 = time.time()
    rep = {"ok": False, "mode": "fm", "iq": {"file": path, "rate": int(rate), "format": fmt,
                                             "bytes": os.path.getsize(path) if os.path.exists(path) else None}}
    if not os.path.exists(path):
        rep["error"] = "Datei fehlt: %s" % path
        return rep
    try:
        mpx = demod_file(path, int(rate), fmt, float(seconds), shift_hz)
    except Exception as e:
        rep["error"] = "Demodulation fehlgeschlagen: %r" % e
        return rep
    if len(mpx) < FS_MPX:
        rep["error"] = "zu wenig Signal (%.2f s)" % (len(mpx) / FS_MPX)
        return rep
    rep["iq"]["seconds_analyzed"] = round(len(mpx) / FS_MPX, 2)
    o = _measure(mpx, cfg, which, host, True, time.time(), lenient=interference, ct_expected=ct_expected)
    rep["measured"] = o["measured"]
    rep["rds"] = _rds_summary(o["redsea"])
    rep["expected"] = o.get("expected")
    rep["fields"] = o.get("fields") or []
    rep["mismatches"] = o.get("mismatches") or []
    if interference:
        rep["note"] = "Summensignal mit Stoertraeger: Hub/Audio/BLER nur informativ"
    if rds_sent:
        rep["rds_sent"] = rds_sent
    if which == 0 and cfg and cfg["fm"].get("second_carrier", {}).get("enable"):
        # Zweittraeger im Summensignal (mit Stoerung durch den Nutztraeger) - nur informativ
        sc = cfg["fm"]["second_carrier"]
        try:
            mpx2 = demod_file(path, int(rate), fmt, float(seconds), shift_hz + sc["offset_khz"] * 1000.0)
            o2 = _measure(mpx2, cfg, 1, host, True, time.time(), lenient=True, ct_expected=False)
            rep["second_carrier_in_sum"] = {"offset_khz": sc["offset_khz"], "level_db": sc["level_db"],
                                            "measured": o2["measured"], "rds": _rds_summary(o2["redsea"]),
                                            "fields": o2.get("fields"), "mismatches_informational": o2.get("mismatches")}
        except Exception as e:
            rep["second_carrier_in_sum"] = {"error": repr(e)}
    rep["ok"] = not rep["mismatches"] if cfg else True
    rep["elapsed_analyze_s"] = round(time.time() - t0, 1)
    return rep
