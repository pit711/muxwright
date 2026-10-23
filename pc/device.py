"""SSH access to the Pluto (dablab firmware, root/analog) for things dablab-agent does not do:
deploying binaries/FIR files to /tmp and RX captures with iio_readdev on the device (local context,
full rate, no USB/RNDIS bottleneck during capture). TX rates/FIR are never touched here.
"""
import hashlib
import os
import posixpath
import re
import shlex
import subprocess
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = os.path.join(HERE, "device_files")
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class DeviceError(Exception):
    pass


class Device:
    def __init__(self, host="192.168.2.1", user="root", password="analog", log=None):
        self.host, self.user, self.password = host, user, password
        self.log = log or (lambda *a: None)
        self._c = None
        self.lock = threading.Lock()

    def _client(self):
        import paramiko
        if self._c is not None and self._c.get_transport() is not None and self._c.get_transport().is_active():
            return self._c
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            c.connect(self.host, username=self.user, password=self.password, timeout=6, banner_timeout=10,
                      auth_timeout=10, look_for_keys=False, allow_agent=False)
        except Exception as e:
            raise DeviceError(f"SSH {self.user}@{self.host}: {e}")
        self._c = c
        return c

    def close(self):
        if self._c is not None:
            try:
                self._c.close()
            except Exception:
                pass
            self._c = None

    def run(self, cmd, timeout=60, data=None):
        """-> (rc, stdout bytes, stderr str). data: bytes for stdin."""
        with self.lock:
            c = self._client()
            ch = c.get_transport().open_session(timeout=10)
            ch.settimeout(timeout)
            ch.exec_command(cmd)
            if data is not None:
                view = memoryview(data)
                for i in range(0, len(view), 1 << 16):
                    ch.sendall(view[i:i + (1 << 16)])
                ch.shutdown_write()
            out, err = bytearray(), bytearray()
            t0 = time.time()
            while True:
                if ch.recv_ready():
                    out += ch.recv(1 << 20)
                    continue
                if ch.recv_stderr_ready():
                    err += ch.recv_stderr(1 << 16)
                    continue
                if ch.exit_status_ready() and not ch.recv_ready() and not ch.recv_stderr_ready():
                    break
                if time.time() - t0 > timeout:
                    ch.close()
                    raise DeviceError(f"Zeitüberschreitung: {cmd[:80]}")
                time.sleep(0.005)
            rc = ch.recv_exit_status()
            ch.close()
            return rc, bytes(out), err.decode("utf-8", "replace")

    def sh(self, cmd, timeout=60):
        rc, out, err = self.run(cmd, timeout)
        if rc:
            raise DeviceError(f"{cmd[:80]}: rc {rc} {err.strip()[:200]}")
        return out.decode("utf-8", "replace")

    # ------------------------------------------------------------------ files
    def ensure(self, local, remote, mode="0644"):
        """Copy local file to the device if missing or different (md5). -> remote path."""
        with open(local, "rb") as f:
            blob = f.read()
        md5 = hashlib.md5(blob).hexdigest()
        rc, out, _ = self.run(f"md5sum {shlex.quote(remote)} 2>/dev/null")
        if rc == 0 and out.decode().split()[:1] == [md5]:
            return remote
        d = posixpath.dirname(remote)
        self.log("info", f"Gerät: kopiere {os.path.basename(local)} → {remote} ({len(blob)} B)")
        rc, _, err = self.run(f"mkdir -p {shlex.quote(d)} && cat > {shlex.quote(remote)}.part && "
                              f"chmod {mode} {shlex.quote(remote)}.part && mv {shlex.quote(remote)}.part {shlex.quote(remote)}",
                              timeout=120, data=blob)
        if rc:
            raise DeviceError(f"Kopie nach {remote}: {err[:200]}")
        rc, out, _ = self.run(f"md5sum {shlex.quote(remote)}")
        if out.decode().split()[:1] != [md5]:
            raise DeviceError(f"Prüfsumme {remote} falsch")
        return remote

    def ensure_fir(self, kind, symbol_rate, alpha, taps, wsl_distro="dablab"):
        """RRC FIR for dvbc_mod/dvbs_mod --sps 1 (TX INT 2, RX DEC 2) -> path on the device."""
        name = f"rrc_{kind}_{int(symbol_rate)}_{alpha}_{taps}.fir"
        local = os.path.join(FILES, name)
        if not os.path.isfile(local):
            known = {("dvbc", 6900000, 0.15, 128): "rrc69.fir", ("dvbs", 22000000, 0.35, 64): "rrc22.fir"}
            k = known.get((kind, int(symbol_rate), alpha, taps))
            if k and os.path.isfile(os.path.join(FILES, k)):
                local = os.path.join(FILES, k)
            else:
                win = local
                m = re.match(r"^([A-Za-z]):[\\/](.*)$", win)
                wpath = f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")
                r = subprocess.run(["wsl", "-d", wsl_distro, "-e", "/opt/dablab/mod/src/rrc_fir_gen", "--alpha", str(alpha),
                                    "--sr", str(int(symbol_rate)), "--taps", str(taps), "-o", wpath],
                                   capture_output=True, timeout=60, creationflags=NOWIN)
                if r.returncode or not os.path.isfile(local):
                    raise DeviceError(f"rrc_fir_gen: {r.stderr[-300:]!r}")
        return self.ensure(local, f"/tmp/dablab_fir/{os.path.basename(local)}")

    # ------------------------------------------------------------------ processes outside the agent
    def stray_modulators(self):
        """Modulator processes not started by the agent (e.g. left over from manual tests)."""
        out = self.sh("ps | grep -v grep | grep -E 'dvb([tcs]|s2)_mod|iio_writedev|iqplay' || true")
        return [l.strip() for l in out.splitlines() if l.strip()]

    def kill_stray(self):
        """Mute, then SIGTERM/SIGKILL stray modulators. Agent-started ones are stopped via the agent."""
        lines = self.stray_modulators()
        if not lines:
            return []
        self.sh(_PHY + "echo -89.75 > $PHY/out_voltage0_hardwaregain")
        pids = [l.split()[0] for l in lines]
        self.sh("kill -TERM " + " ".join(pids) + " 2>/dev/null; sleep 2; kill -KILL " + " ".join(pids) + " 2>/dev/null; true")
        self.log("warn", f"Gerät: fremde Modulatorprozesse beendet: {lines}")
        return lines

    # ------------------------------------------------------------------ RX capture
    def rx_capture(self, lo_hz, samples, gain_db=20, rx_bw_hz=None, settle_s=0.5):
        """Capture `samples` complex samples (int16 I/Q) with iio_readdev on the device at the current RX rate.
        Only RX LO, gain mode/gain (and optionally the analog RX bandwidth) are changed and restored afterwards;
        sampling rate and FIR (shared BBPLL with TX) stay untouched. -> (bytes, info dict)."""
        samples = int(samples)
        bw = f"echo {int(rx_bw_hz)} > $PHY/in_voltage_rf_bandwidth;" if rx_bw_hz else ""
        g = int(max(-3, min(71, round(gain_db))))
        script = _PHY + f"""
RXLO0=$(cat $PHY/out_altvoltage0_RX_LO_frequency); GM0=$(cat $PHY/in_voltage0_gain_control_mode); RXBW0=$(cat $PHY/in_voltage_rf_bandwidth)
TXR0=$(cat $PHY/tx_path_rates)
echo {int(lo_hz)} > $PHY/out_altvoltage0_RX_LO_frequency
{bw}
echo manual > $PHY/in_voltage0_gain_control_mode; echo {g} > $PHY/in_voltage0_hardwaregain
sleep {settle_s}
echo "INFO lo=$(cat $PHY/out_altvoltage0_RX_LO_frequency) rate=$(cat $PHY/in_voltage_sampling_frequency) bw=$(cat $PHY/in_voltage_rf_bandwidth) gain=$(cat $PHY/in_voltage0_hardwaregain) rx_fir=$(cat $PHY/in_voltage_filter_fir_en) txrate=$(cat $PHY/out_voltage_sampling_frequency)" > /tmp/dablab_rx.info
iio_readdev -u local: -b 262144 -s {samples} cf-ad9361-lpc voltage0 voltage1 > /tmp/dablab_rx.iq 2>/tmp/dablab_rx.err
RC=$?
[ "$(cat $PHY/tx_path_rates)" = "$TXR0" ] && echo "TXSAME 1" >> /tmp/dablab_rx.info || echo "TXSAME 0" >> /tmp/dablab_rx.info
{"echo $RXBW0 > $PHY/in_voltage_rf_bandwidth;" if rx_bw_hz else ""}
echo $RXLO0 > $PHY/out_altvoltage0_RX_LO_frequency; echo $GM0 > $PHY/in_voltage0_gain_control_mode
echo "RC $RC" >> /tmp/dablab_rx.info
"""
        t0 = time.time()
        self.sh(script, timeout=60 + samples / 1e6)
        info_txt = self.sh("cat /tmp/dablab_rx.info; wc -c < /tmp/dablab_rx.iq")
        t_cap = time.time() - t0
        rc, blob, err = self.run("cat /tmp/dablab_rx.iq; rm -f /tmp/dablab_rx.iq", timeout=60 + samples * 4 / 2e6)
        info = {"capture_s": round(t_cap, 2), "transfer_s": round(time.time() - t0 - t_cap, 2), "bytes": len(blob)}
        m = re.search(r"INFO lo=(\d+) rate=(\d+) bw=(\d+) gain=([-\d.]+)[^ ]* (?:dB )?rx_fir=(\d) txrate=(\d+)", info_txt)
        if m:
            info.update(rx_lo_hz=int(m.group(1)), rx_rate_hz=int(m.group(2)), rx_bw_hz=int(m.group(3)),
                        rx_gain_db=float(m.group(4)), rx_fir=int(m.group(5)), tx_rate_hz=int(m.group(6)))
        info["tx_rates_unchanged"] = "TXSAME 1" in info_txt
        if len(blob) < samples * 4 * 0.9:
            raise DeviceError(f"RX-Mitschnitt zu kurz ({len(blob)} B): {info_txt.strip()[-200:]}")
        return blob, info


_PHY = "PHY=$(dirname $(grep -l '^ad9361-phy$' /sys/bus/iio/devices/iio:device*/name)); "
