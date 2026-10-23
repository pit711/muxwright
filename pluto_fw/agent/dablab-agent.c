/*
 * dablab-agent – kleiner Steuerdienst auf dem ADALM-Pluto (DAB-Lab).
 *
 * HTTP/JSON auf Port 8080 (konfigurierbar mit -p). Startet/stoppt Modulatoren
 * (dvbt_mod, dvbc_mod, fm_mod, iqplay ...) als Kindprozess, meldet Status
 * (CPU je Kern, Temperatur, DAC-Underflow-Zähler, Netz), setzt DAC-Rate/FIR
 * über libiio/libad9361 (lokaler Kontext) und RF-Parameter.
 *
 * Sicherheit: jeder Modulatorstart beginnt mit hardwaregain -89.75 dB; die
 * Dämpfung wird nie über 0 dB oder unter -89.75 dB gesetzt.
 *
 * API: siehe API.md neben dieser Datei.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdarg.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <sched.h>
#include <time.h>
#include <dirent.h>
#include <ctype.h>
#include <pthread.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <sys/sysinfo.h>
#include <sys/resource.h>
#include <sys/statvfs.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <iio.h>
#include <ad9361.h>

#define AGENT_VERSION "0.6"
#define MOD_LOG "/tmp/dablab-mod.log"
#define UPLOAD_DIR "/tmp/dablab_files"
#define UPLOAD_RESERVE (16LL << 20)	/* so viel muss in /tmp frei bleiben */
#define MAX_ARGS 64
#define MAX_TAPS 128
#define ATTEN_SAFE 89.75

static int g_port = 8080;
static volatile sig_atomic_t g_quit;

/* ------------------------------------------------------------------ util */
static double now_s(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return ts.tv_sec + ts.tv_nsec * 1e-9; }

static int read_file(const char *path, char *buf, size_t n)
{
	int fd = open(path, O_RDONLY); ssize_t r;
	if (fd < 0) return -1;
	r = read(fd, buf, n - 1); close(fd);
	if (r < 0) return -1;
	buf[r] = 0; while (r > 0 && (buf[r-1] == '\n')) buf[--r] = 0;
	return (int)r;
}
static int write_file(const char *path, const char *s)
{
	int fd = open(path, O_WRONLY); ssize_t r;
	if (fd < 0) return -1;
	r = write(fd, s, strlen(s)); close(fd);
	return r < 0 ? -1 : 0;
}

/* -------------------------------------------------------------- JSON out */
struct sb { char *p; size_t len, cap; };
static void sb_cat(struct sb *b, const char *fmt, ...)
{
	va_list ap; int n;
	for (;;) {
		va_start(ap, fmt); n = vsnprintf(b->p + b->len, b->cap - b->len, fmt, ap); va_end(ap);
		if (n < 0) return;
		if ((size_t)n < b->cap - b->len) { b->len += n; return; }
		b->cap = (b->cap + n + 1) * 2; b->p = realloc(b->p, b->cap);
	}
}
static void sb_str(struct sb *b, const char *s)
{
	sb_cat(b, "\"");
	for (; *s; s++) {
		unsigned char c = *s;
		if (c == '"' || c == '\\') sb_cat(b, "\\%c", c);
		else if (c < 0x20) sb_cat(b, "\\u%04x", c);
		else sb_cat(b, "%c", c);
	}
	sb_cat(b, "\"");
}

/* --------------------------------------------------------------- JSON in
 * Minimaler Parser für flache Objekte: Zahlen, Strings, Bool, Arrays aus
 * Zahlen/Strings. Genügt für unsere Befehle. */
struct jv { const char *s; int len; };	/* Rohwert (Textausschnitt) */

static const char *skipws(const char *p) { while (*p && isspace((unsigned char)*p)) p++; return p; }
static const char *skipval(const char *p)
{
	int depth = 0;
	p = skipws(p);
	if (*p == '"') { p++; while (*p && *p != '"') { if (*p == '\\' && p[1]) p++; p++; } return *p ? p + 1 : p; }
	if (*p == '[' || *p == '{') {
		for (; *p; p++) {
			if (*p == '"') { p++; while (*p && *p != '"') { if (*p == '\\' && p[1]) p++; p++; } if (!*p) return p; continue; }
			if (*p == '[' || *p == '{') depth++;
			else if (*p == ']' || *p == '}') { if (--depth == 0) return p + 1; }
		}
		return p;
	}
	while (*p && *p != ',' && *p != '}' && *p != ']' && !isspace((unsigned char)*p)) p++;
	return p;
}
/* findet Schlüssel key im Objekt obj (oberste Ebene) */
static int jget(const char *obj, const char *key, struct jv *out)
{
	const char *p = skipws(obj);
	size_t kl = strlen(key);
	if (*p != '{') return 0;
	p++;
	for (;;) {
		const char *k, *v, *e;
		p = skipws(p);
		if (*p == '}' || !*p) return 0;
		if (*p != '"') return 0;
		k = p + 1; e = k; while (*e && *e != '"') e++;
		p = skipws(e + 1); if (*p != ':') return 0;
		v = skipws(p + 1); e = skipval(v);
		if ((size_t)(e - k) > 0 && (size_t)(e - k) >= 0 && (size_t)(strchr(k, '"') - k) == kl && !strncmp(k, key, kl)) {
			out->s = v; out->len = (int)(e - v); return 1;
		}
		p = skipws(e); if (*p == ',') p++;
	}
}
static int jstr(const char *obj, const char *key, char *dst, size_t n)
{
	struct jv v; size_t i = 0; const char *p;
	if (!jget(obj, key, &v) || v.len < 2 || v.s[0] != '"') return 0;
	for (p = v.s + 1; p < v.s + v.len - 1 && i + 1 < n; p++) {
		if (*p == '\\' && p + 1 < v.s + v.len - 1) { p++; dst[i++] = (*p == 'n') ? '\n' : (*p == 't') ? '\t' : *p; }
		else dst[i++] = *p;
	}
	dst[i] = 0; return 1;
}
static int jnum(const char *obj, const char *key, double *d)
{
	struct jv v; char tmp[64];
	if (!jget(obj, key, &v) || v.len <= 0 || v.len >= (int)sizeof tmp || v.s[0] == '"' || v.s[0] == '[' || v.s[0] == '{') return 0;
	memcpy(tmp, v.s, v.len); tmp[v.len] = 0;
	if (!strcmp(tmp, "true")) { *d = 1; return 1; }
	if (!strcmp(tmp, "false")) { *d = 0; return 1; }
	*d = strtod(tmp, NULL); return 1;
}
/* Array aus Strings → argv (Zeiger in buf) */
static int jstrarr(const char *obj, const char *key, char *buf, size_t bufn, char **argv, int maxargs)
{
	struct jv v; const char *p, *end; int n = 0; size_t used = 0;
	if (!jget(obj, key, &v) || v.len < 2 || v.s[0] != '[') return 0;
	p = v.s + 1; end = v.s + v.len - 1;
	while (p < end && n < maxargs) {
		const char *e;
		p = skipws(p); if (p >= end) break;
		e = skipval(p);
		if (*p == '"') {
			size_t l = (size_t)(e - p) - 2;
			if (used + l + 1 > bufn) break;
			memcpy(buf + used, p + 1, l); buf[used + l] = 0; argv[n++] = buf + used; used += l + 1;
		} else {
			size_t l = (size_t)(e - p);
			if (used + l + 1 > bufn) break;
			memcpy(buf + used, p, l); buf[used + l] = 0; argv[n++] = buf + used; used += l + 1;
		}
		p = skipws(e); if (*p == ',') p++;
	}
	return n;
}
static int jnumarr(const char *obj, const char *key, double *out, int max)
{
	struct jv v; const char *p, *end; int n = 0;
	if (!jget(obj, key, &v) || v.len < 2 || v.s[0] != '[') return -1;
	p = v.s + 1; end = v.s + v.len - 1;
	while (p < end && n < max) {
		char *e; p = skipws(p); if (p >= end) break;
		out[n++] = strtod(p, &e); if (e == p) break; p = skipws(e); if (*p == ',') p++;
	}
	return n;
}

