# -*- coding: utf-8 -*-
"""SI-Tabellen als TSDuck-XML: NIT (Delivery-Descriptor, service_list, EACEM-LCN), SDT, EIT p/f, TDT/TOT, HbbTV-AIT."""
import os
import struct
import subprocess
import time
from xml.sax.saxutils import escape, quoteattr

from . import tables as T


def _a(v):
    return quoteattr(str(v))


def _hex(v):
    return '"0x%04X"' % int(v)


def nit_xml(cfg, version=1):
    std, ts, rf = cfg["mode"], cfg["ts"], cfg["rf"]
    d = cfg[std]
    L = ['<?xml version="1.0" encoding="UTF-8"?>', "<tsduck>",
         ' <NIT version=%s current="true" network_id=%s actual="true">' % (_a(version & 31), _hex(ts["network_id"])),
         '  <metadata PID="16"/>',
         '  <network_name_descriptor network_name=%s/>' % _a(ts["network_name"]),
         '  <transport_stream transport_stream_id=%s original_network_id=%s>'
         % (_hex(ts["transport_stream_id"]), _hex(ts["original_network_id"]))]
    if std == "isdbt":
        L.append('   <ISDB_terrestrial_delivery_system_descriptor area_code=%s guard_interval=%s transmission_mode=%s>'
                 % (_a(d["area_code"]), _a(d["guard"]), _a("mode%d" % d["mode"])))
        L.append('    <frequency value=%s/>' % _a(int(rf["freq_hz"])))
        L.append('   </ISDB_terrestrial_delivery_system_descriptor>')
        L.append('   <generic_descriptor tag="0xCD">%s</generic_descriptor>' % isdb_ts_information(cfg).hex().upper())
        one = [s for s in cfg["services"] if s.get("oneseg")]
        if one:
            L.append("   <partial_reception_descriptor>")
            L += ['    <service id=%s/>' % _hex(s["service_id"]) for s in one]
            L.append("   </partial_reception_descriptor>")
    elif std == "dvbt2":
        # T2_system_id = network_id (dvbt2_mod --t2-system-id, gleiche Zuordnung in pc/agent.py)
        L.append('   <T2_delivery_system_descriptor plp_id="0" T2_system_id=%s>' % _hex(ts["network_id"]))
        L.append('    <extension SISO_MISO="SISO" bandwidth=%s guard_interval=%s transmission_mode=%s other_frequency="false" tfs="false">'
                 % (_a("%dMHz" % d["bandwidth_mhz"]), _a(d["guard"]), _a(d["fft"])))
        L.append('     <cell cell_id=%s><centre_frequency value=%s/></cell>' % (_hex(d.get("cell_id", 0)), _a(int(rf["freq_hz"]))))
        L.append('    </extension>')
        L.append('   </T2_delivery_system_descriptor>')
    elif std == "dtmb":
        pass                              # GB/T 17975: kein eigener Delivery-Descriptor in TSDuck; Suchlauf ueber die Frequenz
    elif std == "dvbt":
        L.append('   <terrestrial_delivery_system_descriptor centre_frequency=%s bandwidth=%s priority="HP" '
                 'no_time_slicing="true" no_MPE_FEC="true" constellation=%s hierarchy_information="0" '
                 'code_rate_HP_stream=%s code_rate_LP_stream=%s guard_interval=%s transmission_mode=%s '
                 'other_frequency="false"/>'
                 % (_a(int(rf["freq_hz"])), _a("%dMHz" % d["bandwidth_mhz"]), _a(d["constellation"]),
                    _a(d["code_rate"]), _a(d["code_rate"]), _a(d["guard"]), _a(d["mode"])))
    elif std == "dvbs":
        s2 = d.get("system") == "dvbs2"
        L.append('   <satellite_delivery_system_descriptor frequency=%s orbital_position=%s west_east_flag=%s polarization=%s '
                 'roll_off=%s modulation_system=%s modulation_type=%s symbol_rate=%s FEC_inner=%s/>'
                 % (_a(int(d["frequency_hz"])), _a(d["orbital_position"]), _a(d["west_east"]), _a(d["polarization"]),
                    _a(d["roll_off"]), _a("DVB-S2" if s2 else "DVB-S"), _a(d.get("modulation", "QPSK")),
                    _a(int(d["symbol_rate"])), _a(T.DVBS2_NIT_FEC[d["code_rate"]] if s2 else d["code_rate"])))
    else:
        L.append('   <cable_delivery_system_descriptor frequency=%s FEC_outer="RS" modulation=%s symbol_rate=%s '
                 'FEC_inner="none"/>' % (_a(int(rf["freq_hz"])), _a(d["constellation"]), _a(int(d["symbol_rate"]))))
    L.append("   <service_list_descriptor>")
    for s in cfg["services"]:
        L.append('    <service service_id=%s service_type=%s/>' % (_hex(s["service_id"]), _a("0x%02X" % s["_service_type"])))
    L.append("   </service_list_descriptor>")
    if ts.get("lcn", True):
        L.append('   <private_data_specifier_descriptor private_data_specifier="0x00000028"/>')
        L.append("   <logical_channel_number_descriptor>")
        for s in cfg["services"]:
            L.append('    <service service_id=%s logical_channel_number=%s visible_service=%s/>'
                     % (_hex(s["service_id"]), _a(int(s["lcn"])), _a("true" if s.get("visible", True) else "false")))
        L.append("   </logical_channel_number_descriptor>")
    L += ["  </transport_stream>", " </NIT>", "</tsduck>", ""]
    return "\n".join(L)


