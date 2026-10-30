# -*- coding: utf-8 -*-
"""SSH-Terminal fuer das HbbTV-Portal: der Fernseher (Browser ohne TCP) spricht HTTP mit diesem PC, der PC haelt die
SSH-Verbindung (paramiko). Nur im LAN-Modus und nur, wenn ein Portal das Terminal erlaubt (portal.ssh).

Schutz: Freischaltung je Fernseher-IP per 6-stelliger PIN, die nur am PC selbst abrufbar ist (http://127.0.0.1:<port>
/hbbtv/ssh-pin; nicht im Protokoll, das im LAN-Modus ueber die API lesbar ist); 5 Versuche, 5 min gueltig, danach
15 min Sperre der IP, hoechstens 20 Fehlversuche pro Stunde insgesamt. Danach Token (12 h, an die IP gebunden).
Ziele: nur private LAN-Adressen (nicht der PC selbst, nicht link-local) oder die Liste portal.ssh_hosts; verbunden wird
mit der geprueften IP. Rechnerschluessel werden beim ersten Kontakt gespeichert (runtime/hbbtv_known_hosts) und danach
geprueft. Hoechstens 2 Sitzungen, 15 min Leerlauf. Passwoerter werden nicht gespeichert und nicht protokolliert.
"""
import ipaddress
import os
import secrets
import socket
import threading
import time

PIN_TTL, PIN_TRIES, TOKEN_TTL, IDLE, MAX_SESS, BUF_MAX = 300, 5, 12 * 3600, 900, 2, 1 << 20
LOCK_S, GLOBAL_FAILS = 900, 20


class Session:
    def __init__(self, ip, client, chan):
        self.ip, self.client, self.chan = ip, client, chan
        self.buf, self.base = "", 0           # Ausgabe ab Offset base
        self.closed = False
        self.t = time.time()
        self.cv = threading.Condition()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        import codecs
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                d = self.chan.recv(32768)
                if not d:
                    break
                with self.cv:
                    self.buf += dec.decode(d)
                    if len(self.buf) > BUF_MAX:                 # Anfang verwerfen, Offset mitzaehlen
                        cut = len(self.buf) - BUF_MAX
                        self.buf, self.base = self.buf[cut:], self.base + cut
                    self.cv.notify_all()
        except Exception:
            pass
        with self.cv:
            self.closed = True
            self.cv.notify_all()
        self.close()

    def read(self, off, wait=20):
        with self.cv:
            if off >= self.base + len(self.buf) and not self.closed:
                self.cv.wait(wait)
            start = max(0, off - self.base)
            return self.buf[start:], self.base + len(self.buf), self.closed

    def close(self):
        self.closed = True
        for x in (self.chan, self.client):
            try:
                x.close()
            except Exception:
                pass