/* ------------------------------------------------------------------- IIO */
static struct iio_context *g_ctx;
static struct iio_device *g_phy, *g_dds, *g_xadc;
static pthread_mutex_t g_iio_lock = PTHREAD_MUTEX_INITIALIZER;

static int iio_open(void)
{
	if (g_ctx) return 0;
	g_ctx = iio_create_local_context();
	if (!g_ctx) return -1;
	iio_context_set_timeout(g_ctx, 2000);
	g_phy = iio_context_find_device(g_ctx, "ad9361-phy");
	g_dds = iio_context_find_device(g_ctx, "cf-ad9361-dds-core-lpc");
	g_xadc = iio_context_find_device(g_ctx, "xadc");
	return (g_phy && g_dds) ? 0 : -1;
}
static struct iio_channel *tx_lo(void) { return iio_device_find_channel(g_phy, "altvoltage1", true); }
static struct iio_channel *rx_lo(void) { return iio_device_find_channel(g_phy, "altvoltage0", true); }
static struct iio_channel *tx_ch(void) { return iio_device_find_channel(g_phy, "voltage0", true); }
static struct iio_channel *rx_ch(void) { return iio_device_find_channel(g_phy, "voltage0", false); }

static int set_atten(double db)	/* db >= 0: Dämpfung in dB */
{
	if (db < 0) db = 0; if (db > ATTEN_SAFE) db = ATTEN_SAFE;
	return iio_channel_attr_write_double(tx_ch(), "hardwaregain", -db) < 0 ? -1 : 0;
}
static int tx_mute(void) { return set_atten(ATTEN_SAFE); }

/* DAC-Underflow: sticky Bit 0 im Register 0x88 des DDS-Cores (write-1-clear) */
static long g_unf_count;
static int g_unf_last = -1;
static int dac_unf_read_clear(void)
{
	uint32_t v = 0;
	if (iio_device_reg_read(g_dds, 0x88, &v) < 0) return -1;
	if (v & 1) iio_device_reg_write(g_dds, 0x88, 1);
	return v & 1;
}

/* ------------------------------------------------------------- Modulator */
static pid_t g_mod_pid;
static char g_mod_cmdline[1024];
static double g_mod_started;
static int g_mod_exit = -1;	/* letzter Exit-Code / -Signal */
static int g_mod_cpu = 1, g_mod_prio = 50;
static pthread_mutex_t g_mod_lock = PTHREAD_MUTEX_INITIALIZER;

static pid_t find_pid(const char *comm)
{
	DIR *d = opendir("/proc"); struct dirent *e; pid_t r = 0; char path[64], buf[64];
	if (!d) return 0;
	while ((e = readdir(d))) {
		if (!isdigit((unsigned char)e->d_name[0])) continue;
		snprintf(path, sizeof path, "/proc/%s/comm", e->d_name);
		if (read_file(path, buf, sizeof buf) > 0 && !strcmp(buf, comm)) { r = atoi(e->d_name); break; }
	}
	closedir(d); return r;
}
static int set_affinity_pid(pid_t pid, int cpu, int nice_val)
{
	cpu_set_t set; CPU_ZERO(&set);
	if (cpu < 0) { CPU_SET(0, &set); CPU_SET(1, &set); } else CPU_SET(cpu, &set);
	if (sched_setaffinity(pid, sizeof set, &set) < 0) return -1;
	/* auch alle Threads */
	{
		char path[64]; DIR *d; struct dirent *e;
		snprintf(path, sizeof path, "/proc/%d/task", pid);
		if ((d = opendir(path))) {
			while ((e = readdir(d))) if (isdigit((unsigned char)e->d_name[0])) {
				sched_setaffinity(atoi(e->d_name), sizeof set, &set);
				if (nice_val > -100) setpriority(PRIO_PROCESS, atoi(e->d_name), nice_val);
			}
			closedir(d);
		}
	}
	return 0;
}
static void iiod_place(int cpu, int nice_val)
{
	pid_t p = find_pid("iiod");
	if (p) set_affinity_pid(p, cpu, nice_val);
}

