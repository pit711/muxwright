"""Wandelt die Ausgaben von pluto_bench.sh / pluto_load60.sh in tests/results/pluto_bench.json um."""
import json, re, sys
txt = open(sys.argv[1], encoding="utf-8", errors="replace").read()
out = {"source": "ADALM-Pluto, Zynq 7010, 2x Cortex-A9 667 MHz, Firmware v0.31, statische Binaries in /tmp", "micro": {}, "bench": {}, "load60": [], "notes": []}
cur = None
for line in txt.splitlines():
    m = re.match(r"## micro (\S+)", line)
    if m: cur = ("micro", m.group(1)); out["micro"][cur[1]] = {}; continue
    m = re.match(r"## selftest (\S+): (.*)", line)
    if m: out.setdefault("selftest", {})[m.group(1)] = m.group(2).strip(); continue
    m = re.match(r"## (\S+)$", line)
    if m and m.group(1) not in ("bench",): cur = ("bench", m.group(1)); continue
    m = re.match(r"micro_(\w+)=([\d.]+)", line)
    if m and cur and cur[0] == "micro": out["micro"][cur[1]][m.group(1)] = float(m.group(2)); continue
    if line.startswith("{") and cur and cur[0] == "bench":
        try: out["bench"][cur[1]] = json.loads(line)
        except Exception: pass
    m = re.match(r"## dauerlauf (.*)", line)
    if m: out["load60"].append({"cmd": m.group(1)}); continue
    if line.startswith("{") and out["load60"] and "result" not in out["load60"][-1]:
        try: out["load60"][-1]["result"] = json.loads(line)
        except Exception: pass
    m = re.match(r"(cpu\d) busy=([\d.]+)%", line)
    if m and out["load60"]: out["load60"][-1][m.group(1)] = float(m.group(2))
    if line.startswith("## loadavg"): out["notes"].append(line[3:])
json.dump(out, open(sys.argv[2], "w"), indent=1)
print(json.dumps(out, indent=1)[:3000])
