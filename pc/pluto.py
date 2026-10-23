"""ADALM-Pluto access for DAB-Lab (libiio over USB).

Safety: every connect, error, stop and shutdown sets TX hardwaregain to -89.75 dB.
No code path writes a hardwaregain above 0 dB or below -89.75 dB.
"""
import collections
import queue
import threading
import time

import numpy as np

from iio_load import load as _load_iio

MUTE_DB = -89.75
REG_DAC_UNF = 0x88          # cf-ad9361-dds-core-lpc: bit0 = DAC underflow (write 1 to clear)

_iio = None
_iio_err = None


def set_thread_priority(level):
    """Windows thread priority: 1 = ABOVE_NORMAL, 2 = HIGHEST, 15 = TIME_CRITICAL. No-op elsewhere."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        return bool(k.SetThreadPriority(k.GetCurrentThread(), int(level)))
    except Exception:
        return False


def set_process_priority(cls=0x8000):
    """Windows process priority class: 0x8000 = ABOVE_NORMAL, 0x80 = HIGH."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        return bool(k.SetPriorityClass(k.GetCurrentProcess(), int(cls)))
    except Exception:
        return False


def iio_module():
    global _iio, _iio_err
    if _iio is None and _iio_err is None:
        try:
            _iio = _load_iio()
        except Exception as e:      # DLL missing etc.
            _iio_err = f"{type(e).__name__}: {e}"
    return _iio


def clamp_gain(atten_db):
    """atten (positive dB) -> hardwaregain in [-89.75, 0], 0.25 dB steps."""
    g = -float(atten_db)
    g = max(MUTE_DB, min(0.0, g))
    return round(g * 4) / 4


class PlutoError(Exception):
    pass