static void mod_reap(void)
{
	int st; pid_t r;
	if (g_mod_pid <= 0) return;
	r = waitpid(g_mod_pid, &st, WNOHANG);
	if (r == g_mod_pid) {
		g_mod_exit = WIFEXITED(st) ? WEXITSTATUS(st) : WIFSIGNALED(st) ? 128 + WTERMSIG(st) : -1;
		g_mod_pid = 0;
		pthread_mutex_lock(&g_iio_lock); tx_mute(); pthread_mutex_unlock(&g_iio_lock);
		iiod_place(1, 0);	/* iiod bekommt Kern 1 zurück */
	}
}
static int mod_stop(int timeout_ms)
{
	double t0;
	if (g_mod_pid <= 0) return 0;
	kill(g_mod_pid, SIGTERM); t0 = now_s();
	while (g_mod_pid > 0 && (now_s() - t0) * 1000 < timeout_ms) { usleep(20000); mod_reap(); }
	if (g_mod_pid > 0) { kill(g_mod_pid, SIGKILL); usleep(100000); mod_reap(); }
	if (g_mod_pid > 0) { waitpid(g_mod_pid, NULL, 0); g_mod_pid = 0; }
	return 0;
}
static int mod_start(char **argv, int argc, int cpu, int prio, const char *cwd)
{
	pid_t pid; int fd;
	if (argc < 1) return -1;
	mod_stop(2000);
	pthread_mutex_lock(&g_iio_lock); tx_mute(); dac_unf_read_clear(); g_unf_count = 0; pthread_mutex_unlock(&g_iio_lock);
	iiod_place(0, 5);	/* iiod auf Kern 0, niedrigere Priorität */
	pid = fork();
	if (pid < 0) return -1;
	if (pid == 0) {
		cpu_set_t set; struct sched_param sp;
		fd = open(MOD_LOG, O_WRONLY | O_CREAT | O_TRUNC, 0644);
		if (fd >= 0) { dup2(fd, 1); dup2(fd, 2); close(fd); }
		fd = open("/dev/null", O_RDONLY); if (fd >= 0) { dup2(fd, 0); close(fd); }
		if (cwd && *cwd) { if (chdir(cwd)) {} }
		CPU_ZERO(&set); if (cpu < 0) { CPU_SET(0, &set); CPU_SET(1, &set); } else CPU_SET(cpu, &set);
		sched_setaffinity(0, sizeof set, &set);
		if (prio > 0) { sp.sched_priority = prio; sched_setscheduler(0, SCHED_FIFO, &sp); }
		setsid();
		argv[argc] = NULL;
		execvp(argv[0], argv);
		fprintf(stderr, "exec %s: %s\n", argv[0], strerror(errno));
		_exit(127);
	}
	g_mod_pid = pid; g_mod_started = now_s(); g_mod_exit = -1; g_mod_cpu = cpu; g_mod_prio = prio;
	{
		int i; g_mod_cmdline[0] = 0;
		for (i = 0; i < argc; i++) { strncat(g_mod_cmdline, argv[i], sizeof g_mod_cmdline - strlen(g_mod_cmdline) - 2); if (i + 1 < argc) strcat(g_mod_cmdline, " "); }
	}
	return 0;
}

/* letzte vollständige JSON-Zeile des Modulator-Logs (Statuszeile mit rt_factor, clipped, stuffed ...) */
static int mod_last_json(char *dst, size_t n)
{
	char buf[8192]; long sz; FILE *f = fopen(MOD_LOG, "r"); char *e, *b, *p; int depth;
	if (!f) return 0;
	fseek(f, 0, SEEK_END); sz = ftell(f); if (sz > (long)sizeof buf - 1) sz = sizeof buf - 1; fseek(f, -sz, SEEK_END);
	sz = (long)fread(buf, 1, sz, f); fclose(f); buf[sz] = 0;
	for (e = buf + sz; e > buf; ) {
		while (e > buf && (e[-1] == '\n' || e[-1] == '\r' || e[-1] == ' ')) e--;
		b = e; while (b > buf && b[-1] != '\n') b--;
		if (e - b >= 2 && *b == '{' && e[-1] == '}' && (size_t)(e - b) < n) {
			for (p = b, depth = 0; p < e; p++) { if (*p == '{') depth++; else if (*p == '}') depth--; else if ((unsigned char)*p < 0x20) { depth = -99; break; } }
			if (depth == 0) { memcpy(dst, b, e - b); dst[e - b] = 0; return 1; }
		}
		e = b;
	}
	return 0;
}

