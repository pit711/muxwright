ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"   # Repo-Wurzel (dablab/)
M=/opt/dablab/mod/x86s2; T=/tmp/s2sps; H=$ROOT/pluto_fw/mod/tests/dvbs2_hardrx.py
for ro in 0.35 0.20; do
$M/dvbs2_mod -i $T/in.ts -o $T/tx.iq --mod 8psk --rate 5/6 --pilots 0 --rolloff $ro --sps 2 --sr 333000 --quiet 2>/dev/null
python3 $H --iq $T/tx.iq --sps 2 --mod 8psk --rate 5/6 --pilots 0 --rolloff $ro -o $T/rx.ts | python3 -c "import sys,json;r=json.load(sys.stdin);print('$ro',{k:r[k] for k in ('ok','frames','bbheader_ok','up_crc_errors','pls_ok','mer_db')})"
python3 - <<'P'
a=open('/tmp/s2sps/in.ts','rb').read(); b=open('/tmp/s2sps/rx.ts','rb').read()
i=a.find(b[:752]); print("start",i, "len", len(b)//188)
bad=[k for k in range(len(b)//188) if a[i+188*k:i+188*k+188]!=b[188*k:188*k+188]]
print("abweichende Pakete", len(bad), bad[:10])
P
done
