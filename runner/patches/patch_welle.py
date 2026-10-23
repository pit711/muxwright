"""DABLAB-Patches fuer welle-cli 2.7:
 DABLAB_DLPLUS: DL-Plus-Kommandos (TS 102 980) in /mux.json unter services[].dls.dlplus (Hex)
 DABLAB_MODE:   Uebertragungsmodus I-IV ueber Umgebungsvariable WELLE_DAB_MODE
 DABLAB_RAWFILE: Datei-Eingang haelt Echtzeit auch bei grobem Timer (WSL1)
"""
import pathlib, sys
# Aufruf: python3 patch_welle_dlplus.py [<Pfad zu welle.io>]   (Standard: ~/dablab/src/welle.io)
S = (pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path.home() / "dablab/src/welle.io") / "src"

def rep(rel, old, new, count=1):
    p = S / rel
    t = p.read_text()
    if new in t:          # schon gepatcht (idempotent)
        return
    assert t.count(old) >= 1, (rel, old[:60])
    p.write_text(t.replace(old, new, count)); print("patched", rel)

# 1) DL decoder: DL Plus command (0x02) auswerten statt verwerfen
rep("backend/pad_decoder.h",
    "\tDL_STATE GetLabel() {return label;}\n",
    "\tDL_STATE GetLabel() {return label;}\n"
    "\t// DABLAB_DLPLUS\n\tstd::vector<uint8_t> dlplus_cmd;\n\tbool dlplus_new = false;\n"
    "\tbool TakeDLPlus(std::vector<uint8_t>& out) {if(!dlplus_new) return false; dlplus_new = false; out = dlplus_cmd; return true;}\n")
rep("backend/pad_decoder.h",
    "\tvirtual void PADChangeSlide(const MOT_FILE& /*slide*/) {}\n",
    "\tvirtual void PADChangeSlide(const MOT_FILE& /*slide*/) {}\n"
    "\tvirtual void PADChangeDLPlus(const std::vector<uint8_t>& /*cmd*/) {} // DABLAB_DLPLUS\n")
rep("backend/pad_decoder.cpp",
    """		case 0x01:	// remove label
			cmd_remove_label = true;
			break;
""",
    """		case 0x01:	// remove label
			cmd_remove_label = true;
			break;
		case 0x02:	// DL Plus command (DABLAB_DLPLUS)
			cmd_dlplus = true;
			field_len = (dg_raw[1] & 0x0F) + 1;
			break;
""")
rep("backend/pad_decoder.cpp",
    "\tbool cmd_remove_label = false;\n",
    "\tbool cmd_remove_label = false;\n\tbool cmd_dlplus = false; // DABLAB_DLPLUS\n")
rep("backend/pad_decoder.cpp",
    """	// on Remove Label command, display empty label
	if(cmd_remove_label) {""",
    """	// DABLAB_DLPLUS: keep the DL Plus command field
	if(cmd_dlplus) {
		dlplus_cmd.assign(dg_raw.begin() + 2, dg_raw.begin() + 2 + field_len);
		dlplus_new = true;
		DataGroup::Reset();
		return false;
	}

	// on Remove Label command, display empty label
	if(cmd_remove_label) {""")
rep("backend/pad_decoder.cpp",
    """			if(dl_decoder.ProcessDataSubfield(xpad_ci.type == 2, xpad + xpad_offset, xpad_ci.len))
				observer->PADChangeDynamicLabel(dl_decoder.GetLabel());
""",
    """			if(dl_decoder.ProcessDataSubfield(xpad_ci.type == 2, xpad + xpad_offset, xpad_ci.len))
				observer->PADChangeDynamicLabel(dl_decoder.GetLabel());
			{	// DABLAB_DLPLUS
				std::vector<uint8_t> dlp;
				if(dl_decoder.TakeDLPlus(dlp))
					observer->PADChangeDLPlus(dlp);
			}
""")
# 2) Adapter -> ProgrammeHandlerInterface
rep("backend/decoder_adapter.h",
    "        virtual void PADChangeSlide(const MOT_FILE& slide);\n",
    "        virtual void PADChangeSlide(const MOT_FILE& slide);\n"
    "        virtual void PADChangeDLPlus(const std::vector<uint8_t>& cmd); // DABLAB_DLPLUS\n")
rep("backend/decoder_adapter.cpp",
    "void DecoderAdapter::PADChangeSlide(const MOT_FILE &slide)\n",
    "void DecoderAdapter::PADChangeDLPlus(const std::vector<uint8_t>& cmd) // DABLAB_DLPLUS\n"
    "{\n    myInterface.onDLPlus(cmd);\n}\n\n"
    "void DecoderAdapter::PADChangeSlide(const MOT_FILE &slide)\n")
rep("backend/radio-controller.h",
    "        virtual void onNewDynamicLabel(const std::string& label) = 0;\n",
    "        virtual void onNewDynamicLabel(const std::string& label) = 0;\n"
    "        /* DABLAB_DLPLUS: raw DL Plus command field */\n"
    "        virtual void onDLPlus(const std::vector<uint8_t>& /*cmd*/) {}\n")
