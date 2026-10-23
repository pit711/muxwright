#!/usr/bin/env python3
"""Kurzfassung eines UKW-Pruefberichts (check/decode) oder eines fm_live_test-Ergebnisses."""
import json, sys

r = json.load(open(sys.argv[1], encoding="utf-8"))
if "decode_A" in r or "cpu_pct" in r:
    for k in ("startup_s", "rate_min_sps", "rate_avg_sps", "max_backlog_ms", "exit_rc", "children_before",
              "children_alive_after", "status_lines", "cpu_pct", "ctl_result", "ctl_verified", "eof_early", "log_tail"):
        print(k, json.dumps(r.get(k), ensure_ascii=False)[:700])
    for n in ("A", "B"):
        print(n, json.dumps(r.get("decode_" + n), ensure_ascii=False)[:1800])
    sys.exit(0)
m = r.get("measured", {})
rd = r.get("rds") or {}
print("ok", r.get("ok"), "err", r.get("error"), "elapsed", r.get("elapsed_s"), "rt-factor",
      (r.get("render") or {}).get("realtime_factor"), "sec", (r.get("iq") or {}).get("seconds_analyzed"))
print("dev", m.get("deviation"), "mpxpow", m.get("mpx_power_dbr"))
print("pilot", m.get("pilot"), "rds", m.get("rds"), "38k", m.get("subcarrier_38k_residual_khz"))
au = m.get("audio", {})
print("audio", json.dumps({k: au.get(k) for k in ("left", "right", "separation_db", "block_grid_lines", "mono_peak_khz", "diff_peak_khz")}))
print("RDS", json.dumps({k: rd.get(k) for k in ("groups", "group_types", "pi", "ps", "radiotext", "prog_type", "tp", "ta",
                                                 "is_music", "di", "af_a", "af_b", "country", "language", "clock_time",
                                                 "pty_name", "long_ps", "rtplus_tags", "rtplus_last", "eon", "bler_mean_pct",
                                                 "prog_item_number", "prog_item_started")}, ensure_ascii=False))
print("FIELDS", json.dumps([(f["name"], f["soll"], f["ist"], f["ok"]) for f in r.get("fields", [])], ensure_ascii=False)[:3000])
print("MISMATCH", r.get("mismatches"))
sc = r.get("second_carrier")
if sc:
    print("SC", json.dumps({"err": sc.get("error"), "dev": (sc.get("measured") or {}).get("deviation"),
                            "pilot": (sc.get("measured") or {}).get("pilot"), "rds": (sc.get("measured") or {}).get("rds"),
                            "ps": (sc.get("rds") or {}).get("ps"), "pi": (sc.get("rds") or {}).get("pi"),
                            "groups": (sc.get("rds") or {}).get("groups"), "bler": (sc.get("rds") or {}).get("bler_mean_pct"),
                            "mm": sc.get("mismatches")}, ensure_ascii=False))
