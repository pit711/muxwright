#!/usr/bin/env python3
"""DTMB-Loopback-Matrix: TS → dtmb_mod (IQ-Datei, 15,12 MS/s, SRRC in Software) → dtmb-sdr (unabhängiger freier
Empfänger, MIT, github.com/ningzichun/dtmb-sdr) → TS → paketgenauer Vergleich.

Aufruf (WSL):  dtmb_tests.py DTMB_MOD WORKDIR DTMB_SDR_ROOT DTMB_SDR_BUILD [FALL …]
Ergebnisse: tests/results/dtmb_<fall>.json und dtmb_summary.json

Je Fall zusätzlich:
  * Systeminformation: Empfänger-Frontend mit --system-info-index auto (erkennt Modulation/Rate/Verschachtelung aus
    den 36 SI-Symbolen selbst) – der erkannte Index muss zum gesendeten passen.
  * Nutzrate aus Empfängersicht: Pakete je sauber dekodiertem Signalrahmen × 1504 bit × 7,56 MS/s / Rahmenlänge.
"""
import sys, os, json, subprocess, re, time, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, 'results')

CASES = {
    # name: (carriers, const, rate, pn, ti, extra)
    'c3780_64qam_0.6_pn945_ti720': (3780, '64qam', '0.6', 945, 720, []),
    'c3780_64qam_0.8_pn420_ti240': (3780, '64qam', '0.8', 420, 240, []),
    'c3780_16qam_0.6_pn945_ti240': (3780, '16qam', '0.6', 945, 240, []),
    'c3780_16qam_0.8_pn420_ti720': (3780, '16qam', '0.8', 420, 720, []),
    'c3780_32qam_0.8_pn420_ti720': (3780, '32qam', '0.8', 420, 720, []),
    'c3780_4qamnr_0.8_pn595_ti720': (3780, '4qamnr', '0.8', 595, 720, []),
    'c3780_4qam_0.4_pn945_ti240': (3780, '4qam', '0.4', 945, 240, []),
    'c3780_16qam_0.4_pn595_ti240': (3780, '16qam', '0.4', 595, 240, []),
    'c3780_64qam_0.4_pn420_ti720': (3780, '64qam', '0.4', 420, 720, []),
    'c3780_4qam_0.6_pn420_ti240_pnconst': (3780, '4qam', '0.6', 420, 240, ['--pn-const']),
    'c1_4qam_0.8_pn595_ti240': (1, '4qam', '0.8', 595, 240, []),
    'c1_4qam_0.4_pn595_ti720': (1, '4qam', '0.4', 595, 720, []),
}

def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)

def profile_index(const, rate, ti):
    base = {'4qamnr': 3, '32qam': 17}.get(const)
    if base is None:
        base = {'4qam': 5, '16qam': 11, '64qam': 19}[const] + 2 * ['0.4', '0.6', '0.8'].index(rate)
    return base + (ti == 720)