def sdt_xml(cfg, version=1, names=None):
    """names: optionale Ueberschreibung {key: {"name":..,"provider":..}} (ctl)."""
    ts = cfg["ts"]
    names = names or {}
    L = ['<?xml version="1.0" encoding="UTF-8"?>', "<tsduck>",
         ' <SDT version=%s current="true" transport_stream_id=%s original_network_id=%s actual="true">'
         % (_a(version & 31), _hex(ts["transport_stream_id"]), _hex(ts["original_network_id"]))]
    for s in cfg["services"]:
        n = names.get(s["key"], {})
        eit = ts.get("eit", True)
        sched = eit and (s.get("source") or {}).get("kind") == "schedule"     # Zeitplan: EIT schedule (0x50) im TS
        L.append('  <service service_id=%s EIT_schedule=%s EIT_present_following=%s running_status="running" '
                 'CA_mode="false">' % (_hex(s["service_id"]), _a("true" if sched else "false"), _a("true" if eit else "false")))
        L.append('   <service_descriptor service_type=%s service_provider_name=%s service_name=%s/>'
                 % (_a("0x%02X" % s["_service_type"]), _a(n.get("provider") or s["provider"]), _a(n.get("name") or s["name"])))
        L.append("  </service>")
    L += [" </SDT>", "</tsduck>", ""]
    return "\n".join(L)


# --------------------------------------------------------------------------------------------- HbbTV (experimentell)
def ait_xml(svc, version=1):
    """AIT (ETSI TS 102 809 5.3.4, HbbTV TS 102 796 7.2.3) fuer die HbbTV-Anwendung eines Dienstes:
    application_type 0x0010, Transport HTTP (Basis-URL + initial_path) oder Objektkarussell im eigenen Dienst
    (component_tag, ohne remote_connection), Profil 0 / Version 1.1.1."""
    hb = svc["hbbtv"]
    if hb.get("transport") == "carousel":
        tp = '    <object_carousel component_tag=%s/>' % _a("0x%02X" % hb["_oc_tag"])
    else:
        tp = '    <http><url base=%s/></http>' % _a(hb["_base"])
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>', "<tsduck>",
        ' <AIT version=%s current="true" test_application_flag="false" application_type="0x0010">' % _a(version & 31),
        '  <metadata PID=%s/>' % _a(hb["_pid"]),
        '  <application control_code=%s>' % _a("0x01" if hb["autostart"] else "0x02"),   # AUTOSTART / PRESENT
        '   <application_identifier organization_id=%s application_id=%s/>' % (_a("0x%08X" % hb["org_id"]), _hex(hb["app_id"])),
        '   <transport_protocol_descriptor transport_protocol_label="1">',
        tp,
        '   </transport_protocol_descriptor>',
        '   <application_descriptor service_bound="true" visibility="3" application_priority="1">',
        '    <profile application_profile="0x0000" version="1.1.1"/>',
        '    <transport_protocol label="1"/>',
        '   </application_descriptor>',
        '   <application_name_descriptor><language code=%s application_name=%s/></application_name_descriptor>'
        % (_a(hb["_lang"]), _a(hb["name"])),
        '   <simple_application_location_descriptor initial_path=%s/>' % _a(hb["_path"]),
        "  </application>", " </AIT>", "</tsduck>", ""])


