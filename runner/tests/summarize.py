#!/usr/bin/env python3
"""Kurzfassung eines Pruefberichts (check/decode JSON auf stdin oder Datei)."""
import json, sys
r = json.load(open(sys.argv[1]) if len(sys.argv) > 1 else sys.stdin)
e = r.get("ensemble", {})
print("ok=%s sync=%s snr=%s fic_crc=%s mismatches=%s errors=%s" % (r.get("ok"), r.get("sync"), r.get("snr_db"),
      r.get("fic_crc_errors"), r.get("mismatches"), r.get("errors")))
print("ensemble: eid=%s label=%r short=%r ecc=%s lto=%s dt=%s AL=%s" % (e.get("eid"), e.get("label"), e.get("shortlabel"),
      e.get("ecc"), e.get("lto"), e.get("datetime"), e.get("al_flag")))
for s in r.get("services", []):
    print(" svc %s %-16r %-9r pty=%s dyn=%s lang=%s %s %s kbit/s %s sr=%s ch=%s lvl=%s err=%s" % (
        s["sid"], s["label"], s["shortlabel"], s["pty"], s.get("pty_dynamic"), s.get("language"), s["codec"],
        s["bitrate"], s["protection"], s["samplerate"], s["channels"], s["audio_level_db"], s["errors"]))
    print("     dls=%r dlplus=%s slides=%s asu=%s ua=%s" % (s["dls"], [(t["type"], t["text"]) for t in s["dlplus"]],
          [(x["w"], x["h"], x["bytes"]) for x in s["slides"]], s.get("announcements"), [u["name"] for u in s.get("user_apps", [])]))
print("tii:", [(t["comb"], t["pattern"]) for t in r.get("tii", [])])
print("announcements (FIG0/19):", r.get("announcements"))
print("linking:", json.dumps(r.get("linking")))
print("packet components:", r.get("packet_components"))
print("figs:", r.get("fic", {}).get("figs_seen"))
if "render" in r:
    print("render:", json.dumps(r["render"].get("level_s16")), "gain", r["render"].get("digital_gain"),
          "elapsed", r["render"].get("elapsed_s"), "data", json.dumps(r["render"].get("data_services")))
print("capacity:", r.get("capacity", {}).get("used_cu"), "warnings:", r.get("warnings"))
tx = r.get("tx_fic")
if tx:
    print("TX-FIC (ETI): frames=%s fibs=%s crc=%s AL=%s figs=%s" % (tx["eti_frames"], tx["fibs"], tx["crc_errors"], tx["al_flag"], tx["figs_seen"]))
    print("  0/6:", json.dumps(tx["fig0_6"]), "\n  0/21:", json.dumps(tx["fig0_21"]), "\n  0/24:", json.dumps(tx["fig0_24"]))
    print("  0/18:", json.dumps(tx["asu"]), "\n  0/19:", json.dumps(tx["asw"]), "\n  0/3:", tx["packet_components"], "\n  0/13:", json.dumps(tx["user_apps"]))
