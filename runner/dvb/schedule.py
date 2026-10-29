# -*- coding: utf-8 -*-
"""Zeitplan (Sendeablauf) je Programm: Quellenart "schedule".

Eintraege {"time": "HH:MM[:SS]", "days": [1..7] (ISO, 1 = Montag; leer = taeglich), "kind": file|playlist|testpattern|folder,
"file"/"playlist"/"pattern"/"folder", "title", "text", "loop"}. Ein Eintrag laeuft ab seiner Startzeit bis zum Start des
naechsten (Datei/Playlist in Schleife; loop = false: danach Fueller-Testbild).

ScheduleFeeder nutzt den Mechanismus des VideoFeeder (Rohdaten-FIFOs yuv420p/s16le, die der Mux-ffmpeg mit festem Takt liest):
der Mux sieht einen einzigen endlosen Strom, PTS/DTS/PCR laufen ueber die Umschaltungen hinweg stetig weiter. Umgeschaltet
wird bildgenau nach der Stromuhr (Startzeit + gelieferte Bilder/fps), damit stimmt der Zeitpunkt auch bei render (schneller als
Echtzeit) und haengt nicht an der Vorlaufzeit der Warteschlangen.

EIT: present/following aus dem Zeitplan (Start/Dauer wie geplant, event_id aus der Startminute) und EIT schedule actual
(table_id 0x50, Segmente zu 3 h, die naechsten 48 h) als eigene Binaerdatei, die tsp mit eigener Wiederholrate einspielt.

Testhilfe (nur Zeitplan-Uhr, nicht TDT/TOT): DABLAB_SCHEDULE_START="HH:MM[:SS]" (heute, Ortszeit) setzt den Start der
Stromuhr genau, DABLAB_CLOCK_OFFSET_S verschiebt sie um Sekunden."""
import datetime
import json
import os
import re
import subprocess
import threading
import time

from . import si
from . import sources as SRC

KINDS = ("file", "playlist", "testpattern", "folder")
WINDOW_S = 48 * 3600          # EIT schedule: so weit voraus
FOLDER_CHUNK_S = 10.0          # Bildordner: so oft wird das neueste Bild neu gewaehlt
_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$")


def start_clock(now=None):
    """Startwert der Zeitplan-Uhr: jetzt, verschoben um DABLAB_CLOCK_OFFSET_S, oder genau DABLAB_SCHEDULE_START (heute)."""
    now = time.time() if now is None else now
    t = parse_time(os.environ.get("DABLAB_SCHEDULE_START"))
    if t is not None:
        lt = time.localtime(now)
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, t // 3600, (t // 60) % 60, t % 60, 0, 0, -1))
    try:
        return now + float(os.environ.get("DABLAB_CLOCK_OFFSET_S") or 0)
    except ValueError:
        return now


def parse_time(s):
    """'HH:MM' oder 'HH:MM:SS' -> Sekunden seit Mitternacht, sonst None."""
    m = _TIME_RE.match(str(s or ""))
    if not m:
        return None
    h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if h > 23 or mi > 59 or se > 59:
        return None
    return h * 3600 + mi * 60 + se


def parse_days(v):
    """Wochentage -> sortierte Liste 1..7 (ISO); leer/None = taeglich. Erlaubt Liste oder '1,2,3'. None bei Fehler."""
    if v in (None, "", []):
        return []
    if isinstance(v, str):
        v = [x for x in re.split(r"[,\s]+", v) if x]
    try:
        d = sorted({int(x) for x in v})
    except (TypeError, ValueError):
        return None
    return d if all(1 <= x <= 7 for x in d) else None