def ait_pmt_patch(svc, version=1):
    """XML-Patch fuer 'tsp -P pmt --patch-xml': haengt die AIT-PID als stream_type 0x05 mit
    application_signalling_descriptor (0x6F) an die PMT des Dienstes; beim Karussell zusaetzlich dessen PID als
    stream_type 0x0B (DSM-CC U-N) mit stream_identifier (component_tag), carousel_identifier und data_broadcast_id
    0x0123 (HbbTV, TS 102 796 7.2.5)."""
    hb = svc["hbbtv"]
    L = ['<?xml version="1.0" encoding="UTF-8"?>', "<tsduck>", " <PMT>",
         '  <component x-node="add" elementary_PID=%s stream_type="0x05">' % _hex(hb["_pid"]),
         '   <application_signalling_descriptor>',
         '    <application application_type="0x0010" AIT_version_number=%s/>' % _a(version & 31),
         "   </application_signalling_descriptor>", "  </component>"]
    if hb.get("transport") == "carousel":
        L += ['  <component x-node="add" elementary_PID=%s stream_type="0x0B">' % _hex(hb["_oc_pid"]),
              '   <stream_identifier_descriptor component_tag=%s/>' % _a("0x%02X" % hb["_oc_tag"]),
              '   <carousel_identifier_descriptor carousel_id=%s/>' % _a(hb["_carousel_id"]),
              '   <data_broadcast_id_descriptor data_broadcast_id="0x0123"/>', "  </component>"]
    return "\n".join(L + [" </PMT>", "</tsduck>", ""])


def isdb_ts_information(cfg):
    """ts_information_descriptor (ARIB STD-B10 6.2.42, ABNT NBR 15603): remote_control_key_id = virtuelle Kanalnummer,
    TS-Name, Dienste je Uebertragungsart (wie im Sendebetrieb: 0x0F = Vollsegment, 0xAF = 1seg/Teilempfang)."""
    ts, d = cfg["ts"], cfg["isdbt"]
    name = str(ts.get("network_name") or "LAB").encode("latin-1", "replace")[:20]
    groups = []
    if d.get("layers") == 2:
        groups = [(0x0F, [s for s in cfg["services"] if not s.get("oneseg")]), (0xAF, [s for s in cfg["services"] if s.get("oneseg")])]
    else:
        groups = [(0x0F, list(cfg["services"]))]
    groups = [(t, ss) for t, ss in groups if ss]
    b = bytes([d.get("remote_key", 1) & 0xFF, (len(name) << 2) | len(groups)]) + name
    for t, ss in groups:
        b += bytes([t, len(ss)]) + b"".join(struct.pack(">H", s["service_id"]) for s in ss)
    return b


# --------------------------------------------------------------------------------------------- ATSC PSIP (A/65)
ST_VIDEO = {"mpeg2": 0x02, "h264": 0x1B, "hevc": 0x24}
ST_AUDIO = {"ac3": 0x81, "aac": 0x0F, "mp2": 0x04}
GPS_EPOCH = 315964800                 # 1980-01-06 00:00:00 UTC
GPS_UTC_OFFSET = 18                   # Schaltsekunden seit 1980 (Stand 2017, unveraendert)


