"""DABLAB-Patches fuer ODR-DabMux 5.5.1:
 1) blockierendes Lesen aus FIFOs: Teil-Reads auffuellen statt 'rewind' (input/File.cpp, mpeg.c)  [DABLAB_FIFO]
 2) negativer local-time-offset: Vorzeichen nicht verwerfen (ConfigParser.cpp)                    [DABLAB_LTO]
Aufruf: python3 patch_dabmux.py [<Pfad zu ODR-DabMux>]   (Standard: ~/dablab/src/ODR-DabMux)
"""
import pathlib, sys
S = (pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path.home() / "dablab/src/ODR-DabMux") / "src"

p = S / "input/File.cpp"
t = p.read_text()
old = """        ret = read(m_fd, buffer, size);

        if (ret == -1) {
            etiLog.level(error) << "Can't read file " << strerror(errno);
            return -1;
        }

        if (ret < (ssize_t)size) {"""
new = """        /* DABLAB_FIFO: read() on a FIFO may return less than requested.
         * Keep reading until the frame is complete or EOF is reached. */
        ret = 0;
        while (ret < (ssize_t)size) {
            ssize_t r = read(m_fd, buffer + ret, size - ret);
            if (r == -1 and errno == EINTR) continue;
            if (r == -1) {
                etiLog.level(error) << "Can't read file " << strerror(errno);
                return -1;
            }
            if (r == 0) break;
            ret += r;
        }

        if (ret < (ssize_t)size) {"""
if "DABLAB_FIFO" not in t:
    assert old in t
    p.write_text(t.replace(old, new)); print("File.cpp patched")

p = S / "mpeg.c"
t = p.read_text()
old = """    offset = result;
    size -= offset;
    data = (char*)data + offset;
    result = readData(file, data, size, tries - 1);"""
new = """    offset = result;
    if (result == 0) return 0; /* DABLAB_FIFO: EOF */
    size -= offset;
    data = (char*)data + offset;
    /* DABLAB_FIFO: progress made -> keep trying (partial reads from FIFOs) */
    result = readData(file, data, size, tries);"""
if "DABLAB_FIFO" not in t:
    assert old in t
    p.write_text(t.replace(old, new)); print("mpeg.c patched")

p = S / "ConfigParser.cpp"
t = p.read_text()
old = "        ensemble->lto = abs(rint(lto_hours * 2));"
new = "        ensemble->lto = rint(lto_hours * 2); // DABLAB_LTO: Vorzeichen behalten (FIG 0/9 kodiert es)"
if "DABLAB_LTO" not in t:
    assert old in t
    p.write_text(t.replace(old, new)); print("ConfigParser.cpp patched (LTO)")