# 3) WebProgrammeHandler
rep("welle-cli/webprogrammehandler.h",
    "            std::chrono::time_point<std::chrono::system_clock> last_changed; };\n        dls_t getDLS() const;\n",
    "            std::chrono::time_point<std::chrono::system_clock> last_changed;\n"
    "            std::string dlplus; /* DABLAB_DLPLUS hex */ };\n        dls_t getDLS() const;\n"
    "        std::string last_dlplus_hex; // DABLAB_DLPLUS\n")
rep("welle-cli/webprogrammehandler.h",
    "        virtual void onNewDynamicLabel(const std::string& label) override;\n",
    "        virtual void onNewDynamicLabel(const std::string& label) override;\n"
    "        virtual void onDLPlus(const std::vector<uint8_t>& cmd) override; // DABLAB_DLPLUS\n")
rep("welle-cli/webprogrammehandler.cpp",
    "        dls.last_changed = time_label_change;\n    }\n",
    "        dls.last_changed = time_label_change;\n    }\n    dls.dlplus = last_dlplus_hex; // DABLAB_DLPLUS\n")
rep("welle-cli/webprogrammehandler.cpp",
    "void WebProgrammeHandler::onNewDynamicLabel(const string& label)\n",
    "void WebProgrammeHandler::onDLPlus(const std::vector<uint8_t>& cmd) // DABLAB_DLPLUS\n{\n"
    "    static const char* hx = \"0123456789abcdef\";\n    std::string s;\n"
    "    for (auto b : cmd) { s += hx[b >> 4]; s += hx[b & 15]; }\n"
    "    std::unique_lock<std::mutex> lock(stats_mutex);\n    last_dlplus_hex = s;\n}\n\n"
    "void WebProgrammeHandler::onNewDynamicLabel(const string& label)\n")
# 4) JSON
rep("welle-cli/jsonconvert.h",
    "    std::string dls_label;\n",
    "    std::string dls_label;\n    std::string dls_dlplus; // DABLAB_DLPLUS\n")
rep("welle-cli/jsonconvert.cpp",
    """            {"label", s.dls_label},\n""",
    """            {"label", s.dls_label},\n            {"dlplus", s.dls_dlplus}, // DABLAB_DLPLUS\n""")
rep("welle-cli/webradiointerface.cpp",
    "                service.dls_label = dls.label;\n",
    "                service.dls_label = dls.label;\n                service.dls_dlplus = dls.dlplus; // DABLAB_DLPLUS\n")

# 5) DABLAB_MODE: Uebertragungsmodus I-IV per Umgebungsvariable WELLE_DAB_MODE (welle-cli kennt sonst nur Modus I)
rep("welle-cli/webradiointerface.cpp",
    '#include "welle-cli/webprogrammehandler.h"\n',
    '#include "welle-cli/webprogrammehandler.h"\n'
    'static int dablab_dab_mode() { const char* e = getenv("WELLE_DAB_MODE"); int m = e ? atoi(e) : 1; '
    'return (m >= 1 && m <= 4) ? m : 1; } // DABLAB_MODE\n')
rep("welle-cli/webradiointerface.cpp", "    dabparams(1),\n", "    dabparams(dablab_dab_mode()), // DABLAB_MODE\n")
p = S / "welle-cli/webradiointerface.cpp"
t = p.read_text()
if "rro, dablab_dab_mode())" not in t:
    t = t.replace("rx = make_unique<RadioReceiver>(*this, in, rro);",
                  "rx = make_unique<RadioReceiver>(*this, in, rro, dablab_dab_mode());")
    t = t.replace("rx = make_unique<RadioReceiver>(*this, input, rro);",
                  "rx = make_unique<RadioReceiver>(*this, input, rro, dablab_dab_mode());")
    p.write_text(t); print("patched mode", p)

# 6) DABLAB_RAWFILE: Datei-Eingang in Echtzeit auch bei grobem Timer (WSL1: 15,6 ms).
#    Puffer 256 KiB (= 16 ms Signal) -> 16 MiB, Verbraucher-Wartezeit 100 ms -> 1 ms.
#    Ohne das spielte welle-cli unter WSL1 eine 15-s-Datei in 40 s nicht einmal durch.
p = S / "input/raw_file.cpp"
t = p.read_text()
if "DABLAB_RAWFILE" not in t:
    t = t.replace("#define INPUT_FRAMEBUFFERSIZE 8 * 32768", "#define INPUT_FRAMEBUFFERSIZE (512 * 32768) // DABLAB_RAWFILE")
    old = """        if (readerPausing)
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        else
            std::this_thread::sleep_for(std::chrono::milliseconds(100));"""
    new = """        if (readerPausing)
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        else
            std::this_thread::sleep_for(std::chrono::milliseconds(1)); // DABLAB_RAWFILE"""
    assert old in t
    p.write_text(t.replace(old, new))
    print("patched rawfile", p)
