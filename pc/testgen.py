"""Stand-in IQ source with the runner's `live` interface (used while no runner is installed).

Writes DAB-mode-I-like OFDM (1536 random QPSK carriers, 1 kHz spacing, guard interval and null
symbol, 1.536 MHz occupied) as int16 LE interleaved I/Q to stdout, blocking.
The reader (Pluto) sets the pace. stderr: JSON lines like the runner.
"""
import argparse
import json
import os
import signal
import sys
import time

import numpy as np


def log(**kw):
    kw.setdefault("t", round(time.time(), 3))
    sys.stderr.write(json.dumps(kw) + "\n")
    sys.stderr.flush()


def make_frames(fs, nframes, backoff_db, seed=1):
    k = int(round(fs / 2048000))          # oversampling vs. 2.048 MS/s
    nfft, guard, null = 2048 * k, 504 * k, 2656 * k
    rng = np.random.default_rng(seed)
    bins = np.r_[np.arange(-768, 0), np.arange(1, 769)] % nfft
    frames = []
    for _ in range(nframes):
        parts = [np.zeros(null, np.complex64)]
        for _s in range(76):
            X = np.zeros(nfft, np.complex64)
            X[bins] = (rng.choice([-1, 1], 1536) + 1j * rng.choice([-1, 1], 1536)) / np.sqrt(2)
            x = np.fft.ifft(X).astype(np.complex64)
            parts.append(np.r_[x[-guard:], x])
        frames.append(np.concatenate(parts))
    sig = np.concatenate(frames)
    active = sig[np.abs(sig) > 0]
    rms = np.sqrt(np.mean(np.abs(active) ** 2))
    sig *= 32767 * 10 ** (-backoff_db / 20) / rms
    iq = np.empty(2 * len(sig), np.float32)
    iq[0::2], iq[1::2] = sig.real, sig.imag
    return np.clip(np.rint(iq), -32767, 32767).astype("<i2").tobytes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="live")
    ap.add_argument("--config")
    ap.add_argument("--rate", type=int, default=None)
    ap.add_argument("--frames", type=int, default=8)
    a = ap.parse_args()
    fs, backoff = 4096000, 12.0
    if a.config and os.path.exists(a.config):
        try:
            rf = json.load(open(a.config, encoding="utf-8")).get("rf", {})
            fs = int(rf.get("sample_rate", fs))
            backoff = float(rf.get("backoff_db", backoff))
        except Exception as e:
            log(lvl="warn", msg=f"config: {e}")
    if a.rate:
        fs = a.rate
    log(session=f"testgen-{os.getpid()}")
    t0 = time.time()
    data = make_frames(fs, a.frames, backoff)
    log(lvl="info", msg=f"Testgenerator: OFDM 1,536 MHz, {fs} S/s, Backoff {backoff} dB, "
                        f"{len(data) // 4} Samples Schleife ({time.time() - t0:.1f} s Aufbau)")
    try:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    except Exception:
        pass
    out = sys.stdout.buffer
    mv = memoryview(data)
    step = 1 << 18
    try:
        while True:
            for i in range(0, len(mv), step):
                out.write(mv[i:i + step])
    except (BrokenPipeError, OSError, KeyboardInterrupt, SystemExit):
        pass
    finally:
        try:
            sys.stderr.write(json.dumps({"t": time.time(), "lvl": "info", "msg": "Testgenerator beendet"}) + "\n")
        except Exception:
            pass
        os._exit(0)


if __name__ == "__main__":
    main()