def tvct_xml(cfg, version=1):
    """TVCT (Antenne) bzw. CVCT (Kabel): virtuelle Kanaele major.minor mit service_location_descriptor."""
    ts = cfg["ts"]
    cable = cfg["mode"] == "dvbc"
    tag = "CVCT" if cable else "TVCT"
    mod = ("%s" % cfg["dvbc"]["constellation"]) if cable else "8-VSB"
    L = ['<?xml version="1.0" encoding="UTF-8"?>', "<tsduck>",
         ' <%s version=%s current="true" protocol_version="0" transport_stream_id=%s>' % (tag, _a(version & 31), _hex(ts["transport_stream_id"]))]
    for i, s in enumerate(cfg["services"]):
        short = "".join(ch for ch in str(s["name"]) if 32 <= ord(ch) < 127)[:7] or "LAB%d" % (i + 1)
        L.append('  <channel short_name=%s major_channel_number=%s minor_channel_number=%s modulation_mode=%s channel_TSID=%s '
                 'program_number=%s ETM_location="0" access_controlled="false" hidden=%s hide_guide="true" service_type=%s source_id=%s>'
                 % (_a(short), _a(int(ts.get("major_channel", 1))), _a(int(s.get("minor") or s["lcn"] or i + 1)), _a(mod),
                    _hex(ts["transport_stream_id"]), _a(s["service_id"]), _a("false" if s.get("visible", True) else "true"),
                    _a("dtv" if s["type"] == "tv" else "audio"), _a(i + 1)))
        L.append('   <service_location_descriptor PCR_PID=%s>' % _hex(s["_pcr_pid"]))
        if s["type"] == "tv":
            L.append('    <component stream_type=%s elementary_PID=%s/>' % (_a("0x%02X" % ST_VIDEO[s["video"]["codec"]]), _hex(s["_vpid"])))
        if s["_apid"] is not None:
            L.append('    <component stream_type=%s elementary_PID=%s ISO_639_language_code=%s/>'
                     % (_a("0x%02X" % ST_AUDIO[s["audio"]["codec"]]), _hex(s["_apid"]), _a(s["audio"].get("language", "eng")[:3])))
        L.append("   </service_location_descriptor>")
        L.append('   <extended_channel_name_descriptor><long_channel_name_text><string language="eng" text=%s/></long_channel_name_text>'
                 '</extended_channel_name_descriptor>' % _a(s["name"]))
        L.append("  </channel>")
    L += [" </%s>" % tag, "</tsduck>", ""]
    return "\n".join(L)


def mgt_xml(cfg, vct_bytes, vct_version=1, version=1):
    tag = "CVCT-current" if cfg["mode"] == "dvbc" else "TVCT-current"
    return "\n".join(['<?xml version="1.0" encoding="UTF-8"?>', "<tsduck>",
                      ' <MGT version=%s protocol_version="0">' % _a(version & 31),
                      '  <table type=%s PID="0x1FFB" version_number=%s number_bytes=%s/>' % (_a(tag), _a(vct_version & 31), _a(int(vct_bytes))),
                      " </MGT>", "</tsduck>", ""])


def stt_section(now=None):
    """System Time Table (A/65 6.1) als fertiger Abschnitt; GPS-Sekunden, keine Sommerzeit-Angabe."""
    t = int(time.time() if now is None else now)
    body = struct.pack(">HBBBBIBBB", 0x0000, 0xC1, 0, 0, 0, t - GPS_EPOCH + GPS_UTC_OFFSET, GPS_UTC_OFFSET, 0x60, 0)
    head = struct.pack(">BH", 0xCD, 0xF000 | (len(body) + 4))
    sec = head + body
    return sec + struct.pack(">I", _crc32_mpeg(sec))