class Gateway:
    def __init__(self, runtime, log, allowed, hosts=lambda: []):
        self.runtime, self.log, self.allowed, self.hosts = runtime, log, allowed, hosts
        self.port = 8792
        self.pins, self.tokens, self.sess = {}, {}, {}
        self.locked, self.fails = {}, []          # IP -> gesperrt bis; Zeitpunkte aller Fehlversuche
        self.lock = threading.RLock()

    def pending(self):
        """Offene PINs fuer die Anzeige am PC (nur localhost)."""
        now = time.time()
        with self.lock:
            return [(ip, p[0], int(p[1] - now)) for ip, p in self.pins.items() if p[1] > now and p[2] > 0]

    def _target(self, host, port):
        """Ziel pruefen und aufloesen: Liste portal.ssh_hosts oder private LAN-Adresse (nicht loopback/link-local)."""
        try:
            infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        except OSError:
            return None, "Rechner nicht gefunden"
        ip = infos[0][4][0]
        allow = [h.strip().lower() for h in self.hosts() if h.strip()]
        if allow:
            return (ip, None) if host.lower() in allow or ip in allow else (None, "Rechner nicht in der erlaubten Liste")
        a = ipaddress.ip_address(ip)
        if a.is_loopback or a.is_link_local or a.is_multicast or a.is_unspecified or not a.is_private:
            return None, "Nur Rechner im eigenen LAN erlaubt"
        return ip, None

    # ---------------------------------------------------------------- Freischaltung
    def _trusted(self, ip, token):
        with self.lock:
            t = self.tokens.get(token or "")
            return bool(t and t[0] == ip and t[1] > time.time())

    def _new_pin(self, ip):
        with self.lock:
            p = self.pins.get(ip)
            if p and p[1] > time.time() and p[2] > 0:
                return                               # gueltige PIN bleibt, bis sie benutzt wird oder ablaeuft
            self.pins[ip] = ["%06d" % secrets.randbelow(1000000), time.time() + PIN_TTL, PIN_TRIES]
        # PIN bewusst nicht ins Protokoll (im LAN-Modus ueber die API lesbar)
        self.log("warn", "HbbTV-SSH: Fernseher %s moechte ein Terminal oeffnen - PIN nur an diesem PC: "
                 "http://127.0.0.1:%s/hbbtv/ssh-pin" % (ip, self.port))

    def reset_locks(self):
        """Sperren aufheben (nur ueber die localhost-Seite am PC)."""
        with self.lock:
            self.locked, self.fails = {}, []
        self.log("info", "HbbTV-SSH: Sperren am PC aufgehoben")

    def locks(self):
        now = time.time()
        with self.lock:
            return [(ip, int(t - now)) for ip, t in self.locked.items() if t > now], len([t for t in self.fails if now - t < 3600])

    def _blocked(self, ip):
        now = time.time()
        with self.lock:
            self.fails = [t for t in self.fails if now - t < 3600]
            return self.locked.get(ip, 0) > now or len(self.fails) >= GLOBAL_FAILS

    def _check_pin(self, ip, pin):
        with self.lock:
            p = self.pins.get(ip)
            if not p or p[1] < time.time() or p[2] <= 0:
                return None
            if secrets.compare_digest(str(pin or ""), p[0]):
                del self.pins[ip]
                tok = secrets.token_urlsafe(24)
                self.tokens[tok] = (ip, time.time() + TOKEN_TTL)
                ok, left = True, 1
            else:
                p[2] -= 1
                self.fails.append(time.time())
                if p[2] <= 0:
                    self.locked[ip] = time.time() + LOCK_S
                ok, left = False, p[2]
        if ok:
            self.log("info", "HbbTV-SSH: Fernseher %s freigeschaltet (12 h)" % ip)
            return tok
        if left <= 0:
            self.log("warn", "HbbTV-SSH: Fernseher %s nach 5 falschen PINs fuer 15 min gesperrt" % ip)
        return False

    # ---------------------------------------------------------------- Befehle
    def open(self, ip, j):
        if not self.allowed():
            return {"ok": False, "error": "SSH-Terminal ist in Muxwright nicht erlaubt (Portal-Einstellungen)"}
        tok = j.get("token")
        if not self._trusted(ip, tok) and self._blocked(ip):
            return {"ok": False, "error": "Zu viele Fehlversuche - spaeter erneut versuchen"}
        # Ziel zuerst pruefen: eine richtige PIN soll nicht an einem unzulaessigen Ziel verbraucht werden
        host, user = str(j.get("host") or "").strip(), str(j.get("user") or "").strip()
        if not host or not user:
            return {"ok": False, "error": "Rechner und Benutzer angeben"}
        port = int(j.get("port") or 22)
        addr, why = self._target(host, port)
        if not addr:
            return {"ok": False, "error": why}
        if not self._trusted(ip, tok):
            if j.get("pin"):
                tok = self._check_pin(ip, j.get("pin"))
                if tok is False:
                    return {"ok": False, "need_pin": True, "error": "PIN falsch"}
            else:
                tok = None
            if not tok:
                self._new_pin(ip)
                return {"ok": False, "need_pin": True}

        def fail(msg):                               # Token immer mitgeben, damit keine neue PIN noetig wird
            return {"ok": False, "error": msg, "token": tok}

        self._gc()
        if sum(1 for s in self.sess.values() if not s.closed) >= MAX_SESS:
            return fail("Zu viele offene Sitzungen")
        try:
            import paramiko
        except ImportError:
            return fail("paramiko fehlt auf dem PC (pip install paramiko)")
        kh = os.path.join(self.runtime, "hbbtv_known_hosts")
        c = paramiko.SSHClient()
        try:
            if os.path.exists(kh):
                c.load_host_keys(kh)
        except Exception:
            pass
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())   # erster Kontakt: merken (unten gespeichert)
        try:
            c.connect(addr, port=port, username=user, password=str(j.get("password") or "") or None,
                      timeout=10, banner_timeout=10, auth_timeout=10, look_for_keys=False, allow_agent=False)
        except paramiko.BadHostKeyException:
            return fail("Rechnerschluessel hat sich geaendert - Verbindung abgelehnt (runtime/hbbtv_known_hosts pruefen)")
        except paramiko.AuthenticationException:
            return fail("Anmeldung abgelehnt")
        except Exception as e:
            return fail("Verbindung fehlgeschlagen: %s" % e.__class__.__name__)
        try:
            c.save_host_keys(kh)
        except Exception:
            pass
        key = c.get_transport().get_remote_server_key()
        fp = "%s %s" % (key.get_name(), ":".join("%02x" % b for b in key.get_fingerprint()))
        chan = c.invoke_shell(term="xterm", width=int(j.get("cols") or 80), height=int(j.get("rows") or 24))
        sid = secrets.token_hex(8)
        with self.lock:
            self.sess[sid] = Session(ip, c, chan)
        self.log("info", "HbbTV-SSH: %s -> %s@%s (%s):%s" % (ip, user, host, addr, port))
        return {"ok": True, "sid": sid, "token": tok, "fingerprint": fp}

    def _get(self, ip, sid, tok):
        s = self.sess.get(sid or "")
        if not s or s.ip != ip or not self._trusted(ip, tok):
            return None
        s.t = time.time()
        return s

    def read(self, ip, q):
        s = self._get(ip, q.get("sid"), q.get("t"))
        if not s:
            return {"ok": False, "error": "Sitzung unbekannt"}
        data, off, closed = s.read(int(q.get("o") or 0))
        return {"ok": True, "data": data, "o": off, "closed": closed}

    def write(self, ip, j):
        s = self._get(ip, j.get("sid"), j.get("t"))
        if not s or s.closed:
            return {"ok": False}
        try:
            s.chan.send(str(j.get("d") or "").encode("utf-8"))
        except Exception:
            return {"ok": False}
        return {"ok": True}

    def resize(self, ip, j):
        s = self._get(ip, j.get("sid"), j.get("t"))
        if s:
            try:
                s.chan.resize_pty(width=int(j.get("cols") or 80), height=int(j.get("rows") or 24))
            except Exception:
                pass
        return {"ok": bool(s)}

    def close(self, ip, j):
        s = self._get(ip, j.get("sid"), j.get("t"))
        if s:
            s.close()
        return {"ok": True}

    def _gc(self):
        now = time.time()
        with self.lock:
            for sid, s in list(self.sess.items()):
                if s.closed or now - s.t > IDLE:
                    s.close()
                    del self.sess[sid]
            for k, v in list(self.tokens.items()):
                if v[1] < now:
                    del self.tokens[k]
