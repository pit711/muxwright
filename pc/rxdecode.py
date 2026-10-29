"""Decoders for RX captures from the Pluto (Kontrollempfang im Gerätepfad).

Input: int16 interleaved I/Q as captured by iio_readdev on the device (12-bit values), RX LO = TX LO,
RX rate = TX rate (shared BBPLL; with TX FIR INT 2 the RX FIR (same taps, DEC 2) is the matched filter).

  DVB-T: gr-dtv receiver (pluto_fw/mod/tests/dvbt_rx_grdtv.py) in WSL -> TS -> tsanalyze
  DVB-C: pluto_fw/mod/tests/dvbc_demod.py --rx (1 sps after the RX-FIR matched filter, FFT oversampling) -> TS
  DVB-S: dvbs_soft.py --rx --up 8 -> dvbs_rx (C) -> TS
  PAL:   runner/pal/demod.analyze (in process) -> line rate, levels, picture correlation, sound
Every result: {"ok", "standard", "lock", "ts_packets", "services":[{sid,name,provider}], "mismatches", ...}.
"""
import json
import os
import re
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
from setup import win2wsl  # noqa: E402
MODTESTS = win2wsl(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pluto_fw", "mod", "tests"))
DVBS_RX = "/opt/dablab/mod/src/dvbs_rx"


def wslpath(p):
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", os.path.abspath(p))
    return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/") if m else p


def _wsl(script, distro="dablab", timeout=900):
    r = subprocess.run(["wsl", "-d", distro, "-e", "bash", "-c", script], capture_output=True, timeout=timeout,
                       creationflags=NOWIN)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def ts_analyze(ts_path, distro="dablab"):
    """tsanalyze --json on a TS file (in WSL) -> compact dict."""
    if not os.path.isfile(ts_path) or os.path.getsize(ts_path) < 188 * 10:
        return {"ts_packets": (os.path.getsize(ts_path) // 188) if os.path.isfile(ts_path) else 0, "services": []}
    rc, out, err = _wsl(f"tsanalyze --json {wslpath(ts_path)} 2>/dev/null", distro, 300)
    res = {"ts_packets": os.path.getsize(ts_path) // 188, "services": []}
    try:
        j = json.loads(out)
    except Exception:
        res["tsanalyze_error"] = (err or out)[-300:]
        return res
    ts = j.get("ts") or {}
    res["ts_id"] = ts.get("id")
    res["bitrate_bps"] = (ts.get("bitrate") or {}).get("ts") if isinstance(ts.get("bitrate"), dict) else ts.get("bitrate")
    pk = ts.get("packets") or {}
    res["packets_invalid_sync"] = pk.get("invalid-syncs")
    res["transport_errors"] = pk.get("transport-errors")
    pids = j.get("pids") or []
    res["discontinuities"] = sum(int((p.get("packets") or {}).get("discontinuities") or 0) for p in pids)
    for s in j.get("services") or []:
        res["services"].append({"sid": s.get("id"), "name": s.get("name"), "provider": s.get("provider"),
                                "type": s.get("type-name") or s.get("type")})
    nets = (ts.get("original-network-id"), ts.get("network-id"))
    res["onid"] = nets[0]
    # EIT present/following titles per service (tstables, PID 18)
    rc, out, err = _wsl(f"tstables --pid 18 --max-tables 12 {wslpath(ts_path)} 2>/dev/null | grep -E 'Service Id:|Event name:'",
                        distro, 120)
    eit, sid = {}, None
    for line in out.splitlines():
        m = re.search(r"Service Id: 0x([0-9A-Fa-f]+)", line)
        if m:
            sid = int(m.group(1), 16)
            continue
        m = re.search(r'Event name: "(.*)"', line)
        if m and sid is not None:
            eit.setdefault(sid, [])
            if m.group(1) not in eit[sid]:
                eit[sid].append(m.group(1))
    for s in res["services"]:
        s["eit"] = eit.get(s.get("sid"), [])
    res["eit"] = {str(k): v for k, v in eit.items()}
    return res


def _expected_services(cfg):
    out = []
    for s in cfg.get("services") or []:
        if s.get("name"):
            out.append(s["name"])
    for p in (cfg.get("dvb") or {}).get("programs") or []:
        if p.get("name") and p["name"] not in out:
            out.append(p["name"])
    return out


def _finish(res, cfg):
    exp = _expected_services(cfg)
    got = [s.get("name") for s in res.get("services") or []]
    res["expected_services"] = exp
    miss = [n for n in exp if n not in got]
    M = res.setdefault("mismatches", [])
    unknown = [s for s in res.get("services") or [] if s.get("name") in (None, "", "(unknown)")]
    W = res.setdefault("warnings", [])
    if not res.get("ts_packets"):
        M.append("kein Transportstrom dekodiert")
    elif miss and unknown and len(res.get("services") or []) >= len(exp) and len(unknown) >= len(miss):
        W.append(f"SDT nicht im Mitschnitt ({res.get('ts_packets')} Pakete): {len(res['services'])} Dienste per PAT/PMT, Namen unbekannt")
    elif miss:
        M.append("Dienste fehlen: " + ", ".join(miss))
    exp_eit = [((s.get("eit") or {}).get("title")) for s in cfg.get("services") or [] if (s.get("eit") or {}).get("title")]
    got_eit = [t for s in res.get("services") or [] for t in s.get("eit") or []]
    res["expected_eit"] = exp_eit
    if res.get("ts_packets") and exp_eit and not any(t in got_eit for t in exp_eit):
        (W if res.get("ts_packets", 0) < 20000 else M).append("EIT-Titel nicht im Mitschnitt: " + ", ".join(exp_eit))
    if res.get("transport_errors"):
        M.append(f"{res['transport_errors']} Transportfehler")
    res["lock"] = bool(res.get("ts_packets"))
    res["ok"] = res["lock"] and not M
    return res


QAM_LEVELS = {"qpsk": (1, 2), "16qam": (3, 10), "64qam": (7, 42)}     # höchste Stufe, mittlere Leistung des Gitters


def cells_metrics(path, const, ncells, npoints=2500):
    """MER und Konstellation aus den entzerrten Datenzellen von gr-dtv (dvbt_rx_grdtv.py --cells).
    gr-dtv normiert auf Es = 1; Gitter ±1, ±3 … ±top in Einheiten 1/sqrt(norm). Träger, die überwiegend Ausreißer
    liefern (DC-Übersprechen des Zero-IF-Empfängers im Pluto, Mitte des Kanals), zählen nicht zur MER – das ist ein
    Artefakt des Messempfängers, nicht des Senders; ihre Zahl steht im Ergebnis. Dazu kommen einzelne Zellen mit
    riesigem Fehler (gr-dtv teilt durch eine fast verschwindende Kanalschätzung, gemessen in ~der Hälfte der Symbole):
    Fehler ≥ 2 Gitterstufen gelten als Ausreißer, ihr Anteil steht in mer_outlier_pct. Die MER ist damit eine robuste
    Schätzung, bei MER < ~15 dB etwas zu optimistisch."""
    top, norm = QAM_LEVELS.get(const, QAM_LEVELS["64qam"])
    x = np.fromfile(path, np.complex64)
    if x.size < ncells * 4:
        return {}
    v = x[:x.size - x.size % ncells].reshape(-1, ncells) * np.sqrt(norm)
    q = lambda a: np.clip(2 * np.floor(a / 2) + 1, -top, top)
    ideal = q(v.real) + 1j * q(v.imag)
    err = np.abs(v - ideal)
    bad = (err > 2).mean(axis=0) > 0.5                   # Träger mit überwiegend Ausreißern
    cand = ~bad[None, :] & np.isfinite(err)
    use = cand & (err < 2)
    p_sig, p_err = np.mean(np.abs(ideal[use]) ** 2), np.mean(err[use] ** 2)
    pts = v[:, ~bad].ravel()
    pts = pts[np.random.default_rng(1).choice(pts.size, min(npoints, pts.size), replace=False)]
    pts = pts[np.abs(pts) < top + 2]
    return {"mer_db": round(float(10 * np.log10(p_sig / max(p_err, 1e-12))), 1), "mer_symbols": int(v.shape[0]),
            "mer_excluded_carriers": int(bad.sum()),
            "mer_outlier_pct": round(float(100 * (1 - use.sum() / max(cand.sum(), 1))), 2), "constellation": {"levels": top,
            "points": [[round(float(c.real), 2), round(float(c.imag), 2)] for c in pts]}}


def decode_dvbt(iq, cfg, distro="dablab"):
    d = cfg.get("dvbt") or {}
    const = str(d.get("constellation", "64-QAM")).upper().replace("-", "").lower()
    mode = str(d.get("mode") or d.get("fft") or "8k").lower()
    base = os.path.splitext(iq)[0]
    res = {"standard": "dvbt", "decoder": "gr-dtv"}
    raw = np.fromfile(iq, "<i2")
    res["clip_fraction"] = float(np.mean(np.abs(raw) >= 2047))
    tries = []
    for variant in ("norm", "conj"):
        x = np.clip(raw.astype(np.int32) * 8, -32768, 32767).astype(np.int16)
        if variant == "conj":
            x = x.copy()
            x[1::2] = -x[1::2]
        f = f"{base}.{variant}.iq"
        x.tofile(f)
        ts = f"{base}.{variant}.ts"
        cells = f"{base}.{variant}.cells"
        rc, out, err = _wsl(f"nice -n 5 timeout 600 python3 {MODTESTS}/dvbt_rx_grdtv.py --iq {wslpath(f)} --mode {mode} "
                            f"--const {const} --rate {d.get('code_rate', '2/3')} --guard {d.get('guard', '1/32')} -o {wslpath(ts)} "
                            f"--cells {wslpath(cells)}", distro, 700)
        os.remove(f)
        a = ts_analyze(ts, distro)
        try:
            a.update(cells_metrics(cells, const, 6048 if mode == "8k" else 1512))
        except Exception as e:
            a["mer_error"] = str(e)
        try:
            os.remove(cells)
        except OSError:
            pass
        tries.append((variant, a))
        if a.get("services"):
            break
    variant, a = max(tries, key=lambda t: (len(t[1].get("services") or []), t[1].get("ts_packets") or 0))
    res.update(a)
    res["variant"] = variant
    res["ts_file"] = f"{base}.{variant}.ts"
    return _finish(res, cfg)


def decode_dvbc(iq, cfg, distro="dablab", rx_fir=1):
    d = cfg.get("dvbc") or {}
    qam = int("".join(ch for ch in str(d.get("constellation", "256-QAM")) if ch.isdigit()) or 256)
    sps = int(d.get("sps", 1))
    base = os.path.splitext(iq)[0]
    ts, js = base + ".ts", base + ".dvbc.json"
    opt = ("--sps 1 --up 8" + (" --no-mf" if rx_fir else "")) if sps == 1 else "--sps 2"
    rc, out, err = _wsl(f"nice -n 5 timeout 600 python3 {MODTESTS}/dvbc_demod.py --iq {wslpath(iq)} --qam {qam} {opt} --rx "
                        f"--sr {int(d.get('symbol_rate', 6900000))} --max-symbols {os.path.getsize(iq) // 4} "
                        f"--dump-ts {wslpath(ts)} --json {wslpath(js)}", distro, 1200)
    res = {"standard": "dvbc", "decoder": "dvbc_demod.py", "matched_filter": "AD9361-RX-FIR" if rx_fir else "software"}
    try:
        dj = json.load(open(js, encoding="utf-8"))
        res["demod"] = {k: dj.get(k) for k in ("sync", "rs_ok", "rs_failed", "mer_db", "evm_pct", "rms_dbfs", "freq_offset_hz",
                                                 "symbols", "ok") if k in dj}
    except Exception:
        res["demod_error"] = (err or out)[-400:]
    res.update(ts_analyze(ts, distro))
    res["ts_file"] = ts
    return _finish(res, cfg)


def decode_dvbs2(iq, cfg, distro="dablab"):
    """DVB-S2: harter Kontrollempfänger (dvbs2_hardrx.py, systematische FEC, CRC-8 je Paket) – braucht hohes SNR (Loopback)."""
    d = cfg.get("dvbs") or {}
    base = os.path.splitext(iq)[0]
    ts = base + ".ts"
    ro = float(d.get("roll_off") or d.get("rolloff") or 0.35)
    cmd = (f"python3 {MODTESTS}/dvbs2_hardrx.py --iq {wslpath(iq)} --sps 1 --rx --up 8 --skip 200000 --max 3000000 "
           f"--mod {str(d.get('modulation', 'QPSK')).lower()} --rate {d.get('code_rate') or d.get('fec') or '3/4'} "
           f"--pilots {1 if d.get('pilots', True) else 0} --rolloff {ro} -o {wslpath(ts)}")
    rc, out, err = _wsl("nice -n 5 " + cmd, distro, 1500)
    res = {"standard": "dvbs2", "decoder": "dvbs2_hardrx.py", "log": (out + err)[-600:]}
    m = re.search(r"\{.*\}", out)
    if m:
        try:
            res["hardrx"] = json.loads(m.group(0))
        except Exception:
            pass
    res.update(ts_analyze(ts, distro))
    res["ts_file"] = ts
    hr = res.get("hardrx") or {}
    if hr.get("up_crc_errors"):
        res.setdefault("mismatches", []).append(f"{hr['up_crc_errors']} Paket-CRC-Fehler (harte Entscheidung, MER {hr.get('mer_db')} dB)")
    if hr and not hr.get("pls_ok", True):
        res.setdefault("mismatches", []).append(f"PLHEADER-MODCOD abweichend: {hr.get('pls_detected')}")
    return _finish(res, cfg)


def decode_dvbs(iq, cfg, distro="dablab"):
    d = cfg.get("dvbs") or {}
    if str(d.get("system", "dvbs")).lower() == "dvbs2":
        return decode_dvbs2(iq, cfg, distro)
    cr = d.get("code_rate") or d.get("fec") or "3/4"
    base = os.path.splitext(iq)[0]
    s8, ts = base + ".s8", base + ".ts"
    n = os.path.getsize(iq) // 4
    # dvbs_soft oversamples the whole segment x8 in one FFT -> decode in 1,5-M-symbol segments (memory), append the TS;
    # each segment re-synchronises on its own (a few packets lost at the joins), SDT/EIT sections are found per segment
    seg = 1500000
    starts = list(range(200000, max(200001, n - seg // 2), seg))
    parts = []
    for i, sk in enumerate(starts):
        mx = min(seg, n - sk - 1000)
        if mx < 200000:
            break
        parts.append(f"python3 {MODTESTS}/dvbs_soft.py --iq {wslpath(iq)} --sps 1 --rx --up 8 -o {wslpath(s8)}.{i} --max {mx} --skip {sk} "
                     f"&& {DVBS_RX} --rate {cr} -i {wslpath(s8)}.{i} -o {wslpath(ts)}.{i} --max {mx}; rm -f {wslpath(s8)}.{i}")
    script = "cd /tmp && rm -f " + wslpath(ts) + " && " + " ; ".join(parts) + f" ; cat {wslpath(ts)}.* > {wslpath(ts)} 2>/dev/null; rm -f {wslpath(ts)}.*"
    rc, out, err = _wsl("nice -n 5 bash -c " + json.dumps(script), distro, 1500)
    res = {"standard": "dvbs", "decoder": "dvbs_soft.py + dvbs_rx", "log": (out + err)[-600:]}
    m = re.search(r"\{.*\}", out)
    if m:
        try:
            res["soft"] = json.loads(m.group(0))
        except Exception:
            pass
    try:
        os.remove(s8)
    except OSError:
        pass
    res.update(ts_analyze(ts, distro))
    res["ts_file"] = ts
    return _finish(res, cfg)


def decode_pal(iq, cfg, rx_rate, png=None):
    """In-process PAL demodulation of the RX capture (runner/pal/demod.analyze)."""
    sys.path.insert(0, os.path.join(ROOT, "runner"))
    from pal import cli as P, demod   # noqa: E402
    c, E, W = P.normalize(dict(cfg, mode="pal"))
    p = c["pal"]
    raw = np.fromfile(iq, "<i2").astype(np.float64)
    x = (raw[0::2] + 1j * raw[1::2]) / 2048.0
    x = x - np.mean(x)
    n = min(len(x), int(rx_rate * 0.16 * 3))           # 3 loops of 160 ms
    seg = x[len(x) - n:]
    res = {"standard": "pal", "decoder": "runner/pal/demod"}
    try:
        import shutil
        ff = shutil.which("ffmpeg") or (os.path.join("C:" + os.sep, "ffmpeg", "bin", "ffmpeg.exe") if os.path.exists(os.path.join("C:" + os.sep, "ffmpeg", "bin", "ffmpeg.exe")) else "ffmpeg")
        d = demod.analyze(seg, rx_rate, -p["_offset_loop"], p["system"], png=png, ffmpeg=ff)
    except Exception as e:
        res.update(ok=False, lock=False, mismatches=[f"PAL-Demodulation: {type(e).__name__}: {e}"])
        return res
    d = {k: (float(v) if isinstance(v, (np.floating, np.integer)) else v) for k, v in d.items()
         if isinstance(v, (int, float, str, bool, np.floating, np.integer))}
    res["measured"] = d
    M = []
    lr = d.get("line_rate_hz", 0)
    if abs(lr - 15625) > 5:
        M.append(f"Zeilenfrequenz {lr:.1f} Hz")
    if "blank_rel" in d and abs(d["blank_rel"] - 0.73) > 0.08:
        M.append(f"Austastpegel {d['blank_rel']:.3f} statt 0,73")
    snd = p["sound"]
    if snd["kind"] == "tone" and d.get("audio_tone_hz") and abs(d["audio_tone_hz"] - snd["freq_hz"]) > 20:
        M.append(f"Tonfrequenz {d['audio_tone_hz']:.1f} statt {snd['freq_hz']} Hz")
    res["png"] = png if png and os.path.isfile(png) else None
    res["mismatches"] = M
    res["lock"] = abs(lr - 15625) <= 5
    res["ok"] = res["lock"] and not M
    return res


def decode(std, iq, cfg, rx_rate, distro="dablab", png=None, rx_fir=1):
    if std == "dvbt":
        return decode_dvbt(iq, cfg, distro)
    if std == "dvbc":
        return decode_dvbc(iq, cfg, distro, rx_fir)
    if std == "dvbs":
        return decode_dvbs(iq, cfg, distro)
    if std == "pal":
        return decode_pal(iq, cfg, rx_rate, png)
    return {"ok": False, "skipped": True, "error": f"kein Gerätedecoder für {std}"}
