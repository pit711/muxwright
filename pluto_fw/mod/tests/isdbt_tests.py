#!/usr/bin/env python3
"""Loopback-Testmatrix isdbt_mod → gr-isdbt (unabhängiger Empfänger) → TS-Vergleich.
Aufruf (WSL): isdbt_tests.py BIN WORKDIR [NAME ...]   Ergebnisse: tests/results/isdbt_<name>.json, isdbt_summary.json
Jede Schicht wird in einem eigenen gr-isdbt-Graphen dekodiert (statische Viterbi-Zustände in gr-isdbt)."""
import json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, 'results')
CS = {'qpsk': 4, '16qam': 16, '64qam': 64}
CR = {'1/2': 0, '2/3': 1, '3/4': 2, '5/6': 3, '7/8': 4}

CASES = [
    # name, mode, guard, layers, (a_mod, a_rate, a_ti), (b_mod, b_rate, b_ti), frames, ts ('pid' | 'av')
    ('m3_l2_g8_std', 3, '1/8', 2, ('qpsk', '2/3', 4), ('64qam', '3/4', 2), 20, 'pid'),
    ('m3_l2_g16_av', 3, '1/16', 2, ('qpsk', '2/3', 4), ('64qam', '3/4', 2), 50, 'av'),
    ('m3_l1_g4_16qam12_ti0', 3, '1/4', 1, ('16qam', '1/2', 0), None, 12, 'pid'),
    ('m3_l1_g8_64qam78_ti1', 3, '1/8', 1, ('64qam', '7/8', 1), None, 12, 'pid'),
    ('m3_l2_g32_mix', 3, '1/32', 2, ('16qam', '5/6', 1), ('qpsk', '7/8', 0), 12, 'pid'),
    ('m1_l2_g8', 1, '1/8', 2, ('qpsk', '1/2', 4), ('16qam', '2/3', 8), 40, 'pid'),
    ('m2_l2_g8', 2, '1/8', 2, ('qpsk', '2/3', 2), ('64qam', '3/4', 4), 30, 'pid'),
]


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)


