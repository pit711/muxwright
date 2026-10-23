#!/bin/bash
# DAB-Lab Pluto-Firmware: Gerätetests (aus WSL „dablab“ ausführen, Pluto unter 192.168.2.1)
#   bash device_tests.sh [all|basic|rate|iqplay|dma|udp|agent]
# Ergebnisse als Text auf stdout (ins Messprotokoll kopieren).
P=192.168.2.1
SSH="sshpass -p analog ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 root@$P"
A=http://$P:8080
j() { curl -s -m 20 "$@"; echo; }

basic() {
	echo "## basic"
	$SSH "cat /opt/VERSIONS; uname -a; cat /proc/cmdline; nproc; cat /sys/module/industrialio_buffer_dma/parameters/max_block_size; cat /proc/sys/kernel/sched_rt_runtime_us; grep -E 'dma|usb' /proc/interrupts; for n in \$(grep 7c420000 /proc/interrupts | cut -d: -f1); do echo irq\$n affinity=\$(cat /proc/irq/\$n/smp_affinity); done; ps -o pid,comm,args | grep -E 'iiod|dablab|taskset' | grep -v grep; taskset -p \$(pidof iiod); iiod -V 2>&1 | head -1; iio_info 2>/dev/null | grep -E 'Library version|fw_version|hw_model'; grep -E 'cma|Cma' /proc/meminfo; free | head -2" 2>&1 | grep -v Warning
}
agent() {
	echo "## agent"
	j $A/api/status
	j -X POST -d '{"lo_hz":185360000,"atten_db":89.75}' $A/api/rf
	j $A/api/mod/log
}
rate() {
	echo "## rate: 2.048 MS/s über ad9361_set_bb_rate"
	j -X POST -d '{"rate_hz":2048000}' $A/api/rate
	echo "## rate: 2x-FIR mit eigenen Koeffizienten (16 Taps, Halbband-ähnlich), 2.048 MS/s"
	j -X POST -d '{"rate_hz":2048000,"interp":2,"dec":2,"taps":[-12,0,66,0,-220,0,1072,2048,1072,0,-220,0,66,0,-12,0]}' $A/api/rate
	echo "## rate: 9.142857 MS/s (DVB-T 8 MHz)"
	j -X POST -d '{"rate_hz":9142857}' $A/api/rate
	$SSH "cat /sys/bus/iio/devices/iio:device*/in_voltage_filter_fir_en 2>/dev/null; for d in /sys/bus/iio/devices/iio:device*; do [ \"\$(cat \$d/name)\" = ad9361-phy ] && cat \$d/out_voltage_sampling_frequency \$d/in_out_voltage_filter_fir_en; done" 2>&1 | grep -v Warning
}
iqplay() {
	echo "## iqplay 9.142857 MS/s aus Testdatei (Sinus) 60 s, lokal, Kern 1, SCHED_FIFO"
	$SSH "head -c 73142856 /dev/urandom > /tmp/test_9m.iq; ls -la /tmp/test_9m.iq" 2>&1 | grep -v Warning
	j -X POST -d '{"cmd":"iqplay","args":["-r","9142857","-f","/tmp/test_9m.iq","-t","60","-g","89.75"],"cpu":1,"rt_prio":50}' $A/api/mod/start
	sleep 63
	j $A/api/status
	j $A/api/mod/log
}
dma() {
	echo "## maximale lokale DMA-Rate: iqplay -z (Nullen) je 30 s bei 14/20/30/40/61.44 MS/s"
	for r in 14000000 20000000 30000000 40000000 61440000; do
		j -X POST -d "{\"cmd\":\"iqplay\",\"args\":[\"-r\",\"$r\",\"-z\",\"-t\",\"30\",\"-q\"],\"cpu\":1,\"rt_prio\":50}" $A/api/mod/start > /dev/null
		sleep 34
		echo "rate $r:"; j $A/api/mod/log | tail -c 400
	done
}
udp() {
	echo "## UDP-Durchsatz PC(WSL)->Pluto, iperf3 60 Mbit/s und 35 Mbit/s, 1316-Byte-Pakete (TS)"
	$SSH "pkill iperf3; (iperf3 -s -D >/dev/null 2>&1); sleep 1; pidof iperf3" 2>&1 | grep -v Warning
	for b in 35M 60M 100M; do echo "-- $b"; iperf3 -c $P -u -b $b -l 1316 -t 15 2>&1 | tail -4; done
	echo "-- TCP"; iperf3 -c $P -t 10 2>&1 | tail -4
	$SSH "pkill iperf3" 2>/dev/null
}
case "${1:-all}" in
	basic) basic ;; agent) agent ;; rate) rate ;; iqplay) iqplay ;; dma) dma ;; udp) udp ;;
	all) basic; agent; rate; iqplay; udp ;;
esac