/* --------------------------------------------------------------- Status */
static unsigned long long g_cpu_prev[4][2];	/* busy, total je Kern (0,1) + gesamt(2) */
static double g_cpu_pct[3];
static void cpu_sample(void)
{
	FILE *f = fopen("/proc/stat", "r"); char line[256];
	if (!f) return;
	while (fgets(line, sizeof line, f)) {
		int idx; unsigned long long v[10] = {0}, tot = 0, idle; int i;
		if (!strncmp(line, "cpu ", 4)) idx = 2;
		else if (!strncmp(line, "cpu0", 4)) idx = 0;
		else if (!strncmp(line, "cpu1", 4)) idx = 1;
		else continue;
		sscanf(line + 4, "%llu %llu %llu %llu %llu %llu %llu %llu", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5], &v[6], &v[7]);
		for (i = 0; i < 8; i++) tot += v[i];
		idle = v[3] + v[4];
		{
			unsigned long long dt = tot - g_cpu_prev[idx][1], di = idle - g_cpu_prev[idx][0];
			if (dt > 0) g_cpu_pct[idx] = 100.0 * (double)(dt - di) / (double)dt;
			g_cpu_prev[idx][0] = idle; g_cpu_prev[idx][1] = tot;
		}
	}
	fclose(f);
}
static double read_temp(void)
{
	double raw = 0, off = 0, sc = 1;
	if (!g_xadc) return -273;
	iio_device_attr_read_double(g_xadc, "in_temp0_raw", &raw);
	iio_device_attr_read_double(g_xadc, "in_temp0_offset", &off);
	iio_device_attr_read_double(g_xadc, "in_temp0_scale", &sc);
	return (raw + off) * sc / 1000.0;
}
static void net_stats(struct sb *b)
{
	FILE *f = fopen("/proc/net/dev", "r"); char line[512]; int first = 1;
	sb_cat(b, "\"net\":{");
	if (f) {
		while (fgets(line, sizeof line, f)) {
			char name[32]; unsigned long long rb, rp, re, rd, tb, tp;
			char *c = strchr(line, ':'); if (!c) continue; *c = 0;
			sscanf(line, " %31s", name);
			if (sscanf(c + 1, "%llu %llu %llu %llu %*u %*u %*u %*u %llu %llu", &rb, &rp, &re, &rd, &tb, &tp) < 6) continue;
			if (!strcmp(name, "lo")) continue;
			sb_cat(b, "%s\"%s\":{\"rx_bytes\":%llu,\"rx_pkts\":%llu,\"rx_err\":%llu,\"rx_drop\":%llu,\"tx_bytes\":%llu,\"tx_pkts\":%llu}", first ? "" : ",", name, rb, rp, re, rd, tb, tp);
			first = 0;
		}
		fclose(f);
	}
	sb_cat(b, "}");
	f = fopen("/proc/net/snmp", "r");
	if (f) {
		char hdr[512];
		while (fgets(line, sizeof line, f)) {
			if (!strncmp(line, "Udp:", 4)) {
				strcpy(hdr, line);
				if (fgets(line, sizeof line, f)) {
					unsigned long long in = 0, noport = 0, inerr = 0, out = 0, rcvbuf = 0;
					sscanf(line + 4, "%llu %llu %llu %llu %llu", &in, &noport, &inerr, &out, &rcvbuf);
					sb_cat(b, ",\"udp\":{\"in\":%llu,\"noport\":%llu,\"in_err\":%llu,\"out\":%llu,\"rcvbuf_err\":%llu}", in, noport, inerr, out, rcvbuf);
				}
				break;
			}
		}
		fclose(f);
	}
}
static void status_json(struct sb *b)
{
	char buf[256]; struct sysinfo si; double d; long long ll; const char *s;
	double la[3] = {0};
	sysinfo(&si); getloadavg(la, 3);
	sb_cat(b, "{\"agent\":\"" AGENT_VERSION "\",");
	if (read_file("/opt/VERSIONS", buf, sizeof buf) > 0) { char *nl = strchr(buf, '\n'); if (nl) *nl = 0; sb_cat(b, "\"fw\":"); sb_str(b, buf); sb_cat(b, ","); }
	if (read_file("/proc/sys/kernel/osrelease", buf, sizeof buf) > 0) { sb_cat(b, "\"kernel\":"); sb_str(b, buf); sb_cat(b, ","); }
	sb_cat(b, "\"uptime_s\":%ld,\"load\":[%.2f,%.2f,%.2f],", si.uptime, la[0], la[1], la[2]);
	sb_cat(b, "\"mem_free_kb\":%lu,\"cpu_pct\":{\"cpu0\":%.1f,\"cpu1\":%.1f,\"all\":%.1f},", (unsigned long)(si.freeram * si.mem_unit / 1024), g_cpu_pct[0], g_cpu_pct[1], g_cpu_pct[2]);
	{ char mi[2048]; if (read_file("/proc/meminfo", mi, sizeof mi) > 0) { s = strstr(mi, "CmaFree:"); if (s) sb_cat(b, "\"cma_free_kb\":%ld,", atol(s + 8)); } }
	if (read_file("/sys/module/industrialio_buffer_dma/parameters/max_block_size", buf, sizeof buf) > 0) sb_cat(b, "\"iio_max_block_size\":%s,", buf);
	pthread_mutex_lock(&g_iio_lock);
	if (g_ctx) {
		sb_cat(b, "\"temp_c\":%.1f,", read_temp());
		sb_cat(b, "\"dac\":{\"underflows\":%ld,\"unf_bit\":%d,", g_unf_count, g_unf_last);
		if (iio_channel_attr_read_longlong(tx_ch(), "sampling_frequency", &ll) >= 0) sb_cat(b, "\"rate_hz\":%lld,", ll);
		if (iio_channel_attr_read_longlong(tx_lo(), "frequency", &ll) >= 0) sb_cat(b, "\"lo_hz\":%lld,", ll);
		if (iio_channel_attr_read_double(tx_ch(), "hardwaregain", &d) >= 0) sb_cat(b, "\"gain_db\":%.2f,", d);
		if (iio_channel_attr_read_longlong(tx_ch(), "rf_bandwidth", &ll) >= 0) sb_cat(b, "\"rf_bw_hz\":%lld,", ll);
		if (iio_channel_attr_read(tx_ch(), "filter_fir_en", buf, sizeof buf) >= 0) sb_cat(b, "\"fir_en\":%s,", buf);
		else if (iio_device_attr_read(g_phy, "in_out_voltage_filter_fir_en", buf, sizeof buf) >= 0) sb_cat(b, "\"fir_en\":%s,", buf);
		if (iio_channel_attr_read_longlong(rx_lo(), "frequency", &ll) >= 0) sb_cat(b, "\"rx_lo_hz\":%lld,", ll);
		if (iio_channel_attr_read_longlong(rx_ch(), "sampling_frequency", &ll) >= 0) sb_cat(b, "\"rx_rate_hz\":%lld,", ll);
		if (iio_device_attr_read(g_phy, "ensm_mode", buf, sizeof buf) >= 0) { sb_cat(b, "\"ensm\":"); sb_str(b, buf); sb_cat(b, ","); }
		b->len--; if (b->p[b->len] != ',') b->len++;
		sb_cat(b, "},");
	} else sb_cat(b, "\"iio\":\"unavailable\",");
	pthread_mutex_unlock(&g_iio_lock);
	pthread_mutex_lock(&g_mod_lock);
	mod_reap();
	sb_cat(b, "\"mod\":{\"running\":%s,\"pid\":%d,\"cmd\":", g_mod_pid > 0 ? "true" : "false", (int)g_mod_pid);
	sb_str(b, g_mod_cmdline);
	sb_cat(b, ",\"since_s\":%.1f,\"exit\":%d,\"cpu\":%d,\"rt_prio\":%d", g_mod_pid > 0 ? now_s() - g_mod_started : 0.0, g_mod_exit, g_mod_cpu, g_mod_prio);
	{	/* Statuszeile des Modulators unverändert durchreichen; clipped/rt_factor zusätzlich als eigene Felder */
		char js[4096]; const char *c;
		if (mod_last_json(js, sizeof js)) {
			sb_cat(b, ",\"stats\":%s", js);
			if ((c = strstr(js, "\"clipped\":"))) sb_cat(b, ",\"clipped\":%lld", atoll(c + 10));
			if ((c = strstr(js, "\"rt_factor\":"))) sb_cat(b, ",\"rt_factor\":%.3f", atof(c + 12));
		}
	}
	sb_cat(b, "},");
	pthread_mutex_unlock(&g_mod_lock);
	{
		pid_t ip = find_pid("iiod"); char p[64]; cpu_set_t set; int mask = 0;
		sb_cat(b, "\"iiod\":{\"pid\":%d", (int)ip);
		if (ip > 0 && sched_getaffinity(ip, sizeof set, &set) == 0) { if (CPU_ISSET(0, &set)) mask |= 1; if (CPU_ISSET(1, &set)) mask |= 2; sb_cat(b, ",\"cpumask\":%d", mask); }
		snprintf(p, sizeof p, "/proc/%d/stat", (int)ip);
		sb_cat(b, "},");
	}
	net_stats(b);
	sb_cat(b, "}");
}

