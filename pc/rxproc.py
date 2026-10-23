"""RX capture for /api/monitor in a separate process.

The TX stream lives in the server process on `usb:`. Capturing RX in the same process costs
DAC underruns (GIL + shared USB control pipe). So the capture runs here, in a child process
(multiprocessing "spawn") with its own libiio context, by default over `ip:192.168.2.1`
(iiod serves both contexts independently). The child also does the heavy numpy work
(Welch spectrum, 2:1 decimation, test-generator correlation) and writes the 2.048 MS/s cf32
file. The parent only receives a small JSON-able dict.

Can also be run directly:  python rxproc.py --seconds 4 --gain 60 --lo 185360000 --out x.cf32.iq
"""
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

RX_NBUF = 1 << 16          # small RX buffers: keeps iiod latency low
RX_KBUFS = 4


def _capture(uri, seconds, gain_db, lo_hz, nbuf=RX_NBUF, set_rate=None):
    """-> (complex64 array scaled to +-1, fs). Own context, nothing touches TX attributes."""
    from iio_load import load
    iio = load()
    ctx = iio.Context(uri)
    ctx.set_timeout(5000)
    phy = ctx.find_device("ad9361-phy")
    adc = ctx.find_device("cf-ad9361-lpc")
    if phy is None or adc is None:
        raise RuntimeError("ad9361-Geräte fehlen")
    rx_in = phy.find_channel("voltage0", False)
    if set_rate:
        phy.find_channel("voltage0", True).attrs["sampling_frequency"].value = str(int(set_rate))
    fs = int(rx_in.attrs["sampling_frequency"].value)
    phy.find_channel("altvoltage0", True).attrs["frequency"].value = str(int(round(lo_hz)))
    rx_lo = int(phy.find_channel("altvoltage0", True).attrs["frequency"].value)
    rx_in.attrs["gain_control_mode"].value = "manual"
    g = int(max(-3, min(71, round(gain_db))))
    rx_in.attrs["hardwaregain"].value = str(g)
    for n in ("voltage0", "voltage1"):
        adc.find_channel(n, False).enabled = True
    try:
        adc.set_kernel_buffers_count(RX_KBUFS)
    except Exception:
        pass
    total = int(seconds * fs)
    nb = (total + nbuf - 1) // nbuf
    out = np.empty(nb * nbuf, dtype=np.complex64)
    buf = iio.Buffer(adc, nbuf, False)
    try:
        for _ in range(3):                  # settle LO/gain
            buf.refill()
        for i in range(nb):
            buf.refill()
            raw = np.frombuffer(buf.read(), dtype="<i2").astype(np.float32)
            out[i * nbuf:(i + 1) * nbuf] = (raw[0::2] + 1j * raw[1::2]) / 2048.0
    finally:
        try:
            buf.cancel()
        except Exception:
            pass
        del buf
        del ctx
    return out[:total], fs, rx_lo, g


def spectrum_metrics(x, fs, band_hz=(5e3, 760e3), adj_hz=(900e3, None)):
    """Welch PSD; mean power in |f| within band_hz vs. adj_hz (upper edge None = 0,42·fs). adj None/empty -> null."""
    from scipy.signal import welch
    f, p = welch(x, fs=fs, nperseg=4096, return_onesided=False, detrend=False, scaling="density")
    order = np.argsort(f)
    f, p = f[order], p[order]
    af = np.abs(f)
    band = (af > band_hz[0]) & (af < band_hz[1])
    a_hi = (adj_hz[1] if adj_hz and adj_hz[1] else 0.42 * fs) if adj_hz else 0
    adj = (af > adj_hz[0]) & (af < a_hi) if adj_hz else np.zeros_like(af, bool)

    def db(v):
        return float(10 * np.log10(max(float(v), 1e-30)))
    in_db = db(np.mean(p[band])) if band.any() else None
    adj_db = db(np.mean(p[adj])) if adj.any() else None
    nb = 512
    pd = 10 * np.log10(p.reshape(nb, -1).mean(axis=1) + 1e-30)
    fd = f.reshape(nb, -1).mean(axis=1)
    rms = float(np.sqrt(np.mean(np.abs(x) ** 2)))
    clip = float(np.mean((np.abs(x.real) > 0.99) | (np.abs(x.imag) > 0.99)) * 100)
    return {"in_band_db": round(in_db, 2) if in_db is not None else None,
            "adjacent_db": round(adj_db, 2) if adj_db is not None else None,
            "delta_db": round(in_db - adj_db, 2) if in_db is not None and adj_db is not None else None,
            "band_hz": list(band_hz), "adjacent_hz": [adj_hz[0], a_hi] if adj_hz else None,
            "rms_dbfs": round(float(20 * np.log10(max(rms, 1e-12))), 2), "clip_pct": round(clip, 4),
            "psd_db": [round(float(v), 1) for v in pd], "psd_f_hz": [round(float(v)) for v in fd]}


_TPL = {}


