"""Übersetzung der Log-Meldungen für die Logdatei-Ansicht (de, en, fr, es, it, nl, pl).

Die Meldungen entstehen auf Deutsch (log() in server.py). Für die häufigen Meldungen gibt es hier Muster mit benannten
Gruppen und eine Vorlage je Sprache; unbekannte Meldungen bleiben im Original (die Ansicht markiert sie).
"""
import re

LANGS = ("de", "en", "fr", "es", "it", "nl", "pl")

# (Muster, {Sprache: Vorlage}) – Vorlagen nutzen die benannten Gruppen des Musters
PATTERNS = [
    (r"^DAB-Lab Server, Backend (?P<b>\S+), Pluto (?P<u>\S+), Mindestdämpfung (?P<a>[\d.]+) dB, NCO (?P<s>an|aus)$", {
        "en": "Muxwright server, backend {b}, Pluto {u}, minimum attenuation {a} dB, NCO {s}", "fr": "Serveur Muxwright, backend {b}, Pluto {u}, atténuation minimale {a} dB, NCO {s}",
        "es": "Servidor Muxwright, backend {b}, Pluto {u}, atenuación mínima {a} dB, NCO {s}", "it": "Server Muxwright, backend {b}, Pluto {u}, attenuazione minima {a} dB, NCO {s}",
        "nl": "Muxwright-server, backend {b}, Pluto {u}, minimale verzwakking {a} dB, NCO {s}", "pl": "Serwer Muxwright, backend {b}, Pluto {u}, minimalne tłumienie {a} dB, NCO {s}"}),
    (r"^HTTP (?P<url>\S+)$", {l: "HTTP {url}" for l in ("en", "fr", "es", "it", "nl", "pl")}),
    (r"^RF: (?P<ch>\S+) (?P<f>[\d.]+) MHz, Versatz (?P<o>-?\d+) Hz, Dämpfung (?P<a>[\d.]+) dB, (?P<s>an|stumm)$", {
        "en": "RF: {ch} {f} MHz, offset {o} Hz, attenuation {a} dB, {s}", "fr": "RF : {ch} {f} MHz, décalage {o} Hz, atténuation {a} dB, {s}",
        "es": "RF: {ch} {f} MHz, desplazamiento {o} Hz, atenuación {a} dB, {s}", "it": "RF: {ch} {f} MHz, offset {o} Hz, attenuazione {a} dB, {s}",
        "nl": "RF: {ch} {f} MHz, offset {o} Hz, verzwakking {a} dB, {s}", "pl": "RF: {ch} {f} MHz, przesunięcie {o} Hz, tłumienie {a} dB, {s}"}),
    (r"^TX gestoppt, Pluto stumm \((?P<a>[^)]+)\)$", {
        "en": "TX stopped, Pluto muted ({a})", "fr": "TX arrêté, Pluto muet ({a})", "es": "TX detenido, Pluto en silencio ({a})",
        "it": "TX fermato, Pluto muto ({a})", "nl": "TX gestopt, Pluto gedempt ({a})", "pl": "TX zatrzymany, Pluto wyciszony ({a})"}),
    (r"^TX-Strom läuft$", {"en": "TX stream running", "fr": "Flux TX actif", "es": "Flujo TX en marcha", "it": "Flusso TX attivo",
                          "nl": "TX-stroom loopt", "pl": "Strumień TX działa"}),
    (r"^TX läuft \(Modulator auf dem Pluto\)$", {"en": "TX running (modulator on the Pluto)", "fr": "TX actif (modulateur sur le Pluto)",
        "es": "TX en marcha (modulador en el Pluto)", "it": "TX attivo (modulatore sul Pluto)", "nl": "TX loopt (modulator op de Pluto)",
        "pl": "TX działa (modulator na Pluto)"}),
    (r"^Pluto verbunden: (?P<rest>.+)$", {"en": "Pluto connected: {rest}", "fr": "Pluto connecté : {rest}", "es": "Pluto conectado: {rest}",
        "it": "Pluto collegato: {rest}", "nl": "Pluto verbonden: {rest}", "pl": "Pluto podłączony: {rest}"}),
    (r"^Pluto nicht verbunden: (?P<rest>.+)$", {"en": "Pluto not connected: {rest}", "fr": "Pluto non connecté : {rest}",
        "es": "Pluto no conectado: {rest}", "it": "Pluto non collegato: {rest}", "nl": "Pluto niet verbonden: {rest}", "pl": "Pluto nie podłączony: {rest}"}),
    (r"^dablab-agent erreichbar \((?P<fw>[^)]+)\)$", {"en": "device agent reachable ({fw})", "fr": "agent de l’appareil joignable ({fw})",
        "es": "agente del equipo accesible ({fw})", "it": "agente del dispositivo raggiungibile ({fw})", "nl": "apparaat-agent bereikbaar ({fw})",
        "pl": "agent urządzenia dostępny ({fw})"}),
    (r"^Runner \((?P<b>\w+)\) bereit$", {"en": "runner ({b}) ready", "fr": "runner ({b}) prêt", "es": "runner ({b}) listo", "it": "runner ({b}) pronto",
        "nl": "runner ({b}) gereed", "pl": "runner ({b}) gotowy"}),
    (r"^Sitzung (?P<s>\S+)$", {"en": "session {s}", "fr": "session {s}", "es": "sesión {s}", "it": "sessione {s}", "nl": "sessie {s}", "pl": "sesja {s}"}),
    (r"^Einrichtung abgeschlossen: Betriebsart (?P<m>\w+)(?P<cs> \S+)?$", {
        "en": "setup completed: operating mode {m}{cs}", "fr": "configuration terminée : mode {m}{cs}", "es": "configuración finalizada: modo {m}{cs}",
        "it": "configurazione completata: modalità {m}{cs}", "nl": "instellen afgerond: modus {m}{cs}", "pl": "konfiguracja zakończona: tryb {m}{cs}"}),
    (r"^Einrichtung zurückgesetzt – Pluto stumm$", {"en": "setup reset – Pluto muted", "fr": "configuration réinitialisée – Pluto muet",
        "es": "configuración restablecida – Pluto en silencio", "it": "configurazione azzerata – Pluto muto", "nl": "instellen teruggezet – Pluto gedempt",
        "pl": "konfiguracja zresetowana – Pluto wyciszony"}),
    (r"^Senden abgelehnt: (?P<why>.+)$", {"en": "transmission refused: {why}", "fr": "émission refusée : {why}", "es": "emisión rechazada: {why}",
        "it": "trasmissione rifiutata: {why}", "nl": "uitzenden geweigerd: {why}", "pl": "nadawanie odrzucone: {why}"}),
    (r"^Gerät: laufender Modulator wird beendet \((?P<c>.*)\)$", {"en": "device: stopping running modulator ({c})",
        "fr": "appareil : arrêt du modulateur en cours ({c})", "es": "equipo: deteniendo el modulador en marcha ({c})",
        "it": "dispositivo: arresto del modulatore in esecuzione ({c})", "nl": "apparaat: lopende modulator wordt gestopt ({c})",
        "pl": "urządzenie: zatrzymywanie działającego modulatora ({c})"}),
    (r"^Gerät: kopiere (?P<f>\S+) → (?P<d>\S+) \((?P<n>\d+) B\)$", {"en": "device: copying {f} → {d} ({n} B)", "fr": "appareil : copie de {f} → {d} ({n} o)",
        "es": "equipo: copiando {f} → {d} ({n} B)", "it": "dispositivo: copia di {f} → {d} ({n} B)", "nl": "apparaat: kopieer {f} → {d} ({n} B)",
        "pl": "urządzenie: kopiowanie {f} → {d} ({n} B)"}),
    (r"^Sweep beendet, Dämpfung zurück auf (?P<a>[\d.]+) dB$", {"en": "sweep finished, attenuation back to {a} dB",
        "fr": "balayage terminé, atténuation revenue à {a} dB", "es": "barrido terminado, atenuación de vuelta a {a} dB",
        "it": "sweep terminato, attenuazione riportata a {a} dB", "nl": "sweep klaar, verzwakking terug naar {a} dB", "pl": "przemiatanie zakończone, tłumienie z powrotem {a} dB"}),
    (r"^Prüfung startet \((?P<s>\d+) s\)$", {"en": "check starting ({s} s)", "fr": "vérification en cours ({s} s)", "es": "comprobación iniciada ({s} s)",
        "it": "verifica avviata ({s} s)", "nl": "controle start ({s} s)", "pl": "sprawdzanie rozpoczęte ({s} s)"}),
    (r"^Kette gestoppt, Pluto −89,75 dB$", {"en": "chain stopped, Pluto −89.75 dB", "fr": "chaîne arrêtée, Pluto −89,75 dB",
        "es": "cadena detenida, Pluto −89,75 dB", "it": "catena fermata, Pluto −89,75 dB", "nl": "keten gestopt, Pluto −89,75 dB", "pl": "tor zatrzymany, Pluto −89,75 dB"}),
    (r"^TX-Start \(Gerät\): (?P<e>.+)$", {"en": "TX start (device): {e}", "fr": "démarrage TX (appareil) : {e}", "es": "inicio TX (equipo): {e}",
        "it": "avvio TX (dispositivo): {e}", "nl": "TX-start (apparaat): {e}", "pl": "start TX (urządzenie): {e}"}),
]
# Einzelwörter (nur ganze Wörter), wenn kein Muster passt; Werte werden auch in Mustertreffern übersetzt (an/stumm)
WORDS = {
    "an": {"en": "on", "fr": "activé", "es": "activado", "it": "attivo", "nl": "aan", "pl": "włączony"},
    "aus": {"en": "off", "fr": "désactivé", "es": "desactivado", "it": "spento", "nl": "uit", "pl": "wyłączony"},
    "stumm": {"en": "muted", "fr": "muet", "es": "silencio", "it": "muto", "nl": "gedempt", "pl": "wyciszony"},
    "Fehler": {"en": "error", "fr": "erreur", "es": "error", "it": "errore", "nl": "fout", "pl": "błąd"},
    "gestoppt": {"en": "stopped", "fr": "arrêté", "es": "detenido", "it": "fermato", "nl": "gestopt", "pl": "zatrzymany"},
    "gestartet": {"en": "started", "fr": "démarré", "es": "iniciado", "it": "avviato", "nl": "gestart", "pl": "uruchomiony"},
    "Dämpfung": {"en": "attenuation", "fr": "atténuation", "es": "atenuación", "it": "attenuazione", "nl": "verzwakking", "pl": "tłumienie"},
    "Kette": {"en": "chain", "fr": "chaîne", "es": "cadena", "it": "catena", "nl": "keten", "pl": "tor"},
    "Gerät": {"en": "device", "fr": "appareil", "es": "equipo", "it": "dispositivo", "nl": "apparaat", "pl": "urządzenie"},
    "Konfiguration": {"en": "configuration", "fr": "configuration", "es": "configuración", "it": "configurazione", "nl": "configuratie", "pl": "konfiguracja"},
    "fehlgeschlagen": {"en": "failed", "fr": "échoué", "es": "fallido", "it": "non riuscito", "nl": "mislukt", "pl": "nieudane"},
    "nicht": {"en": "not", "fr": "pas", "es": "no", "it": "non", "nl": "niet", "pl": "nie"},
    "erreichbar": {"en": "reachable", "fr": "joignable", "es": "accesible", "it": "raggiungibile", "nl": "bereikbaar", "pl": "dostępny"},
    "bereit": {"en": "ready", "fr": "prêt", "es": "listo", "it": "pronto", "nl": "gereed", "pl": "gotowy"},
    "Prüfung": {"en": "check", "fr": "vérification", "es": "comprobación", "it": "verifica", "nl": "controle", "pl": "sprawdzanie"},
}
LEVELS = {"info": {"de": "Info", "en": "Info", "fr": "Info", "es": "Info", "it": "Info", "nl": "Info", "pl": "Info"},
          "warn": {"de": "Warnung", "en": "Warning", "fr": "Avertissement", "es": "Aviso", "it": "Avviso", "nl": "Waarschuwing", "pl": "Ostrzeżenie"},
          "error": {"de": "Fehler", "en": "Error", "fr": "Erreur", "es": "Error", "it": "Errore", "nl": "Fout", "pl": "Błąd"}}
_COMPILED = [(re.compile(p), t) for p, t in PATTERNS]
_WORD_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, WORDS), key=len, reverse=True)) + r")\b")


def _word(w, lang):
    return WORDS.get(w, {}).get(lang, w)


def translate(msg, lang):
    """-> (text, translated: bool). de oder unbekannte Sprache: Original."""
    if lang not in LANGS or lang == "de":
        return msg, True
    for rx, tpl in _COMPILED:
        m = rx.match(msg)
        if m and lang in tpl:
            g = {k: (_word(v, lang) if k == "s" else (v or "")) for k, v in m.groupdict().items()}
            return tpl[lang].format(**g), True
    return msg, False      # unbekannt: Original (gemischte Wort-für-Wort-Übersetzungen wären schlechter lesbar)