/* ----------------------------------------------------------- Befehle */
static int cmd_rate(const char *body, struct sb *out)
{
	double rate = 0, taps[MAX_TAPS]; int ntaps, interp = 1, dec = 1, enable = -1; long long ll; char buf[128]; int ret = 0; double d;
	ntaps = jnumarr(body, "taps", taps, MAX_TAPS);
	if (jnum(body, "interp", &d)) interp = (int)d;
	if (jnum(body, "dec", &d)) dec = (int)d;
	if (jnum(body, "fir_en", &d)) enable = (int)d;
	jnum(body, "rate_hz", &rate);
	pthread_mutex_lock(&g_iio_lock);
	if (ntaps > 0) {
		/* eigene FIR-Koeffizienten: filter_fir_config-Text erzeugen */
		struct sb cfg = { calloc(1, 8192), 0, 8192 }; int i;
		if (ntaps % 16) { sb_cat(out, "{\"ok\":false,\"error\":\"taps must be multiple of 16 (max 128)\"}"); free(cfg.p); ret = -1; goto out; }
		sb_cat(&cfg, "# dablab FIR\nTX 3 GAIN 0 INT %d\nRX 3 GAIN -6 DEC %d\n", interp, dec);
		for (i = 0; i < ntaps; i++) sb_cat(&cfg, "%d,%d\n", (int)taps[i], (int)taps[i]);
		if (rate > 0 && (ret = iio_device_attr_write_raw(g_phy, "filter_fir_config", cfg.p, cfg.len)) < 0) { sb_cat(out, "{\"ok\":false,\"error\":\"filter_fir_config: %d\"}", ret); free(cfg.p); goto out; }
		else if (rate <= 0) { ret = iio_device_attr_write_raw(g_phy, "filter_fir_config", cfg.p, cfg.len); if (ret < 0) { sb_cat(out, "{\"ok\":false,\"error\":\"filter_fir_config: %d\"}", ret); free(cfg.p); goto out; } }
		free(cfg.p);
		if (rate > 0) {
			/* Rate mit aktivem FIR: zuerst FIR an, dann Rate (Treiber akzeptiert dann < 2.083 MS/s) */
			iio_device_attr_write(g_phy, "in_out_voltage_filter_fir_en", "1");
			ret = iio_channel_attr_write_longlong(tx_ch(), "sampling_frequency", (long long)rate);
			if (ret < 0) { sb_cat(out, "{\"ok\":false,\"error\":\"sampling_frequency: %d\"}", ret); goto out; }
		}
	} else if (rate > 0) {
		ret = ad9361_set_bb_rate(g_phy, (unsigned long)rate);	/* libad9361: wählt/berechnet FIR bei Bedarf */
		if (ret < 0) { sb_cat(out, "{\"ok\":false,\"error\":\"ad9361_set_bb_rate: %d\"}", ret); goto out; }
	}
	if (enable >= 0) iio_device_attr_write(g_phy, "in_out_voltage_filter_fir_en", enable ? "1" : "0");
	sb_cat(out, "{\"ok\":true");
	if (iio_channel_attr_read_longlong(tx_ch(), "sampling_frequency", &ll) >= 0) sb_cat(out, ",\"rate_hz\":%lld", ll);
	if (iio_channel_attr_read_longlong(rx_ch(), "sampling_frequency", &ll) >= 0) sb_cat(out, ",\"rx_rate_hz\":%lld", ll);
	if (iio_device_attr_read(g_phy, "in_out_voltage_filter_fir_en", buf, sizeof buf) >= 0) sb_cat(out, ",\"fir_en\":%s", buf);
	if (iio_channel_attr_read_longlong(tx_ch(), "rf_bandwidth", &ll) >= 0) sb_cat(out, ",\"rf_bw_hz\":%lld", ll);
	sb_cat(out, "}");
out:
	pthread_mutex_unlock(&g_iio_lock);
	return ret;
}
static int cmd_rf(const char *body, struct sb *out)
{
	double d; long long ll; int ret = 0; char buf[64];
	pthread_mutex_lock(&g_iio_lock);
	if (jnum(body, "atten_db", &d)) ret |= set_atten(d);
	if (jnum(body, "lo_hz", &d)) ret |= iio_channel_attr_write_longlong(tx_lo(), "frequency", (long long)d) < 0;
	if (jnum(body, "rx_lo_hz", &d)) ret |= iio_channel_attr_write_longlong(rx_lo(), "frequency", (long long)d) < 0;
	if (jnum(body, "rf_bw_hz", &d)) ret |= iio_channel_attr_write_longlong(tx_ch(), "rf_bandwidth", (long long)d) < 0;
	if (jnum(body, "rx_rf_bw_hz", &d)) ret |= iio_channel_attr_write_longlong(rx_ch(), "rf_bandwidth", (long long)d) < 0;
	if (jnum(body, "rx_gain_db", &d)) { iio_channel_attr_write(rx_ch(), "gain_control_mode", "manual"); ret |= iio_channel_attr_write_double(rx_ch(), "hardwaregain", d) < 0; }
	if (jstr(body, "ensm", buf, sizeof buf)) ret |= iio_device_attr_write(g_phy, "ensm_mode", buf) < 0;
	if (jnum(body, "mute", &d) && d) ret |= tx_mute();
	sb_cat(out, "{\"ok\":%s", ret ? "false" : "true");
	if (iio_channel_attr_read_longlong(tx_lo(), "frequency", &ll) >= 0) sb_cat(out, ",\"lo_hz\":%lld", ll);
	if (iio_channel_attr_read_double(tx_ch(), "hardwaregain", &d) >= 0) sb_cat(out, ",\"gain_db\":%.2f", d);
	if (iio_channel_attr_read_longlong(tx_ch(), "rf_bandwidth", &ll) >= 0) sb_cat(out, ",\"rf_bw_hz\":%lld", ll);
	sb_cat(out, "}");
	pthread_mutex_unlock(&g_iio_lock);
	return ret;
}
static int cmd_mod_start(const char *body, struct sb *out)
{
	char abuf[2048], *argv[MAX_ARGS + 1], cmd[256], cwd[256] = ""; int argc, cpu = 1, prio = 50; double d, atten = ATTEN_SAFE;
	int is_pal = 0;
	argc = jstrarr(body, "args", abuf, sizeof abuf, argv + 1, MAX_ARGS - 1);
	if (!jstr(body, "cmd", cmd, sizeof cmd)) { sb_cat(out, "{\"ok\":false,\"error\":\"cmd missing\"}"); return -1; }
	argv[0] = cmd; argc += 1;
	if (!strcmp(cmd, "pal") || !strcmp(cmd, "cyclic")) {
		/* zyklische IQ-Datei (z. B. PAL-B/G-Schleife aus pal_bg_gen.py): iqplay -N -c, ganze Datei als ein DMA-Puffer.
		 * Felder: file (Name in UPLOAD_DIR oder absoluter Pfad), rate_hz (8000000), rf_bw_hz (8000000), lo_hz, atten_db, seconds */
		static char sfile[300], srate[32], sbw[32], slo[32], satt[32], ssec[32]; char f[256]; struct stat st;
		if (!jstr(body, "file", f, sizeof f) || !*f || strstr(f, "..")) { sb_cat(out, "{\"ok\":false,\"error\":\"file missing/invalid\"}"); return -1; }
		if (f[0] == '/') snprintf(sfile, sizeof sfile, "%s", f); else snprintf(sfile, sizeof sfile, UPLOAD_DIR "/%s", f);
		if (stat(sfile, &st) < 0 || st.st_size < 16 || st.st_size % 4) { sb_cat(out, "{\"ok\":false,\"error\":\"file not found or size not multiple of 4\"}"); return -1; }
		d = 8000000; jnum(body, "rate_hz", &d); snprintf(srate, sizeof srate, "%.0f", d);
		d = 8000000; jnum(body, "rf_bw_hz", &d); snprintf(sbw, sizeof sbw, "%.0f", d);
		atten = ATTEN_SAFE; if (jnum(body, "atten_db", &d)) atten = d < 0 ? 0 : d > ATTEN_SAFE ? ATTEN_SAFE : d; snprintf(satt, sizeof satt, "%.2f", atten);
		argc = 0; argv[argc++] = "/usr/bin/iqplay"; argv[argc++] = "-N"; argv[argc++] = "-c";
		argv[argc++] = "-r"; argv[argc++] = srate; argv[argc++] = "-w"; argv[argc++] = sbw;
		argv[argc++] = "-f"; argv[argc++] = sfile; argv[argc++] = "-g"; argv[argc++] = satt;
		if (jnum(body, "lo_hz", &d)) { snprintf(slo, sizeof slo, "%.0f", d); argv[argc++] = "-l"; argv[argc++] = slo; }
		if (jnum(body, "seconds", &d) && d > 0) { snprintf(ssec, sizeof ssec, "%.0f", d); argv[argc++] = "-t"; argv[argc++] = ssec; }
		is_pal = 1;
	}
	if (jnum(body, "cpu", &d)) cpu = (int)d;
	if (jnum(body, "rt_prio", &d)) prio = (int)d;
	jstr(body, "cwd", cwd, sizeof cwd);
	if (prio > 90) prio = 90;
	pthread_mutex_lock(&g_mod_lock);
	if (mod_start(argv, argc, cpu, prio, cwd) < 0) { pthread_mutex_unlock(&g_mod_lock); sb_cat(out, "{\"ok\":false,\"error\":\"fork failed\"}"); return -1; }
	pthread_mutex_unlock(&g_mod_lock);
	/* optionale Start-Dämpfung (nach dem Start); bei pal/cyclic setzt iqplay sie selbst, sobald der Puffer gefüllt ist */
	if (!is_pal && jnum(body, "atten_db", &d)) { atten = d; usleep(300000); pthread_mutex_lock(&g_iio_lock); set_atten(atten); pthread_mutex_unlock(&g_iio_lock); }
	sb_cat(out, "{\"ok\":true,\"pid\":%d,\"log\":\"" MOD_LOG "\"}", (int)g_mod_pid);
	return 0;
}
static int cmd_iiod(const char *body, struct sb *out)
{
	double d; int cpu = 1, nice_val = 0; pid_t p;
	if (jnum(body, "cpu", &d)) cpu = (int)d;
	if (jnum(body, "nice", &d)) nice_val = (int)d;
	p = find_pid("iiod");
	if (!p) { sb_cat(out, "{\"ok\":false,\"error\":\"iiod not running\"}"); return -1; }
	set_affinity_pid(p, cpu, nice_val);
	sb_cat(out, "{\"ok\":true,\"pid\":%d,\"cpu\":%d,\"nice\":%d}", (int)p, cpu, nice_val);
	return 0;
}
static void cmd_log(struct sb *out, int lines)
{
	char *buf; long sz; FILE *f = fopen(MOD_LOG, "r"); int n = 0; char *p;
	sb_cat(out, "{\"ok\":true,\"log\":");
	if (!f) { sb_str(out, ""); sb_cat(out, "}"); return; }
	fseek(f, 0, SEEK_END); sz = ftell(f); if (sz > 65536) sz = 65536; fseek(f, -sz, SEEK_END);
	buf = calloc(1, sz + 1); if (fread(buf, 1, sz, f)) {} fclose(f);
	p = buf + sz; while (p > buf && n <= lines) { p--; if (*p == '\n') n++; }
	if (n > lines) p++;
	sb_str(out, p); sb_cat(out, "}"); free(buf);
}

