"""DABLAB-Patch fuer ODR-AudioEnc 3.6.0 [DABLAB_QUEUE]:
SampleQueue::pop_wait() wartete in einer do-while-Schleife IMMER mindestens einmal wait_for(10 ms), auch wenn genug
Samples bereitlagen. Damit war der Encoder auf ~1 Rahmen je 10 ms gedrosselt (macOS/Linux: AAC-LC max. 2x Echtzeit),
unter WSL1 (Timer-Raster 15,6 ms) sogar auf 1,28x Echtzeit -> die Kette schaffte 4,096 MS/s nur knapp.
Fix: Bedingung vor dem Warten pruefen.
Aufruf: python3 patch_audioenc.py [<Pfad zu ODR-AudioEnc>]   (Standard: ~/dablab/src/ODR-AudioEnc)
"""
import pathlib, sys
S = (pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path.home() / "dablab/src/ODR-AudioEnc") / "src"
p = S / "SampleQueue.h"
t = p.read_text()
old = """        do {
            const auto wait_timeout = std::chrono::milliseconds(10);
            m_push_notification.wait_for(lock, wait_timeout);
"""
new = """        while (m_queue.size() < len) { // DABLAB_QUEUE: erst pruefen, dann warten
            const auto wait_timeout = std::chrono::milliseconds(10);
            m_push_notification.wait_for(lock, wait_timeout);
"""
old2 = """                break;
            }
        } while (m_queue.size() < len);
"""
new2 = """                break;
            }
        }
"""
if "DABLAB_QUEUE" not in t:
    assert old in t and old2 in t
    t = t.replace(old, new).replace(old2, new2)
    p.write_text(t)
    print("SampleQueue.h patched")

# [DABLAB_PADSYNC] PadInterface::request() las die PadEnc-Antwort nicht blockierend: lief der Encoder schneller als
# Echtzeit (render/check, oder nach DABLAB_QUEUE), kam die Antwort meist zu spaet -> die meisten AUs ohne PAD,
# DLS/Slides fehlten. Jetzt wird bis 40 ms auf die Antwort gewartet (PadEnc antwortet in << 1 ms).
p = S / "PadInterface.cpp"
t = p.read_text()
if "DABLAB_PADSYNC" not in t:
    t = t.replace("#include <unistd.h>\n", "#include <unistd.h>\n#include <poll.h> // DABLAB_PADSYNC\n", 1)
    old = "    vector<uint8_t> buffer(2048);\n\n    while (true) {\n        ret = ::recvfrom("
    new = ("    // DABLAB_PADSYNC: auf die Antwort von PadEnc warten (max. 40 ms)\n"
           "    if (m_padenc_reachable) {\n"
           "        struct pollfd pfd;\n        pfd.fd = m_sock; pfd.events = POLLIN; pfd.revents = 0;\n"
           "        ::poll(&pfd, 1, 40);\n    }\n\n" + old)
    assert old in t
    p.write_text(t.replace(old, new, 1))
    print("PadInterface.cpp patched")