def run_case(binp, work, c):
    name, mode, guard, nl, A, B, frames, tsk = c
    modopts = f'--mode {mode} --guard {guard} --layers {nl} --a-mod {A[0]} --a-rate {A[1]} --a-ti {A[2]}'
    if B: modopts += f' --b-mod {B[0]} --b-rate {B[1]} --b-ti {B[2]}'
    rates = json.loads(sh(f'{binp} {modopts} --print-rates').stdout)
    na, nb = rates['a_packets_per_frame'], rates['b_packets_per_frame']
    ts = os.path.join(work, name + '.ts')
    if tsk == 'pid':
        sh(f'python3 {HERE}/isdbt_mkts.py {ts} {frames} {na} {nb}')
        apids, pa, pb = '0x101', '0x101', '0x100'
    else:
        secs = frames * rates['frame_s']
        cmd = (f'ffmpeg -y -loglevel error -f lavfi -i testsrc2=size=1280x720:rate=30 -f lavfi -i sine=f=1000:sample_rate=48000 '
               f'-f lavfi -i testsrc=size=320x240:rate=15 -f lavfi -i sine=f=440:sample_rate=48000 -t {secs:.2f} '
               f'-map 0:v -map 1:a -map 2:v -map 3:a -c:v libx264 -pix_fmt yuv420p -preset veryfast -g 30 -x264-params nal-hrd=cbr '
               f'-b:v:0 8M -maxrate:v:0 8M -bufsize:v:0 4M -b:v:1 200k -maxrate:v:1 200k -bufsize:v:1 200k -profile:v:1 baseline '
               f'-c:a aac -b:a 64k -streamid 0:0x101 -streamid 1:0x102 -streamid 2:0x201 -streamid 3:0x202 '
               f'-program title=Full:st=0:st=1 -program title=OneSeg:st=2:st=3 -mpegts_pmt_start_pid 0x1FC8 '
               f'-muxrate {int(rates["total_bps"])} -f mpegts {ts}')
        r = sh(cmd)
        if r.returncode: return {'name': name, 'ok': False, 'error': 'ffmpeg: ' + r.stderr[-400:]}
        apids = '0x201,0x202'
        psi = '0,0x10,0x11,0x12,0x14,0x1fc8,0x1fc9'
        pa, pb = psi + ',0x201,0x202', 'not:0x201,0x202'
    iq = os.path.join(work, name + '.iq')
    r = sh(f'{binp} -i {ts} --layer-a-pids {apids} {modopts} -o {iq} --quiet')
    modlog = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ''
    res = {'name': name, 'mode': mode, 'guard': guard, 'layers': nl, 'A': A, 'B': B, 'rates': rates,
           'mod': json.loads(modlog) if modlog.startswith('{') else modlog}
    rxa = f'--mode {mode} --guard {guard} --layers {nl} --a {CS[A[0]]},{CR[A[1]]},{A[2]}'
    if B: rxa += f' --b {CS[B[0]]},{CR[B[1]]},{B[2]}'
    ok = True
    for L, pids, npf in [('a', pa, na)] + ([('b', pb, nb)] if B else []):
        out = os.path.join(work, name)
        log = sh(f'python3 {HERE}/isdbt_rx_grisdbt.py {iq} {out} {rxa} --only {L}').stdout
        cmp = json.loads(sh(f'python3 {HERE}/isdbt_compare.py {ts} {out}_{L}.ts {pids} {npf}').stdout)
        tm = {'tmcc_ok': log.count('TMCC OK'), 'tmcc_not_ok': log.count('TMCC NOT OK')}
        if L == 'a':
            m = re.search(r'TMCC ANALYSIS(.*?)Layer\s*: C', log, re.S)
            if m:
                tm['tmcc_print'] = [ln.split(':', 1)[1].strip() for ln in m.group(1).splitlines() if ':' in ln and 'Layer' not in ln]
        res['layer_' + L] = dict(cmp, **tm)
        ok = ok and cmp.get('ok', False) and tm['tmcc_ok'] > 0 and tm['tmcc_not_ok'] == 0
        if tsk == 'av':
            # Videostrom des Schichtdienstes dekodieren (Empfang beginnt mitten im GOP: Fehler vor dem ersten IDR normal)
            vp, ap_ = ('0x201', '0x202') if L == 'a' else ('0x101', '0x102')
            pr = sh(f'ffprobe -v quiet -select_streams i:{vp} -count_frames -show_entries stream=codec_name,profile,width,height,nb_read_frames '
                    f'-of csv=p=0 {out}_{L}.ts').stdout.strip()
            au = sh(f'ffprobe -v quiet -select_streams i:{ap_} -count_frames -show_entries stream=codec_name,nb_read_frames -of csv=p=0 {out}_{L}.ts').stdout.strip()
            res['layer_' + L]['ffprobe_video'] = pr
            res['layer_' + L]['ffprobe_audio'] = au
    res['ok'] = ok
    for f in (iq,):
        try: os.remove(f)
        except OSError: pass
    return res


def main():
    binp, work = sys.argv[1], sys.argv[2]
    sel = sys.argv[3:]
    os.makedirs(work, exist_ok=True)
    summ = []
    for c in CASES:
        if sel and c[0] not in sel: continue
        r = run_case(binp, work, c)
        with open(os.path.join(RES, f'isdbt_{c[0]}.json'), 'w') as f: json.dump(r, f, indent=1)
        line = {'name': c[0], 'ok': r['ok']}
        for L in ('a', 'b'):
            if 'layer_' + L in r:
                x = r['layer_' + L]
                line[L] = (f"{x.get('identical', 0)} Pakete identisch, verfälscht {x.get('corrupt')}, Lücken {x.get('gaps')} "
                           f"({x.get('lost')} Pakete, Einrasten), TMCC OK {x['tmcc_ok']}/NOT OK {x['tmcc_not_ok']}")
        print(json.dumps(line), flush=True)
        summ.append(line)
    if not sel:
        with open(os.path.join(RES, 'isdbt_summary.json'), 'w') as f: json.dump(summ, f, indent=1)


if __name__ == '__main__':
    main()