def occurrences(entries, now, back_days=8, fwd_days=3):
    """-> sortierte Liste [(start_epoch, index)] aller Starts von now - back_days bis now + fwd_days (Ortszeit, Sommerzeit
    ueber mktime). Bei gleicher Startzeit gewinnt der spaetere Eintrag (der fruehere hat Dauer 0 und wird ausgelassen)."""
    lt = time.localtime(now)
    base = datetime.date(lt.tm_year, lt.tm_mon, lt.tm_mday)
    out = {}
    for d in range(-back_days, fwd_days + 1):
        day = base + datetime.timedelta(days=d)
        for i, e in enumerate(entries):
            if e.get("_days") and day.isoweekday() not in e["_days"]:
                continue
            t = e["_t"]
            st = time.mktime((day.year, day.month, day.day, t // 3600, (t // 60) % 60, t % 60, 0, 0, -1))
            out[int(st)] = i
    return sorted(out.items())


def current(entries, now):
    """-> (laufend (start, idx) oder None, [(start, idx), ...] danach)."""
    occ = occurrences(entries, now)
    cur, rest = None, []
    for st, i in occ:
        if st <= now:
            cur = (st, i)
        else:
            rest.append((st, i))
    return cur, rest


def normalize_entries(src, svc_index, is_tv, E):
    """Pruefung/Normalisierung der Eintraege (config.py). Fehler nach E."""
    ents = src.get("entries")
    if not isinstance(ents, list) or not ents:
        E.append("services[%d].source.entries: Zeitplan ohne Eintraege" % svc_index)
        src["entries"] = []
        return
    seen = {}
    for j, e in enumerate(ents):
        p = "services[%d].source.entries[%d]" % (svc_index, j)
        if not isinstance(e, dict):
            E.append("%s: Objekt erwartet" % p)
            continue
        e["_t"] = parse_time(e.get("time"))
        if e["_t"] is None:
            E.append("%s.time: HH:MM (00:00-23:59)" % p)
            e["_t"] = 0
        e["_days"] = parse_days(e.get("days"))
        if e["_days"] is None:
            E.append("%s.days: Wochentage 1 (Mo) bis 7 (So)" % p)
            e["_days"] = []
        e["kind"] = str(e.get("kind") or "file").lower()
        if e["kind"] not in KINDS:
            E.append("%s.kind: %s" % (p, ", ".join(KINDS)))
        elif e["kind"] == "file" and not e.get("file"):
            E.append("%s.file fehlt" % p)
        elif e["kind"] == "playlist" and not e.get("playlist"):
            E.append("%s.playlist fehlt" % p)
        elif e["kind"] == "folder":
            if not e.get("folder"):
                E.append("%s.folder fehlt" % p)
            if not is_tv:
                E.append("%s: Bildordner nur fuer TV-Programme" % p)
        elif e["kind"] == "testpattern":
            e.setdefault("pattern", "smptebars")
        e["loop"] = bool(e.get("loop", True))
        e["title"] = str(e.get("title") or "")[:96]
        e["text"] = str(e.get("text") or "")
        for d in (e["_days"] or range(1, 8)):
            k = (e["_t"], d)
            if k in seen:
                E.append("%s: Startzeit %s doppelt (wie Eintrag %d)" % (p, e.get("time"), seen[k] + 1))
                break
            seen[k] = j


# --------------------------------------------------------------------------------------------- Feeder
class ScheduleFeeder(SRC.VideoFeeder):
    def __init__(self, host, cfg, svc, work, sdir, log, audio=True, render=False):
        self._init_io(host, cfg, svc, work, sdir, log, audio)
        self.render = render
        self.entries = self.src["entries"]
        self.filler = self.src.get("filler") or "smptebars"
        self.pl = {"name": "schedule", "items": []}
        self.n = len(self.entries)
        self.t0 = start_clock()                      # Stromuhr: t0 + gelieferte Bilder/fps
        self.offset = self.t0 - time.time()          # Testhilfe: Abstand zur Wanduhr
        self.lead = float(cfg.get("_eit_lead_s") or 0)  # live: Vorlauf der EIT-Einspielung vor der Ausgabe (tsp-Puffer)
        self.occ = None                              # laufender Eintrag (start, idx) oder None (Fueller)
        self.nxt = []                                # folgende Starts
        self.item = {}                               # gerade gespielter Inhalt (Titel, Datei)
        self._probe = {}
        self.sched_file = os.path.join(sdir, "eit", "eit_sched_%s.bin" % self.key)
        self.sched_t = 0
        # Startzustand sofort festlegen und EIT schreiben (tsp laedt die Dateien beim Start)
        self.occ, self.nxt = current(self.entries, self.t0)
        self.eit_occ = self.occ
        self.write_eit(self.t0)

    # -- Uhr --
    def clock(self):
        return self.t0 + (self.frames / self.fps if self.is_tv else self.samples / float(self.sr))

    def now(self):
        """Uhr fuer die EIT: live die Wanduhr plus Vorlauf der tsp-Einspielung (die Stromuhr laeuft um alle Puffer voraus,
        bei kleinen Raten viele Sekunden), render die Stromuhr."""
        return self.clock() if self.render else min(self.clock(), time.time() + self.offset + self.lead)

    def _eit_tick(self):
        """EIT umschalten, wenn auf der EIT-Uhr ein neuer Eintrag beginnt; schedule alle 15 min neu (Fenster, UTC-Tag)."""
        now = self.now()
        occ = current(self.entries, now + 0.001)[0]
        if occ != self.eit_occ or now - self.sched_t > 900:
            if occ != self.eit_occ:
                with self.eit_lock:
                    self.eit_override = {}          # ctl-EIT gilt bis zum naechsten Eintrag
            self.eit_occ = occ
            self.write_eit(now + 0.001)

    def _resync(self):
        """live: steht die Kette (Leser haengt), laeuft die Stromuhr hinter der Wanduhr her -> nachziehen."""
        if self.render:
            return
        lag = time.time() + self.offset - self.clock()
        if lag > 10:
            self.t0 += lag
            self.log("warn", "Zeitplan %s: Stromuhr %.1f s hinter der Uhr, nachgezogen" % (self.key, lag))

    # -- Anzeige --
    def _title(self, idx):
        if idx is None:
            return self.svc["eit"]["title"]
        e = self.entries[idx]
        if e.get("title"):
            return e["title"]
        if e["kind"] == "file":
            pr = self._pr(self.host.media_path(e["file"]))
            return pr.get("title") or os.path.splitext(os.path.basename(e["file"]))[0]
        if e["kind"] == "playlist":
            return e["playlist"]
        if e["kind"] == "folder":
            return os.path.basename(str(e["folder"]).rstrip("/\\")) or self.svc["eit"]["title"]
        return self.svc["eit"]["title"]

    def _text(self, idx):
        if idx is not None and self.entries[idx].get("text"):
            return self.entries[idx]["text"]
        return self.svc["eit"].get("text") or ""

    def now_playing(self):
        st, idx = self.occ if self.occ else (None, None)
        nx = self.nxt[0] if self.nxt else None
        return {"service": self.key, "schedule": True, "entry": idx, "title": self._title(idx), "start": st,
                "end": nx[0] if nx else None, "item": self.item.get("title"), "file": self.item.get("file"),
                "elapsed_s": round(self.clock() - st, 1) if st else None,
                "next": {"entry": nx[1], "title": self._title(nx[1]), "start": nx[0]} if nx else None}

    # -- EIT --
    def _events(self, now):
        """Ereignisse ab dem laufenden bis now + WINDOW_S: [{"start","duration_s","title","text","event_id"}]."""
        occ = occurrences(self.entries, now)
        out = []
        for k, (st, i) in enumerate(occ):
            if k + 1 >= len(occ):
                break
            end = occ[k + 1][0]
            if end <= now and st < now:
                continue
            if st > now + WINDOW_S:
                break
            out.append({"start": st, "duration_s": end - st, "title": self._title(i), "text": self._text(i),
                        "event_id": si.eit_event_id(st)})
        return out

    def write_eit(self, now=None):
        if not self.cfg["ts"].get("eit", True):
            return
        now = self.now() if now is None else now
        evs = self._events(now)
        cur = evs[0] if evs and evs[0]["start"] <= now else None
        if cur is None:          # vor dem ersten Eintrag (Zeitplan beginnt erst spaeter): Fueller bis zum ersten Start
            nx = evs[0]["start"] if evs else now + 3600
            cur = {"title": self.svc["eit"]["title"], "text": self.svc["eit"].get("text") or "", "start": int(now),
                   "duration_s": max(60, nx - int(now)), "event_id": 0xFFFF}
            fol = evs[0] if evs else None
        else:
            fol = evs[1] if len(evs) > 1 else None
        with self.eit_lock:
            o = self.eit_override
            pres = dict(cur, title=o.get("title") or cur["title"], text=o.get("text") or cur["text"])
        try:
            os.makedirs(os.path.dirname(self.eitfile), exist_ok=True)
            si.write_eit(self.host, self.eitfile, self.cfg, self.svc, pres, fol, now=now)
            si.write_eit_schedule(self.host, self.sched_file, self.cfg, self.svc, evs, now)
            self.sched_t = now
        except Exception as e:
            self.log("warn", "EIT-Datei (Zeitplan): %s" % e)

    # -- Inhalte --
    def _pr(self, path):
        if path not in self._probe:
            self._probe[path] = SRC.probe(self.host, path)
        return self._probe[path]

    def _audio_tone(self):
        a = self.src.get("audio") or {}
        if a.get("kind") == "sine":
            return ["-f", "lavfi", "-i", "sine=frequency=%s:sample_rate=%d" % (a.get("freq_hz", 1000), self.sr)], \
                "volume=%sdB,aformat=channel_layouts=stereo" % float(a.get("level_dbfs", -18))
        return ["-f", "lavfi", "-i", "anullsrc=r=%d:cl=stereo" % self.sr], "anull"

    def _pattern_chunk(self, pattern, title):
        inp, v, af = [], None, "anull"
        if self.is_tv:
            inp += ["-f", "lavfi", "-i", "%s=size=%dx%d:rate=%s" % (pattern, self.w, self.h, self.svc["video"]["fps"])]
            v = 0
        a_in, af = self._audio_tone()
        a = 1 if self.is_tv else 0
        inp += a_in
        return {"inputs": inp, "v": v, "a": a, "af": af, "title": title, "file": None,
                "vf": SRC.video_filter(self.svc, SRC.drawtext_filter(self.svc, title, True)) if self.is_tv else None}

    def _media_chunk(self, path, title, off=0.0):
        pr = self._pr(path)
        inp = (["-ss", "%.3f" % off] if off > 0.05 else []) + ["-i", path]
        vdur = pr.get("video_duration_s") or pr.get("duration_s")
        if vdur and off > 0.05:
            vdur = max(0.04, vdur - off)
        a = 0
        if not pr.get("has_audio", True):
            inp += ["-f", "lavfi", "-i", "anullsrc=r=%d:cl=stereo" % self.sr]
            a = 1
        if self.is_tv and not pr.get("has_video", True):
            self.log("warn", "Zeitplan %s: %s enthaelt kein Video, Fueller" % (self.key, os.path.basename(path)))
            return self._pattern_chunk(self.filler, title)
        return {"inputs": inp, "v": 0 if self.is_tv else None, "a": a, "af": "aresample=%d:async=1" % self.sr,
                "vdur": vdur if self.is_tv else None, "title": pr.get("title") or title, "file": path,
                "vf": SRC.video_filter(self.svc) if self.is_tv else None}

    def _chunks(self, idx, start, join):
        """Generator: Teilstuecke des Eintrags idx (ab start); join = mitten in einen laufenden Eintrag einsteigen."""
        if idx is None:
            while True:
                yield self._pattern_chunk(self.filler, self.svc["name"])
        e = self.entries[idx]
        title = self._title(idx)
        if e["kind"] == "testpattern":
            while True:
                yield self._pattern_chunk(e.get("pattern") or "smptebars", title)
        if e["kind"] == "folder":
            folder = SRC.host_path(self.host, e["folder"])
            while True:
                img = self._newest(folder)
                if not img:
                    c = self._pattern_chunk(self.filler, title)
                    c["t"] = FOLDER_CHUNK_S
                    yield c
                    continue
                inp = ["-loop", "1", "-framerate", str(self.svc["video"]["fps"]), "-t", "%.1f" % FOLDER_CHUNK_S, "-i", img,
                       "-f", "lavfi", "-t", "%.1f" % FOLDER_CHUNK_S, "-i", "anullsrc=r=%d:cl=stereo" % self.sr]
                yield {"inputs": inp, "v": 0, "a": 1, "af": "anull", "title": title, "file": img,
                       "vf": SRC.video_filter(self.svc)}
        if e["kind"] == "file":
            items = [{"file": e["file"], "title": title}]
        else:
            items = self.host.load_playlist(e["playlist"], probe=False)["items"]
            if not items:
                self.log("warn", "Zeitplan %s: Playlist %s leer, Fueller" % (self.key, e["playlist"]))
                while True:
                    yield self._pattern_chunk(self.filler, title)
        paths = [self.host.media_path(it["file"]) for it in items]
        missing = [p for p in paths if not os.path.isfile(p)]
        if missing:
            self.log("error", "Zeitplan %s: Datei fehlt: %s – Fueller" % (self.key, ", ".join(missing)))
            while True:
                yield self._pattern_chunk(self.filler, title)
        durs = [self._pr(p).get("video_duration_s" if self.is_tv else "duration_s") or self._pr(p).get("duration_s") or 0
                for p in paths]
        k, off = 0, 0.0
        elapsed = self.clock() - start
        if join and elapsed > 2 and sum(durs) > 0:
            # Einstieg mitten im Eintrag: an die Stelle springen, die jetzt laufen wuerde
            if e["loop"]:
                elapsed %= sum(durs)
            while k < len(durs) and elapsed >= durs[k]:
                elapsed -= durs[k]
                k += 1
            off = elapsed
        while True:
            if k >= len(items):
                if not e["loop"]:
                    while True:
                        yield self._pattern_chunk(self.filler, title)
                k = 0
            it = items[k]
            yield self._media_chunk(paths[k], it.get("title") or title, off)
            off = 0.0
            k += 1

    @staticmethod
    def _newest(folder):
        best = None
        try:
            with os.scandir(folder) as it:
                for d in it:
                    if d.name.startswith(".") or not d.name.lower().endswith(SRC.IMAGE_EXT):
                        continue
                    st = d.stat()
                    if st.st_size and (best is None or st.st_mtime > best[1]):
                        best = (d.path, st.st_mtime)
        except OSError:
            return None
        return best[0] if best else None

    def _spawn(self, c):
        ff = self.host.tool("ffmpeg")
        cmd = [ff, "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "2"] + c["inputs"]
        ra, wa = os.pipe()
        if self.is_tv:
            cmd += ["-map", "%d:v:0" % c["v"], "-vf", c["vf"], "-an"] + (["-t", "%.3f" % c["t"]] if c.get("t") else []) \
                + ["-f", "rawvideo", "pipe:1"]
        if self.use_audio:
            af = c["af"]
            tlim = []
            if c.get("vdur"):
                af += ",apad"                   # Ton exakt auf Bildlaenge (wie VideoFeeder)
                tlim = ["-t", "%.6f" % c["vdur"]]
            elif c.get("t"):
                tlim = ["-t", "%.3f" % c["t"]]
            cmd += ["-map", "%d:a:0" % c["a"], "-vn", "-af", af, "-ac", "2", "-ar", str(self.sr)] + tlim + \
                ["-f", "s16le", "pipe:%d" % wa]
        lf = open(os.path.join(self.sdir, "schedule_%s.log" % self.key), "ab")
        lf.write(("\n=== %s %s\n" % (time.strftime("%H:%M:%S"), " ".join(cmd))).encode())
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE if self.is_tv else subprocess.DEVNULL, stderr=lf,
                                     stdin=subprocess.DEVNULL, pass_fds=[wa])
        os.close(wa)
        lf.close()
        self.proc_a = os.fdopen(ra, "rb", 0)
        self.frames_track = self.samples_track = 0
        self.hit_v = self.hit_a = False
        self.item = {"title": c.get("title"), "file": c.get("file")}

    # -- Ablauf --
    def _enter(self, occ, nxt, join=False):
        self.occ, self.nxt = occ, nxt
        st, idx = occ if occ else (self.clock(), None)
        self.gen = self._chunks(idx, st, join)
        self._eit_tick()
        np_ = self.now_playing()
        try:
            self.host.atomic_write(self.npfile, json.dumps(np_, ensure_ascii=False))
        except Exception:
            pass
        self.log("info", "now_playing", now_playing=np_)

    def _set_limit(self):
        """Grenze bis zum naechsten Start in Bildern bzw. Samples (absolut, wie self.frames/self.samples)."""
        if not self.nxt:
            self.lim_v = self.lim_a = None
            return
        dt = self.nxt[0][0] - self.t0
        if self.is_tv:
            self.lim_v = max(self.frames, int(-(-dt * self.fps // 1)))
            self.lim_a = int(round(self.lim_v / self.fps * self.sr))
        else:
            self.lim_v = None
            self.lim_a = max(self.samples, int(round(dt * self.sr)))

    def _hit(self):
        return (not self.is_tv or self.hit_v) and (not self.use_audio or self.hit_a)

    def run(self):
        try:
            self._open_io()
            first = True
            while not self.stop_evt.is_set():
                self._resync()
                now = self.clock() + 0.001          # Rundung: genau auf dem Startbild gilt der neue Eintrag
                occ, nxt = current(self.entries, now)
                if first or occ != self.occ:
                    self._enter(occ, nxt, join=first)
                    first = False
                else:
                    self.nxt = nxt
                    self._eit_tick()
                self._set_limit()
                if self._hit_now():
                    self._pad()
                    continue
                self._spawn(next(self.gen))
                th = []
                if self.is_tv:
                    th.append(threading.Thread(target=self._drain, args=(self.proc.stdout, self.qv, True), daemon=True))
                if self.use_audio:
                    th.append(threading.Thread(target=self._drain, args=(self.proc_a, self.qa, False), daemon=True))
                for t in th:
                    t.start()
                t_hit = None
                while any(t.is_alive() for t in th) and not self.stop_evt.is_set():
                    for t in th:
                        t.join(0.2)
                    c = self._cmd()
                    if c and c.get("action") == "eit":
                        self.set_eit_override(c.get("title"), c.get("text"), c.get("clear", False))
                    self._eit_tick()
                    if self.hit_v or self.hit_a:
                        t_hit = t_hit or time.time()
                        # Grenze erreicht (beide Spuren, sonst nach 2 s): Teilstueck abbrechen
                        if self._hit() or time.time() - t_hit > 2:
                            self.kill()
                            for t in th:
                                t.join(3)
                            break
                if self.stop_evt.is_set():
                    break
                self.proc.wait()
                try:
                    self.proc_a.close()
                except Exception:
                    pass
                self.lim_v = self.lim_a = None
                self._pad()
        except Exception as e:
            if not self.stop_evt.is_set():
                self.log("error", "Zeitplan-Feeder %s: %s" % (self.key, e))
        finally:
            self.kill()
            self._close_io()

    def _hit_now(self):
        """Grenze schon erreicht (Teilstueck endete genau am Umschaltpunkt)?"""
        v = self.is_tv and self.lim_v is not None and self.frames >= self.lim_v
        a = not self.is_tv and self.lim_a is not None and self.samples >= self.lim_a
        if v or a:
            # Stromuhr steht genau auf dem Start: naechste Runde waehlt den neuen Eintrag
            self.lim_v = self.lim_a = None
            return True
        return False
