#!/usr/bin/env python3
"""Profil der Blockerzeugung (ohne Pipe): wo geht die Rechenzeit hin?"""
import cProfile, pstats, sys, os, time, io
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import dablab_run as host
from fm import fm_tx, dsp
cfg, E, W = fm_tx.normalize_fm(host.load_config(sys.argv[1] if len(sys.argv) > 1 else "examples/fm_standard.json"))
sess = fm_tx.FmSession(host, cfg, "profile-" + host.new_session_id(), render=True)
sess.build()
for i in range(5):
    sess.next_block()
pr = cProfile.Profile()
t0 = time.perf_counter(); c0 = time.process_time()
pr.enable()
N = 50
for i in range(N):
    sess.next_block()
pr.disable()
el = time.perf_counter() - t0; cpu = time.process_time() - c0
sig = N * dsp.N_OUT / dsp.FS_OUT
print("Signal %.1f s: wall %.2f s (%.1f %% eines Kerns), cpu %.2f s" % (sig, el, 100 * el / sig, cpu))
s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(14); print(s.getvalue()[:3500])
sess.stop()