/* ------------------------------------------------------------- Upload */
static int name_ok(const char *n)
{
	size_t i, l = strlen(n);
	if (!l || l > 64 || n[0] == '.') return 0;
	for (i = 0; i < l; i++) if (!(isalnum((unsigned char)n[i]) || n[i] == '.' || n[i] == '_' || n[i] == '-')) return 0;
	return 1;
}
static void url_param(const char *query, const char *key, char *dst, size_t n)
{
	const char *p = query; size_t kl = strlen(key); dst[0] = 0;
	while (p && *p) {
		if (!strncmp(p, key, kl) && p[kl] == '=') { size_t i = 0; p += kl + 1; while (*p && *p != '&' && i + 1 < n) dst[i++] = *p++; dst[i] = 0; return; }
		p = strchr(p, '&'); if (p) p++;
	}
}
/* POST /api/upload?name=X  Body = Rohdaten → UPLOAD_DIR/X (gestreamt, Größe aus Content-Length) */
static void cmd_upload(int fd, const char *query, const char *have, size_t have_len, long long clen, struct sb *out)
{
	char name[80], path[160], tmp[176], buf[65536]; struct statvfs vs; long long left, done = 0; int o; ssize_t r;
	url_param(query, "name", name, sizeof name);
	if (!name_ok(name)) { sb_cat(out, "{\"ok\":false,\"error\":\"name missing/invalid ([A-Za-z0-9._-], max 64)\"}"); return; }
	if (clen <= 0) { sb_cat(out, "{\"ok\":false,\"error\":\"Content-Length missing\"}"); return; }
	mkdir(UPLOAD_DIR, 0755);
	if (statvfs(UPLOAD_DIR, &vs) == 0 && (long long)vs.f_bavail * vs.f_frsize < clen + UPLOAD_RESERVE) {
		sb_cat(out, "{\"ok\":false,\"error\":\"not enough space in /tmp\",\"free\":%lld}", (long long)vs.f_bavail * vs.f_frsize); return;
	}
	snprintf(path, sizeof path, UPLOAD_DIR "/%s", name); snprintf(tmp, sizeof tmp, "%s.part", path);
	o = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0644);
	if (o < 0) { sb_cat(out, "{\"ok\":false,\"error\":\"open: %s\"}", strerror(errno)); return; }
	if (have_len > (size_t)clen) have_len = (size_t)clen;
	if (have_len && write(o, have, have_len) != (ssize_t)have_len) goto fail;
	done = have_len; left = clen - done;
	{ struct timeval tv = { 10, 0 }; setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv); }
	while (left > 0) {
		r = read(fd, buf, left < (long long)sizeof buf ? (size_t)left : sizeof buf);
		if (r <= 0) goto fail;
		if (write(o, buf, r) != r) goto fail;
		done += r; left -= r;
	}
	close(o);
	if (rename(tmp, path) < 0) { unlink(tmp); sb_cat(out, "{\"ok\":false,\"error\":\"rename\"}"); return; }
	sb_cat(out, "{\"ok\":true,\"path\":\"%s\",\"name\":\"%s\",\"bytes\":%lld}", path, name, done);
	return;
