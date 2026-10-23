"""LG webOS (SSAP) – Kanal, Programminfo, Senderliste, Screenshot für den Empfängertest.

  python test/lgtv.py info            aktueller Eingang/Kanal/Programminfo
  python test/lgtv.py channels        Senderliste (Name, Nummer, Frequenz falls vorhanden)
  python test/lgtv.py shot [datei]    Screenshot (falls vom Modell erlaubt)
  python test/lgtv.py raw <uri> [json]
Beim ersten Aufruf fragt der Fernseher nach Erlaubnis; der Schlüssel wird in test/lgtv_key.json gespeichert.
"""
import json, os, ssl, sys, time, urllib.request
import websocket  # pip install websocket-client

TV = os.environ.get("LGTV_IP", "")   # IP des Fernsehers, z. B. 192.168.1.50
KEYFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lgtv_key.json")
PERMS = ["LAUNCH", "LAUNCH_WEBAPP", "APP_TO_APP", "CLOSE", "TEST_OPEN", "TEST_PROTECTED", "CONTROL_AUDIO",
         "CONTROL_DISPLAY", "CONTROL_INPUT_JOYSTICK", "CONTROL_INPUT_MEDIA_RECORDING", "CONTROL_INPUT_MEDIA_PLAYBACK",
         "CONTROL_INPUT_TV", "CONTROL_POWER", "CONTROL_TV_SCREEN", "CONTROL_TV_STANBY", "READ_APP_STATUS",
         "READ_CURRENT_CHANNEL", "READ_INPUT_DEVICE_LIST", "READ_NETWORK_STATE", "READ_RUNNING_APPS",
         "READ_TV_CHANNEL_LIST", "WRITE_NOTIFICATION_TOAST", "READ_POWER_STATE", "READ_COUNTRY_INFO",
         "READ_SETTINGS", "CONTROL_TV_POWER", "CONTROL_MOUSE_AND_KEYBOARD", "READ_INSTALLED_APPS",
         "READ_LGE_SDX", "READ_NOTIFICATIONS", "SEARCH", "WRITE_SETTINGS", "WRITE_NOTIFICATION_ALERT",
         "CHECK_BLUETOOTH_DEVICE", "READ_LGE_TV_INPUT_EVENTS", "READ_TV_CURRENT_TIME"]


def connect():
    try:
        ws = websocket.create_connection(f"wss://{TV}:3001", timeout=10, sslopt={"cert_reqs": ssl.CERT_NONE},
                                        suppress_origin=True)   # neuere webOS: „invalid origin“ mit Origin-Header
    except Exception:
        ws = websocket.create_connection(f"ws://{TV}:3000", timeout=10, suppress_origin=True)
    key = json.load(open(KEYFILE)).get("client-key") if os.path.exists(KEYFILE) else None
    payload = {"forcePairing": False, "pairingType": "PROMPT",
               "manifest": {"manifestVersion": 1, "appVersion": "1.1", "permissions": PERMS,
                            "signed": {"appId": "com.dablab.receivertest", "vendorId": "com.dablab",
                                       "created": "20261023", "localizedAppNames": {"": "DAB-Lab Test"},
                                       "localizedVendorNames": {"": "DAB-Lab"}, "permissions": PERMS, "serial": "1"},
                            "signatures": [{"signatureVersion": 1, "signature": ""}]}}
    if key:
        payload["client-key"] = key
    ws.send(json.dumps({"type": "register", "id": "reg0", "payload": payload}))
    t0 = time.time()
    while time.time() - t0 < int(os.environ.get("LGTV_PAIR_S", "60")):
        ws.settimeout(int(os.environ.get("LGTV_PAIR_S", "60")))
        m = json.loads(ws.recv())
        if m.get("type") == "registered":
            json.dump({"client-key": m["payload"]["client-key"]}, open(KEYFILE, "w"))
            return ws
        if m.get("type") == "error":
            raise SystemExit(f"Registrierung abgelehnt: {m}")
        if m.get("payload", {}).get("pairingType") == "PROMPT":
            print("→ Bitte am Fernseher „Zulassen“ bestätigen …", flush=True)
    raise SystemExit("Keine Bestätigung am Fernseher innerhalb 60 s")


_n = [0]


def req(ws, uri, payload=None):
    _n[0] += 1
    rid = f"r{_n[0]}"
    ws.send(json.dumps({"type": "request", "id": rid, "uri": uri, "payload": payload or {}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == rid:
            return m


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "info"
    ws = connect()
    if cmd == "info":
        for uri in ("ssap://com.webos.applicationManager/getForegroundAppInfo", "ssap://tv/getCurrentChannel",
                    "ssap://tv/getChannelProgramInfo", "ssap://tv/getExternalInputList"):
            m = req(ws, uri)
            print(uri.split("/")[-1], json.dumps(m.get("payload"), ensure_ascii=False)[:1500])
    elif cmd == "channels":
        m = req(ws, "ssap://tv/getChannelList")
        chs = (m.get("payload") or {}).get("channelList", [])
        print(len(chs), "Sender")
        for c in chs:
            print(c.get("channelNumber"), "|", c.get("channelName"), "|", c.get("channelType") or c.get("channelMode"),
                  "|", c.get("physicalNumber"), c.get("frequency") or "", "|", c.get("TSID"), c.get("ONID"), c.get("serviceId") or c.get("SVCID"))
    elif cmd == "shot":
        out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(KEYFILE), "lgtv_shot.jpg")
        m = req(ws, "ssap://tv/executeOneShot", {"path": "/tmp/capture.jpg", "method": "DISPLAY", "format": "JPG"})
        uri = (m.get("payload") or {}).get("imageUri")
        print(json.dumps(m.get("payload"), ensure_ascii=False)[:400])
        if uri:
            ctx = ssl._create_unverified_context()
            open(out, "wb").write(urllib.request.urlopen(uri, context=ctx, timeout=15).read())
            print("gespeichert:", out)
    elif cmd == "raw":
        m = req(ws, sys.argv[2], json.loads(sys.argv[3]) if len(sys.argv) > 3 else {})
        print(json.dumps(m, ensure_ascii=False, indent=1)[:4000])
    ws.close()


if __name__ == "__main__":
    main()