class Pluto:
    def __init__(self, uri="usb:", log=print):
        self.uri = uri
        self.log = log
        self.lock = threading.RLock()      # control attributes
        self.ctx = None
        self.phy = None
        self.dds = None
        self.adc = None
        self.info = {"connected": False, "uri": uri, "fw": None, "temp_c": None,
                     "lo_hz": None, "rx_lo_hz": None, "atten_db": None, "model": None,
                     "serial": None, "error": None}
        self.fs = None
        self.rx_busy = False
        self.ctl_retries = 0
        self.passive = False

    # ---------------------------------------------------------------- connection
    @property
    def connected(self):
        return self.ctx is not None

    def connect(self, passive=False):
        """passive=True: open the context but write nothing (an on-device modulator owns the TX path)."""
        iio = iio_module()
        if iio is None:
            self.info["error"] = f"pylibiio: {_iio_err}"
            return False
        with self.lock:
            if self.ctx is not None:
                return True
            try:
                ctx = iio.Context(self.uri)
                phy = ctx.find_device("ad9361-phy")
                dds = ctx.find_device("cf-ad9361-dds-core-lpc")
                adc = ctx.find_device("cf-ad9361-lpc")
                if not (phy and dds and adc):
                    raise PlutoError("ad9361-Geräte fehlen")
                self.ctx, self.phy, self.dds, self.adc = ctx, phy, dds, adc
                self.passive = passive
                if not passive:
                    self.mute()
                # DDS tones off, so nothing comes out when no buffer is active
                for ch in ([] if passive else dds.channels):
                    if ch.output and ch.id.startswith("altvoltage") and "scale" in ch.attrs:
                        try:
                            ch.attrs["scale"].value = "0"
                        except Exception:
                            pass
                a = ctx.attrs
                self.info.update(connected=True, error=None,
                                 uri=a.get("uri", self.uri),
                                 fw=a.get("fw_version"), model=a.get("hw_model"),
                                 serial=a.get("hw_serial"),
                                 chip=a.get("ad9361-phy,model"))
                self.log("info", f"Pluto verbunden: {self.info['uri']} fw {self.info['fw']} ({self.info['model']})" + (" – passiv, Modulator auf dem Gerät aktiv" if passive else ""))
                self.poll()
                return True
            except Exception as e:
                self.ctx = self.phy = self.dds = self.adc = None
                self.info.update(connected=False, error=str(e))
                return False

    def drop(self, why=""):
        with self.lock:
            if self.ctx is not None:
                self.log("warn", f"Pluto getrennt {why}".strip())
            self.ctx = self.phy = self.dds = self.adc = None
            self.info.update(connected=False, temp_c=None)

    def close(self):
        with self.lock:
            try:
                self.mute()
            except Exception:
                pass
            self.drop()

    # ---------------------------------------------------------------- attributes
    def _retry(self, fn, tries=4):
        """Control-pipe access while streaming fails rarely with EIO (measured ~1 per 5000 reads
        at 4 MS/s TX). Retry instead of treating it as a disconnect."""
        for i in range(tries):
            try:
                return fn()
            except Exception as e:
                if i == tries - 1 or self.phy is None:
                    raise
                self.ctl_retries += 1
                if self.ctl_retries <= 5 or self.ctl_retries % 50 == 0:
                    self.log("warn", f"Steuerzugriff wiederholt ({e}), insgesamt {self.ctl_retries}")
                time.sleep(0.02 * (i + 1))

    def _ch(self, name, out):
        c = self.phy.find_channel(name, out)
        if c is None:
            raise PlutoError(f"Kanal {name} fehlt")
        return c

    def _set(self, name, out, attr, value):
        with self.lock:
            if self.phy is None:
                raise PlutoError("Pluto nicht verbunden")
            ch = self._ch(name, out)

            def w():
                ch.attrs[attr].value = str(value)
            self._retry(w)

    def _get(self, name, out, attr):
        with self.lock:
            if self.phy is None:
                raise PlutoError("Pluto nicht verbunden")
            ch = self._ch(name, out)
            return self._retry(lambda: ch.attrs[attr].value)

    def mute(self):
        """TX hardwaregain -89.75 dB. Never raises if not connected."""
        with self.lock:
            if self.phy is None:
                return False
            self._set("voltage0", True, "hardwaregain", MUTE_DB)
            self.info["atten_db"] = -MUTE_DB
            return True

    def set_atten(self, atten_db):
        g = clamp_gain(atten_db)
        self._set("voltage0", True, "hardwaregain", f"{g:.2f}")
        rb = self.read_gain()
        self.info["atten_db"] = -rb
        return -rb

    def read_gain(self):
        v = self._get("voltage0", True, "hardwaregain")
        return float(v.split()[0])

    def set_tx_lo(self, hz):
        self._set("altvoltage1", True, "frequency", int(round(hz)))
        self._set("altvoltage1", True, "powerdown", 0)
        self.info["lo_hz"] = int(self._get("altvoltage1", True, "frequency"))
        return self.info["lo_hz"]

    def set_rx_lo(self, hz):
        self._set("altvoltage0", True, "frequency", int(round(hz)))
        self.info["rx_lo_hz"] = int(self._get("altvoltage0", True, "frequency"))
        return self.info["rx_lo_hz"]

    def set_rate(self, fs):
        fs = int(fs)
        # a FIR left over from an on-device modulator (RRC, INT/DEC 2) would distort the USB modes: switch it off
        for name, out in (("voltage0", True), ("voltage0", False)):
            try:
                if self._get(name, out, "filter_fir_en").strip() != "0":
                    self._set(name, out, "filter_fir_en", 0)
                    self.log("info", f"FIR ({'TX' if out else 'RX'}) ausgeschaltet")
            except Exception:
                pass
        self._set("voltage0", True, "sampling_frequency", fs)
        got = int(self._get("voltage0", True, "sampling_frequency"))
        if abs(got - fs) > 1:
            raise PlutoError(f"Abtastrate {fs} abgelehnt (Treiber meldet {got})")
        bw = int(max(2.0e6, min(0.85 * fs, 40e6)))
        self._set("voltage0", True, "rf_bandwidth", bw)
        self._set("voltage0", False, "rf_bandwidth", bw)
        self.fs = got
        return got

    def poll(self):
        """Refresh temperature/LO/gain. Returns False on lost connection."""
        with self.lock:
            if self.phy is None:
                return False
            try:
                t = self.phy.find_channel("temp0", False)
                self.info["temp_c"] = round(int(self._retry(lambda: t.attrs["input"].value)) / 1000.0, 1)
                self.info["lo_hz"] = int(self._get("altvoltage1", True, "frequency"))
                self.info["rx_lo_hz"] = int(self._get("altvoltage0", True, "frequency"))
                self.info["atten_db"] = -self.read_gain()
                return True
            except Exception as e:
                self.drop(f"({e})")
                return False

    # ---------------------------------------------------------------- underflow register
    def unf_read_clear(self, wait=True):
        """DAC underflow flag (read + clear). wait=False: skip (None) if the control lock is busy."""
        if not self.lock.acquire(timeout=-1 if wait else 0):
            return None
        try:
            if self.dds is None:
                return 0
            v = self._retry(lambda: self.dds.reg_read(REG_DAC_UNF))
            if v & 1:
                self._retry(lambda: self.dds.reg_write(REG_DAC_UNF, 1))
            return v & 1
        finally:
            self.lock.release()

    # ---------------------------------------------------------------- RX capture
    def rx_capture(self, seconds, gain_db, lo_hz, nbuf=1 << 17):
        """Continuous RX capture at the current rate. Returns (complex64 array, overflows)."""
        if self.adc is None:
            raise PlutoError("Pluto nicht verbunden")
        iio = iio_module()
        with self.lock:
            if not self.fs:
                self.set_rate(4096000)
            fs = self.fs
            self.set_rx_lo(lo_hz)
            self._set("voltage0", False, "gain_control_mode", "manual")
            g = int(max(-3, min(71, round(gain_db))))
            self._set("voltage0", False, "hardwaregain", g)
            for n in ("voltage0", "voltage1"):
                self.adc.find_channel(n, False).enabled = True
            try:
                self.adc.set_kernel_buffers_count(8)
            except Exception:
                pass
        total = int(seconds * fs)
        nb = (total + nbuf - 1) // nbuf
        out = np.empty(nb * nbuf, dtype=np.complex64)
        self.rx_busy = True
        with self.lock:                     # buffer create/destroy talk on the control pipe
            buf = iio.Buffer(self.adc, nbuf, False)
        try:
            for _ in range(3):              # discard settling buffers
                buf.refill()
            for i in range(nb):
                buf.refill()
                raw = np.frombuffer(buf.read(), dtype="<i2").astype(np.float32)
                out[i * nbuf:(i + 1) * nbuf] = (raw[0::2] + 1j * raw[1::2]) / 2048.0
        finally:
            with self.lock:
                del buf
            self.rx_busy = False
        return out[:total], fs


