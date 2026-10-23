import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
if r.get("error"):
    print("ERROR:", r.get("error"), r.get("errors")); sys.exit()
b = r.get("bitrate", {})
print("ok=%s | Soll %.1f | Ist(PCR) %s | Abw %s %% | tsanalyze %s | Null %.1f %% | PCR-Jitter max %s ns | CC-Fehler %s | Dauer %ss"
      % (r["ok"], b["target_bps"], b["measured_bps"], b["deviation_pct"], b["tsanalyze_bps"], 100 * (b["null_share"] or 0),
         r.get("pcr_jitter_ns_max"), r.get("continuity_errors"), r.get("elapsed_s")))
t = r["tables"]
print("  Tabellen:", {k: v for k, v in r.get("table_counts", {}).items() if v}, "| NIT:", json.dumps(r.get("nit"), ensure_ascii=False))
for s in r["services"]:
    d = s.get("decode") or {}
    print("  %s %-14s %-5s LCN %-3s %s %s %s | EIT %r | Decode %s Bilder/%ss Fehler %s | PIDs %s" % (s["sid"], s["name"], s["type"], s["lcn"],
          s.get("video_codec"), s.get("resolution"), s.get("audio_codec"), s.get("eit_title"), d.get("video_frames"),
          d.get("decoded_s"), d.get("errors"), s.get("pids")))
print("  ffprobe.ok:", r["ffprobe"]["ok"], "| MISMATCHES:", r["mismatches"])