def run_case(name, mod, work, sdr, build, seconds=2.5):
    car, const, rate, pn, ti, extra = CASES[name]
    args = f"--carriers {car} --const {const} --rate {rate} --pn {pn} --ti {ti} " + " ".join(extra)
    rates = json.loads(sh(f"{mod} {args} --sps 2 --print-rates").stdout)
    npk = int(rates['ts_bps'] * seconds / 1504)
    ref, iq, dec, log = (os.path.join(work, f"{name}.{e}") for e in ('ref.ts', 'ci16', 'dec.ts', 'log'))
    sh(f"python3 {HERE}/dtmb_ts.py gen {npk} {ref} {zlib.crc32(name.encode()) & 0xffff}")
    t0 = time.time()
    r = sh(f"{mod} -i {ref} -o {iq} {args} --sps 2 --quiet")
    done = json.loads([l for l in r.stderr.splitlines() if l.startswith('{"done"')][-1])
    tmod = time.time() - t0
    idx = profile_index(const, rate, ti)
    env = f"PYTHONPATH={sdr}/python"
    fbm = 'c1' if car == 1 else 'c3780'
    t0 = time.time()
    sh(f"{env} python3 -m dtmb.decode --bin-dir {build} --input {iq} --input-format ci16 --input-rate 15120000 "
       f"--pn-mode pn{pn} --system-info-index {idx} --frame-body-mode {fbm} --error-policy continue --output {dec} 2> {log}")
    tdec = time.time() - t0
    cmpres = json.loads(sh(f"python3 {HERE}/dtmb_ts.py cmp {ref} {dec}").stdout)
    L = open(log).read()
    def kv(k):
        m = re.findall(rf"^{k}=(\S+)$", L, re.M)
        return m[-1] if m else None
    frames = int(kv('clean_signal_frames') or 0); outpk = int(kv('output_packets') or 0)
    flen = pn + 3780
    ppf = outpk / frames if frames else 0
    rx_rate = ppf * 1504 * 7.56e6 / flen
    # SI-Autoerkennung: Resampler + Frontend aus der dtmb-sdr-Kette, Index "auto"
    sys.path.insert(0, f"{sdr}/python")
    from dtmb import decode as D
    a = D.build_parser().parse_args(['--bin-dir', build, '--input', iq, '--input-format', 'ci16', '--input-rate', '15120000',
                                     '--pn-mode', f'pn{pn}', '--system-info-index', str(idx), '--frame-body-mode', fbm,
                                     '--pipeline-buffer-mib', '0'])
    cmds = D.build_commands(a)[:2]
    fe = cmds[1]; fe[fe.index('--system-info-index') + 1] = 'auto'
    fe[-1] = '/dev/null'; fe[-2:-2] = ['--max-frames', '600']
    p1 = subprocess.Popen(cmds[0], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    p2 = subprocess.run(fe, stdin=p1.stdout, capture_output=True, text=True)
    p1.stdout.close(); p1.kill(); p1.wait()
    si = dict(re.findall(r"^(system_info_auto_\w+|system_info_index)=(\S+)$", p2.stderr, re.M))
    res = dict(case=name, carriers=car, const=const, rate=rate, pn=pn, ti=ti, extra=extra, si_index_sent=idx,
               ts_bps_nominal=rates['ts_bps'], ts_bps_receiver=rx_rate, packets_per_frame_receiver=ppf,
               sample_rate=rates['sample_rate'], modulator=done, mod_wall_s=round(tmod, 1), dec_wall_s=round(tdec, 1),
               compare=cmpres, receiver=dict(clean_signal_frames=frames, output_packets=outpk, bch_unclean_blocks=kv('bch_unclean_blocks'),
                                             frame_body_mode=kv('frame_body_mode'), first_pn_phase=kv('first_pn_phase'),
                                             last_pn_phase=kv('last_pn_phase')),
               si_auto=si)
    ok = (cmpres['test_packets_bad'] == 0 and cmpres['missing_in_range'] == 0 and cmpres['first_seq'] == 0
          and cmpres['last_seq'] == npk - 1 and si.get('system_info_index') == str(idx) and abs(rx_rate - rates['ts_bps']) < 1)
    res['pass'] = bool(ok)
    with open(os.path.join(RES, f"dtmb_{name}.json"), 'w') as f:
        json.dump(res, f, indent=1)
    for fn in (iq,):
        if os.environ.get('KEEP') != '1':
            os.remove(fn)
    return res

def main():
    mod, work, sdr, build = sys.argv[1:5]
    names = sys.argv[5:] or list(CASES)
    os.makedirs(work, exist_ok=True); os.makedirs(RES, exist_ok=True)
    summ = []
    for n in names:
        r = run_case(n, mod, work, sdr, build)
        c = r['compare']
        line = dict(case=n, pass_=r['pass'], packets_ok=c['test_packets_ok'], bad=c['test_packets_bad'], missing=c['missing_in_range'],
                    first=c['first_seq'], last=c['last_seq'], ref=c['ref_packets'], ts_bps=round(r['ts_bps_nominal'], 1),
                    ts_bps_rx=round(r['ts_bps_receiver'], 1), si_sent=r['si_index_sent'], si_auto=r['si_auto'].get('system_info_index'),
                    si_locked=r['si_auto'].get('system_info_auto_locked'), bch_unclean=r['receiver']['bch_unclean_blocks'],
                    rt_x86=r['modulator']['rt_factor'])
        print(json.dumps(line), flush=True)
        summ.append(line)
    if not sys.argv[5:]:
        with open(os.path.join(RES, 'dtmb_summary.json'), 'w') as f:
            json.dump(summ, f, indent=1)

if __name__ == '__main__':
    main()