fail:
	close(o); unlink(tmp);
	sb_cat(out, "{\"ok\":false,\"error\":\"upload incomplete\",\"received\":%lld,\"expected\":%lld}", done, clen);
}
static void cmd_files(struct sb *out)
{
	DIR *d = opendir(UPLOAD_DIR); struct dirent *e; struct stat st; char p[400]; int first = 1; struct statvfs vs;
	sb_cat(out, "{\"ok\":true,\"dir\":\"" UPLOAD_DIR "\",\"files\":[");
	if (d) {
		while ((e = readdir(d))) {
			if (e->d_name[0] == '.') continue;
			snprintf(p, sizeof p, UPLOAD_DIR "/%s", e->d_name);
			if (stat(p, &st) == 0 && S_ISREG(st.st_mode)) { sb_cat(out, "%s{\"name\":", first ? "" : ","); sb_str(out, e->d_name); sb_cat(out, ",\"bytes\":%lld}", (long long)st.st_size); first = 0; }
		}
		closedir(d);
	}
	sb_cat(out, "]");
	if (statvfs("/tmp", &vs) == 0) sb_cat(out, ",\"tmp_free\":%lld", (long long)vs.f_bavail * vs.f_frsize);
	sb_cat(out, "}");
}
static void cmd_file_delete(const char *body, struct sb *out)
{
	char n[80], p[160];
	if (!jstr(body, "name", n, sizeof n) || !name_ok(n)) { sb_cat(out, "{\"ok\":false,\"error\":\"name invalid\"}"); return; }
	snprintf(p, sizeof p, UPLOAD_DIR "/%s", n);
	sb_cat(out, "{\"ok\":%s}", unlink(p) == 0 ? "true" : "false");
}