def testgen_correlation(y, fs, backoff):
    """Detect the known test-generator frame in a 2:1-decimated capture (processing gain ~50 dB)."""
    from scipy.signal import fftconvolve, resample_poly
    import testgen
    key = (int(fs), backoff)
    if key not in _TPL:
        raw = np.frombuffer(testgen.make_frames(int(round(fs)), 1, backoff), dtype="<i2").astype(np.float32)
        ref = (raw[0::2] + 1j * raw[1::2]) / 32768.0
        _TPL.clear()
        _TPL[key] = resample_poly(ref, 1, 2).astype(np.complex64)
    tpl = _TPL[key]
    F = len(tpl)
    seg = y[:min(len(y), int(F * 9.2))]          # generator loops 8 frames
    if len(seg) < F * 9.05:
        return {"error": "Mitschnitt zu kurz"}
    cc = fftconvolve(seg, np.conj(tpl[::-1]), mode="valid")
    mag = np.abs(cc)
    pk = int(mag.argmax())
    e_tpl = float(np.sum(np.abs(tpl) ** 2))
    a = cc[pk] / e_tpl
    p_sig = float(abs(a) ** 2 * e_tpl / F)
    p_tot = float(np.mean(np.abs(seg[pk:pk + F]) ** 2))
    p_noise = max(p_tot - p_sig, 1e-30)
    return {"peak_to_median_db": round(float(20 * np.log10(mag[pk] / np.median(mag))), 1), "lag": pk,
            "signal_dbfs": round(10 * np.log10(max(p_sig, 1e-30)), 1),
            "total_dbfs": round(10 * np.log10(max(p_tot, 1e-30)), 1),
            "snr_db": round(10 * np.log10(max(p_sig, 1e-30) / p_noise), 1),
            "detected": bool(mag[pk] / np.median(mag) > 10 ** (25 / 20))}


def worker(uri, seconds, gain_db, lo_hz, out_path, corr_backoff=None, set_rate=None):
    """Whole monitor job. Returns a dict (JSON-able)."""
    from scipy.signal import resample_poly
    t0 = time.time()
    x, fs, rx_lo, g = _capture(uri, seconds, gain_db, lo_hz, set_rate=set_rate)
    t_cap = time.time() - t0
    spec = spectrum_metrics(x, fs)
    y = resample_poly(x, 1, 2).astype(np.complex64)
    del x
    corr = None
    if corr_backoff is not None:
        try:
            corr = testgen_correlation(y, fs, float(corr_backoff))
        except Exception as e:
            corr = {"error": str(e)}
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    y.tofile(out_path)
    return {"ok": True, "uri": uri, "fs_capture": fs, "fs_file": int(round(fs / 2)), "rx_lo_hz": rx_lo,
            "rx_gain_db": g, "capture_s": round(t_cap, 2), "total_s": round(time.time() - t0, 2),
            "samples_file": int(len(y)), "spectrum": spec, "testgen_corr": corr}


def _entry(q, kw):
    try:
        try:                                    # child runs below the TX process
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL
        except Exception:
            pass
        q.put(worker(**kw))
    except Exception as e:
        q.put({"ok": False, "error": f"{type(e).__name__}: {e}"})


def capture(uri, seconds, gain_db, lo_hz, out_path, corr_backoff=None, set_rate=None, timeout=None):
    """Run worker() in a spawned child process. Raises RuntimeError on failure."""
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    kw = dict(uri=uri, seconds=seconds, gain_db=gain_db, lo_hz=lo_hz, out_path=out_path,
              corr_backoff=corr_backoff, set_rate=set_rate)
    p = ctx.Process(target=_entry, args=(q, kw), name="rx-capture", daemon=True)
    p.start()
    deadline = time.time() + (timeout or (seconds * 4 + 90))
    res = None
    try:
        while True:
            try:
                res = q.get(timeout=0.5)
                break
            except Exception:
                pass
            if not p.is_alive():
                try:                                    # result may have been queued just before exit
                    res = q.get(timeout=1.0)
                    break
                except Exception:
                    raise RuntimeError(f"RX-Prozess abgestürzt (Exit {p.exitcode})")
            if time.time() > deadline:
                p.kill()
                raise RuntimeError("RX-Prozess antwortet nicht (Zeitüberschreitung)")
    finally:
        p.join(5)
        if p.is_alive():
            p.kill()
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "RX-Prozess fehlgeschlagen"))
    return res


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default="ip:192.168.2.1")
    ap.add_argument("--seconds", type=float, default=4)
    ap.add_argument("--gain", type=float, default=60)
    ap.add_argument("--lo", type=float, default=185360000)
    ap.add_argument("--out", default=os.path.join(HERE, "..", "runtime", "rx", "manual.cf32.iq"))
    ap.add_argument("--inproc", action="store_true", help="ohne Kindprozess")
    a = ap.parse_args()
    fn = worker if a.inproc else capture
    r = fn(a.uri, a.seconds, a.gain, a.lo, os.path.abspath(a.out))
    r["spectrum"].pop("psd_db", None)
    r["spectrum"].pop("psd_f_hz", None)
    print(json.dumps(r, indent=1))