# ======================================================================== TX streaming
class TxStream:
    """Reads int16 IQ from a subprocess stdout and pushes it into the Pluto TX buffer.

    reader thread: proc.stdout -> bounded queue (back-pressure keeps the chain paced by the Pluto)
    pusher thread: queue -> (optional NCO) -> iio.Buffer.push
    """

    def __init__(self, pluto, proc, fs, log, nco_hz=0.0, nsamp=1 << 16, kbufs=8, qchunks=16,
                 prefill=8, on_fail=None, half_bw=0.85e6):
        self.p = pluto
        self.proc = proc
        self.fs = fs
        self.log = log
        self.N = nsamp
        self.kbufs = kbufs
        self.q = queue.Queue(maxsize=qchunks)
        self.prefill = prefill
        self.on_fail = on_fail
        self.half_bw = float(half_bw)
        self.stop_ev = threading.Event()
        self.state = "starting"
        self.error = None
        self.since = time.time()
        self.samples_sent = 0
        self.underruns = 0
        self.src_stalls = 0
        self.src_late = 0
        self.bytes_in = 0
        self.kernel_level = 0.0
        self.hist = collections.deque(maxlen=64)
        self._nco_hz = float(nco_hz)
        self._nco_phase = np.complex128(1.0)
        self._nco_vec = None
        self._nco_vec_hz = None
        self.t_first = None
        self.th_r = threading.Thread(target=self._reader, name="tx-reader", daemon=True)
        self.th_p = threading.Thread(target=self._pusher, name="tx-pusher", daemon=True)

    def start(self):
        self.th_r.start()
        self.th_p.start()

    # ------------------------------------------------------------------ NCO
    def set_nco(self, hz):
        lim = 0.5 * self.fs - self.half_bw     # keep the signal block inside the 0.85*fs RF filter
        hz = 0.0 if lim <= 0 else max(-lim, min(lim, float(hz)))
        self._nco_hz = hz
        return hz

    def _apply_nco(self, raw):
        f = self._nco_hz
        if f == 0.0:
            return raw
        N = self.N
        if self._nco_vec_hz != f:
            n = np.arange(N, dtype=np.float64)
            self._nco_vec = np.exp(2j * np.pi * f * n / self.fs).astype(np.complex64)
            self._nco_step = np.exp(2j * np.pi * f * N / self.fs)
            self._nco_vec_hz = f
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32).view(np.complex64)
        y = x * (self._nco_vec * np.complex64(self._nco_phase))
        self._nco_phase *= self._nco_step
        self._nco_phase /= abs(self._nco_phase)
        y = y.view(np.float32)
        np.clip(np.rint(y), -32767, 32767, out=y)
        return y.astype("<i2").tobytes()

    # ------------------------------------------------------------------ threads
    def _reader(self):
        set_thread_priority(1)                  # ABOVE_NORMAL: keep the pipe drained
        nbytes = self.N * 4
        f = self.proc.stdout
        try:
            while not self.stop_ev.is_set():
                b = bytearray(nbytes)
                mv = memoryview(b)
                got = 0
                while got < nbytes:
                    k = f.readinto(mv[got:])
                    if not k:
                        raise EOFError("IQ-Quelle beendet (EOF)")
                    got += k
                self.bytes_in += nbytes
                while not self.stop_ev.is_set():
                    try:
                        self.q.put(b, timeout=0.5)
                        break
                    except queue.Full:
                        continue
        except Exception as e:
            if not self.stop_ev.is_set():
                self._fail(f"Quelle: {e}")

    def _fail(self, msg):
        if self.state == "error":
            return
        self.state = "error"
        self.error = msg
        self.log("error", f"TX-Strom: {msg}")
        try:
            self.p.mute()
        except Exception:
            pass
        self.stop_ev.set()
        if self.on_fail:
            try:
                self.on_fail(msg)
            except Exception:
                pass

    def _pusher(self):
        set_thread_priority(2)                  # HIGHEST: the DAC must never starve
        iio = iio_module()
        p = self.p
        buf = None
        try:
            # wait for prefill (ssh/encoder start-up)
            t0 = time.time()
            while self.q.qsize() < self.prefill and not self.stop_ev.is_set():
                if time.time() - t0 > 60:
                    raise PlutoError("keine IQ-Daten innerhalb 60 s")
                time.sleep(0.02)
            if self.stop_ev.is_set():
                return
            with p.lock:
                for n in ("voltage0", "voltage1"):
                    p.dds.find_channel(n, True).enabled = True
                try:
                    p.dds.set_kernel_buffers_count(self.kbufs)
                except Exception:
                    pass
                buf = iio.Buffer(p.dds, self.N, False)
            out = bytearray(self.N * 4)
            last_chk = 0.0
            nbufs = 0
            last_t = None
            per = self.N / self.fs
            while not self.stop_ev.is_set():
                if self.q.empty() and self.state == "running":
                    self.src_late += 1          # source behind real time -> underrun risk is upstream
                try:
                    chunk = self.q.get(timeout=1.0)
                except queue.Empty:
                    self.src_stalls += 1
                    if self.src_stalls % 5 == 1:
                        self.log("warn", "IQ-Quelle liefert nicht (Puffer leer)")
                    continue
                data = self._apply_nco(chunk)
                out[:] = data
                buf.write(out)
                tp = time.time()
                buf.push()
                now = time.time()
                # kernel queue model
                if last_t is not None:
                    self.kernel_level = max(0.0, self.kernel_level - (now - last_t) * self.fs)
                self.kernel_level = min(self.kernel_level + self.N, self.kbufs * self.N)
                if now - tp > 0.3 * per:
                    self.kernel_level = self.kbufs * self.N      # push blocked -> queue full
                    if self.t_first is None and self.state == "running":
                        self.t_first, self.s_first = now, self.samples_sent + self.N   # rate anchor: queue full
                last_t = now
                nbufs += 1
                self.samples_sent += self.N
                self.hist.append((now, self.samples_sent))
                if nbufs == self.kbufs + 2:
                    p.unf_read_clear()                          # flag left from idle DAC
                    self.state = "running"
                    self.since = now
                    self.log("info", "TX-Strom läuft")
                elif nbufs > self.kbufs + 2 and now - last_chk > 0.25:
                    last_chk = now
                    try:
                        unf = p.unf_read_clear(wait=False)
                    except Exception as e:
                        unf = None
                        self.log("warn", f"Underrun-Register nicht lesbar: {e}")
                    if unf:
                        self.underruns += 1
                        if self.underruns <= 10 or self.underruns % 100 == 0:
                            self.log("warn", f"DAC-Underrun #{self.underruns}")
        except Exception as e:
            if not self.stop_ev.is_set():
                self._fail(f"Pluto: {e}")        # lost USB is detected by the poll loop
        finally:
            try:
                p.mute()
            except Exception:
                pass
            if buf is not None:
                with p.lock:
                    try:
                        buf.cancel()
                    except Exception:
                        pass
                    del buf

    # ------------------------------------------------------------------ control / stats
    def stop(self, timeout=5):
        self.stop_ev.set()
        try:
            self.p.mute()
        except Exception:
            pass
        for th in (self.th_p, self.th_r):
            if th.is_alive() and th is not threading.current_thread():
                th.join(timeout)
        if self.state != "error":
            self.state = "idle"

    def stats(self):
        rate = None
        h = list(self.hist)
        if len(h) >= 2:
            # use up to the last ~5 s
            t_end, s_end = h[-1]
            for t, s in h:
                if t_end - t <= 5.0:
                    break
            if t_end > t:
                rate = (s_end - s) / (t_end - t) / 1e6
        avg = None
        if self.t_first and len(h) > 1 and h[-1][0] > self.t_first + 1:
            avg = (h[-1][1] - self.s_first) / (h[-1][0] - self.t_first) / 1e6
        q_ms = self.q.qsize() * self.N / self.fs * 1000
        k_ms = self.kernel_level / self.fs * 1000
        return {"state": self.state, "since": self.since, "underruns": self.underruns,
                "src_stalls": self.src_stalls, "src_late": self.src_late, "samples_sent": self.samples_sent,
                "rate_ms": round(rate, 5) if rate else None,
                "rate_avg_ms": round(avg, 5) if avg else None,
                "buffer_ms": round(q_ms + k_ms, 1), "queue_ms": round(q_ms, 1),
                "kernel_ms": round(k_ms, 1), "latency_ms": round(q_ms + k_ms, 1),
                "bytes_in": self.bytes_in, "nco_hz": self._nco_hz, "error": self.error}