/* ----------------------------------------------------------------- HTTP */
static void http_send(int fd, int code, const char *ctype, const char *body, size_t len)
{
	char hdr[256]; int n = snprintf(hdr, sizeof hdr, "HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %zu\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n", code, code == 200 ? "OK" : code == 404 ? "Not Found" : "Bad Request", ctype, len);
	if (write(fd, hdr, n) < 0) return;
	if (len && write(fd, body, len) < 0) return;
}
static void handle(int fd)
{
	char req[65536]; ssize_t n, tot = 0; char method[8], path[256], query[256] = ""; char *body; long long clen = 0; struct sb out = { calloc(1, 4096), 0, 4096 };
	struct timeval tv = { 3, 0 }; setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
	for (;;) {
		n = read(fd, req + tot, sizeof req - 1 - tot); if (n <= 0) break; tot += n; req[tot] = 0;
		body = strstr(req, "\r\n\r\n");
		if (body) {
			char *cl = strcasestr(req, "Content-Length:"); if (cl) clen = atoll(cl + 15);
			if (tot - (body + 4 - req) >= clen) break;
			if (!strncmp(req, "POST /api/upload", 16)) break;	/* Body wird in cmd_upload gestreamt */
		}
		if (tot >= (ssize_t)sizeof req - 1) break;
	}
	if (tot <= 0 || sscanf(req, "%7s %255s", method, path) != 2) { close(fd); free(out.p); return; }
	body = strstr(req, "\r\n\r\n"); body = body ? body + 4 : (char *)"";
	{ char *q = strchr(path, '?'); if (q) { *q = 0; snprintf(query, sizeof query, "%s", q + 1); } }

	if (!strcmp(method, "OPTIONS")) { http_send(fd, 200, "text/plain", "", 0); }
	else if (!strcmp(path, "/api/status") || !strcmp(path, "/")) { status_json(&out); http_send(fd, 200, "application/json", out.p, out.len); }
	else if (!strcmp(path, "/api/mod/start") && !strcmp(method, "POST")) { cmd_mod_start(body, &out); http_send(fd, 200, "application/json", out.p, out.len); }
	else if (!strcmp(path, "/api/mod/stop")) { pthread_mutex_lock(&g_mod_lock); mod_stop(3000); pthread_mutex_unlock(&g_mod_lock); pthread_mutex_lock(&g_iio_lock); tx_mute(); pthread_mutex_unlock(&g_iio_lock); sb_cat(&out, "{\"ok\":true,\"exit\":%d}", g_mod_exit); http_send(fd, 200, "application/json", out.p, out.len); }
	else if (!strcmp(path, "/api/mod/log")) { cmd_log(&out, 50); http_send(fd, 200, "application/json", out.p, out.len); }
	else if (!strcmp(path, "/api/upload") && !strcmp(method, "POST")) {
		char *hb = strstr(req, "\r\n\r\n"); size_t have = hb ? (size_t)(tot - (hb + 4 - req)) : 0;
		cmd_upload(fd, query, hb ? hb + 4 : "", have, clen, &out); http_send(fd, 200, "application/json", out.p, out.len);
	}
	else if (!strcmp(path, "/api/files")) { cmd_files(&out); http_send(fd, 200, "application/json", out.p, out.len); }
	else if (!strcmp(path, "/api/files/delete") && !strcmp(method, "POST")) { cmd_file_delete(body, &out); http_send(fd, 200, "application/json", out.p, out.len); }
	else if (!strcmp(path, "/api/rate") && !strcmp(method, "POST")) { cmd_rate(body, &out); http_send(fd, 200, "application/json", out.p, out.len); }
	else if (!strcmp(path, "/api/rf") && !strcmp(method, "POST")) { cmd_rf(body, &out); http_send(fd, 200, "application/json", out.p, out.len); }
	else if (!strcmp(path, "/api/iiod") && !strcmp(method, "POST")) { cmd_iiod(body, &out); http_send(fd, 200, "application/json", out.p, out.len); }
	else if (!strcmp(path, "/api/underflow/clear")) { pthread_mutex_lock(&g_iio_lock); dac_unf_read_clear(); g_unf_count = 0; pthread_mutex_unlock(&g_iio_lock); http_send(fd, 200, "application/json", "{\"ok\":true}", 11); }
	else if (!strcmp(path, "/api/reboot") && !strcmp(method, "POST")) {
		char mode[16] = "reset"; char cmdl[64]; jstr(body, "mode", mode, sizeof mode);
		if (strcmp(mode, "ram") && strcmp(mode, "sf") && strcmp(mode, "reset") && strcmp(mode, "verbose")) strcpy(mode, "reset");
		http_send(fd, 200, "application/json", "{\"ok\":true}", 11); close(fd);
		snprintf(cmdl, sizeof cmdl, "/usr/sbin/device_reboot %s", mode); sync(); if (system(cmdl)) {} free(out.p); return;
	}
	else http_send(fd, 404, "application/json", "{\"ok\":false,\"error\":\"unknown endpoint\"}", 40);
	close(fd); free(out.p);
}

/* Hintergrund: CPU-Last, Underflow-Bit pollen, Kind einsammeln */
static void *monitor(void *arg)
{
	(void)arg;
	for (;;) {
		cpu_sample();
		pthread_mutex_lock(&g_iio_lock);
		if (g_ctx) { int u = dac_unf_read_clear(); if (u > 0 && g_mod_pid > 0 && now_s() - g_mod_started > 1.0) g_unf_count++; g_unf_last = u; }	/* nur zählen, solange ein Modulator läuft (im Leerlauf ist das Bit immer gesetzt) */
		pthread_mutex_unlock(&g_iio_lock);
		pthread_mutex_lock(&g_mod_lock); mod_reap(); pthread_mutex_unlock(&g_mod_lock);
		usleep(200000);
	}
	return NULL;
}
static void on_sig(int s) { (void)s; g_quit = 1; }

int main(int argc, char **argv)
{
	int lfd, one = 1, opt; struct sockaddr_in a; pthread_t th;
	while ((opt = getopt(argc, argv, "p:")) != -1) if (opt == 'p') g_port = atoi(optarg);
	signal(SIGPIPE, SIG_IGN);
	{	/* ohne SA_RESTART, damit accept() bei SIGTERM/SIGINT mit EINTR zurückkehrt */
		struct sigaction sa; memset(&sa, 0, sizeof sa); sa.sa_handler = on_sig; sigemptyset(&sa.sa_mask);
		sigaction(SIGTERM, &sa, NULL); sigaction(SIGINT, &sa, NULL);
	}
	if (iio_open() < 0) fprintf(stderr, "dablab-agent: IIO lokal nicht verfügbar (%s)\n", strerror(errno));
	else { pthread_mutex_lock(&g_iio_lock); tx_mute(); pthread_mutex_unlock(&g_iio_lock); }
	pthread_create(&th, NULL, monitor, NULL);
	lfd = socket(AF_INET, SOCK_STREAM, 0);
	setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
	memset(&a, 0, sizeof a); a.sin_family = AF_INET; a.sin_port = htons(g_port); a.sin_addr.s_addr = INADDR_ANY;
	if (bind(lfd, (struct sockaddr *)&a, sizeof a) < 0 || listen(lfd, 8) < 0) { perror("bind"); return 1; }
	printf("dablab-agent %s auf Port %d\n", AGENT_VERSION, g_port); fflush(stdout);
	while (!g_quit) {
		int fd = accept(lfd, NULL, NULL);
		if (fd < 0) { if (errno == EINTR) continue; break; }	/* g_quit wird in der Schleifenbedingung geprüft */
		setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
		handle(fd);
	}
	pthread_mutex_lock(&g_mod_lock); mod_stop(2000); pthread_mutex_unlock(&g_mod_lock);
	if (g_ctx) { tx_mute(); iio_context_destroy(g_ctx); }
	return 0;
}