def write_psip(host, sdir, cfg, version=1):
    """TVCT/CVCT und MGT als .bin (tstabcomp), STT schreibt die Kette sekuendlich nach."""
    vx = os.path.join(sdir, "vct.xml")
    host.atomic_write(vx, tvct_xml(cfg, version))
    subprocess.run([host.tool("tstabcomp"), "-o", os.path.join(sdir, "vct.bin"), vx], check=True, capture_output=True)
    n = os.path.getsize(os.path.join(sdir, "vct.bin"))
    mx = os.path.join(sdir, "mgt.xml")
    host.atomic_write(mx, mgt_xml(cfg, n, version, version))
    subprocess.run([host.tool("tstabcomp"), "-o", os.path.join(sdir, "mgt.bin"), mx], check=True, capture_output=True)
    write_stt(sdir)


def write_stt(sdir, now=None):
    p = os.path.join(sdir, "stt.bin")
    with open(p + ".tmp", "wb") as f:
        f.write(stt_section(now))
    os.replace(p + ".tmp", p)


def _utc(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t))


def _dur(sec):
    sec = max(1, int(round(sec)))
    return "%02d:%02d:%02d" % (sec // 3600, (sec // 60) % 60, sec % 60)


def write_eit(host, path, cfg, svc, present, following=None, now=None):
    """EIT p/f eines Dienstes schreiben: <path> (XML, zum Lesen) und <path ohne .xml>.bin (zwei binaere Abschnitte:
    0 = present, 1 = following), dazu die Versionsnummer 0..31 in <path>.ver weiterzaehlen.
    'tsp -P inject --poll-files' laedt die .bin-Datei bei jeder Aenderung komplett neu; Receiver erkennen die
    Aenderung an der neuen Version. (TSDuck legt aus XML beide Ereignisse in einen Abschnitt, das verletzt
    EN 300 468 5.2.4; eitinject uebernahm geaenderte Titel eines laufenden Ereignisses nicht.)"""
    vf = path + ".ver"
    try:
        with open(vf) as f:
            ver = (int(f.read().strip()) + 1) % 32
    except Exception:
        ver = 0
    now = now or time.time()
    host.atomic_write(path, eit_xml(cfg, svc, present, following, now=now, version=ver))
    binp = path[:-4] + ".bin" if path.endswith(".xml") else path + ".bin"
    tmp = binp + ".tmp%d" % os.getpid()
    with open(tmp, "wb") as f:
        f.write(eit_pf_sections(cfg, svc, present, following, version=ver, now=now))
    os.replace(tmp, binp)
    host.atomic_write(vf, str(ver))
    return ver


# --------------------------------------------------------------------------------------------- binaere EIT p/f
def _crc32_mpeg(data):
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc


def _bcd(n):
    return ((n // 10) << 4) | (n % 10)


def _mjd_utc(t):
    g = time.gmtime(t)
    mjd = int(t // 86400) + 40587          # 1970-01-01 = MJD 40587
    return struct.pack(">HBBB", mjd, _bcd(g.tm_hour), _bcd(g.tm_min), _bcd(g.tm_sec))


def _dur_bcd(sec):
    sec = max(1, int(round(sec)))
    h = min(99, sec // 3600)
    return bytes([_bcd(h), _bcd((sec // 60) % 60), _bcd(sec % 60)])


def dvb_text(s, maxlen=255):
    """DVB-Zeichenkette (EN 300 468 Anhang A): ASCII ohne Praefix, sonst ISO 8859-15 mit Praefix 0x10 0x00 0x0F."""
    s = str(s or "")
    try:
        b = s.encode("ascii")
    except UnicodeEncodeError:
        b = b"\x10\x00\x0f" + s.encode("iso-8859-15", "replace")
    return b[:maxlen]


def _event(eid, start, dur, running, lang, title, text):
    """running: True (4 = running), False (1 = not running) oder ein running_status als Zahl (0 = undefiniert)."""
    name = dvb_text(title, 120)
    txt = dvb_text(text, 250 - len(name))
    sed = (lang.encode("ascii", "replace")[:3].ljust(3, b" ") + bytes([len(name)]) + name + bytes([len(txt)]) + txt)
    desc = bytes([0x4D, len(sed)]) + sed
    rs = running if type(running) is int else (4 if running else 1)
    return struct.pack(">H", eid) + _mjd_utc(start) + _dur_bcd(dur) + struct.pack(">H", (rs << 13) | len(desc)) + desc


def eit_event_id(start):
    """event_id aus der Startzeit im 5-s-Raster (1..65534): p/f und schedule nennen dasselbe Ereignis gleich; eindeutig fuer
    Starts >= 5 s Abstand innerhalb von 3,7 Tagen (EIT schedule reicht 48 h voraus)."""
    return int(start) // 5 % 0xFFFE + 1


def eit_pf_sections(cfg, svc, present, following=None, version=0, now=None):
    ts = cfg["ts"]
    now = now or time.time()
    lang = svc["eit"].get("language", "deu")
    p_start = present.get("start") or (int(now) // 60 * 60)
    p_dur = float(present.get("duration_s") or svc["eit"]["duration_min"] * 60)
    evs = [_event(present.get("event_id", 1), p_start, p_dur, True, lang, present.get("title"), present.get("text"))]
    if following:
        f_dur = float(following.get("duration_s") or svc["eit"]["duration_min"] * 60)
        evs.append(_event(following.get("event_id", 2), following.get("start") or p_start + p_dur, f_dur, False, lang,
                          following.get("title"), following.get("text")))
    else:
        evs.append(b"")
    out = b""
    for sn, ev in enumerate(evs):
        body = struct.pack(">HBBBHHBB", int(svc["service_id"]), 0xC1 | ((int(version) & 31) << 1), sn, 1,
                           int(ts["transport_stream_id"]), int(ts["original_network_id"]), 1, 0x4E) + ev
        sec = struct.pack(">BH", 0x4E, 0xF000 | (len(body) + 4)) + body
        out += sec + struct.pack(">I", _crc32_mpeg(sec))
    return out


def eit_xml(cfg, svc, present, following=None, now=None, event_base=1, version=0):
    """EIT p/f actual als TSDuck-XML (Abschnitt 0 = present, 1 = following).
    present/following: {"title","text","start" (epoch, optional),"duration_s"}. Ohne Start beginnt das laufende
    Ereignis zur vollen Minute. Feste event_ids (present 1, following 2)."""
    ts = cfg["ts"]
    now = now or time.time()
    lang = svc["eit"].get("language", "deu")
    p_start = present.get("start") or (int(now) // 60 * 60)
    p_dur = float(present.get("duration_s") or svc["eit"]["duration_min"] * 60)
    L = ['<?xml version="1.0" encoding="UTF-8"?>', "<tsduck>",
         ' <EIT type="pf" version=%s current="true" actual="true" service_id=%s transport_stream_id=%s '
         'original_network_id=%s last_table_id="0x4E">'
         % (_a(int(version) % 32), _hex(svc["service_id"]), _hex(ts["transport_stream_id"]), _hex(ts["original_network_id"]))]

    def ev(eid, e, start, dur, running):
        L.append('  <event event_id=%s start_time=%s duration=%s running_status=%s CA_mode="false">'
                 % (_a(eid), _a(_utc(start)), _a(_dur(dur)), _a("running" if running else "not-running")))
        L.append('   <short_event_descriptor language_code=%s><event_name>%s</event_name><text>%s</text>'
                 '</short_event_descriptor>' % (_a(lang), escape(str(e.get("title") or "")), escape(str(e.get("text") or ""))))
        L.append("  </event>")

    ev(present.get("event_id", event_base), present, p_start, p_dur, True)
    if following:
        f_dur = float(following.get("duration_s") or svc["eit"]["duration_min"] * 60)
        ev(following.get("event_id", event_base + 1), following, following.get("start") or p_start + p_dur, f_dur, False)
    L += [" </EIT>", "</tsduck>", ""]
    return "\n".join(L)


# --------------------------------------------------------------------------------------------- EIT schedule
EIT_SECTION_MAX = 4093 - 4            # EN 300 468: EIT-Abschnitt hoechstens 4096 Byte (inkl. Kopf), ohne CRC


def eit_schedule_sections(cfg, svc, events, now, version=0, table_id=0x50):
    """EIT schedule actual (EN 300 468 5.2.4, TR 101 211 4.1.4) als Binaerabschnitte.
    events: [{"start","duration_s","title","text","event_id"}]. Die Tabelle 0x50 umfasst 4 Tage ab 00:00 UTC des aktuellen
    Tages in 32 Segmenten zu 3 h; ein Ereignis gehoert in das Segment seiner Startzeit, Segment n hat die Abschnitte
    8n..8n+7. Jedes Segment bis zum letzten belegten bekommt mindestens einen (ggf. leeren) Abschnitt."""
    ts = cfg["ts"]
    lang = svc["eit"].get("language", "deu")
    day0 = int(now) // 86400 * 86400
    segs = {}
    for e in sorted(events, key=lambda x: x["start"]):
        n = (int(e["start"]) - day0) // 10800
        if 0 <= n < 32:
            segs.setdefault(n, []).append(_event(int(e["event_id"]), e["start"], e["duration_s"], 0, lang,
                                                 e.get("title"), e.get("text")))
    last_seg = max(segs) if segs else (int(now) - day0) // 10800
    plan = []                                   # je Segment: Liste der Ereignis-Bytes je Abschnitt
    for n in range(last_seg + 1):
        secs = [b""]
        for ev in segs.get(n, []):
            if secs[-1] and 14 + len(secs[-1]) + len(ev) > EIT_SECTION_MAX:
                if len(secs) == 8:
                    break                       # mehr als 8 volle Abschnitte in 3 h: Rest entfaellt
                secs.append(b"")
            secs[-1] += ev
        plan.append(secs)
    last_sn = 8 * last_seg + len(plan[-1]) - 1
    out = b""
    for n, secs in enumerate(plan):
        for i, evb in enumerate(secs):
            body = struct.pack(">HBBBHHBB", int(svc["service_id"]), 0xC1 | ((int(version) & 31) << 1), 8 * n + i, last_sn,
                               int(ts["transport_stream_id"]), int(ts["original_network_id"]), 8 * n + len(secs) - 1,
                               table_id) + evb
            sec = struct.pack(">BH", table_id, 0xF000 | (len(body) + 4)) + body
            out += sec + struct.pack(">I", _crc32_mpeg(sec))
    return out


def write_eit_schedule(host, path, cfg, svc, events, now):
    """EIT schedule eines Dienstes nach <path> (.bin, atomar), Version in <path>.ver weiterzaehlen."""
    vf = path + ".ver"
    try:
        with open(vf) as f:
            ver = (int(f.read().strip()) + 1) % 32
    except Exception:
        ver = 0
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "wb") as f:
        f.write(eit_schedule_sections(cfg, svc, events, now, version=ver))
    os.replace(tmp, path)
    host.atomic_write(vf, str(ver))
    return ver


def time_xml(cfg, now=None):
    """TDT + TOT (local_time_offset_descriptor). Die Uhrzeit setzt spaeter 'tsp -P timeref --system' fort."""
    ts = cfg["ts"]
    now = now or time.time()
    off = ts.get("utc_offset_min", "auto")
    if off == "auto" or off is None:
        lt = time.localtime(now)
        off = int(lt.tm_gmtoff // 60) if hasattr(lt, "tm_gmtoff") else 60
    offs = str(int(off))  # Minuten (TSDuck: int)
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>', "<tsduck>",
        ' <TDT UTC_time=%s/>' % _a(_utc(now)),
        ' <TOT UTC_time=%s>' % _a(_utc(now)),
        '  <local_time_offset_descriptor>',
        '   <region country_code=%s country_region_id="0" local_time_offset=%s time_of_change="2037-01-01 00:00:00" '
        'next_time_offset=%s/>' % (_a(ts.get("country", "DEU")), _a(offs), _a(offs)),
        '  </local_time_offset_descriptor>', ' </TOT>', "</tsduck>", ""])
