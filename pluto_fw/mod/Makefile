# libdvbmod_lab + dvbt_mod / dvbc_mod / rrc_fir_gen
# Überschreibbar: CC, CFLAGS, LDFLAGS, HAVE_LIBIIO=1 (libiio), STATIC=1
# Beispiele:
#   make                                   (x86, -O3)
#   make CC=arm-linux-gnueabihf-gcc STATIC=1 CFLAGS="-O3 -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard"
#   make HAVE_LIBIIO=1                     (Pluto-Buildroot: CC/CFLAGS aus der Umgebung)

CC      ?= gcc
CFLAGS  ?= -O3
CFLAGS  += -std=gnu11 -Wall -Wextra -Wno-unused-parameter -Wno-unused-function -D_GNU_SOURCE
LDFLAGS ?=
LDLIBS  += -lm -lpthread

HAVE_LIBIIO ?= 0
ifeq ($(HAVE_LIBIIO),1)
CFLAGS += -DHAVE_LIBIIO $(shell pkg-config --cflags libiio 2>/dev/null)
LDLIBS += $(shell pkg-config --libs libiio 2>/dev/null || echo -liio)
endif
ifeq ($(STATIC),1)
LDFLAGS += -static
endif

LIBSRC = lib/fec.c lib/dvbt_inner.c lib/dvbt_frame.c lib/fft16.c lib/dvbc.c lib/dvbs2.c lib/rrc.c lib/ts_input.c lib/output.c lib/util.c
LIBOBJ = $(LIBSRC:.c=.o)
LIB    = libdvbmod_lab.a

PROGS = dvbt_mod dvbc_mod dvbs_mod dvbs2_mod rrc_fir_gen dvbmod_selftest dvbs_rx dvbs2_stage

all: $(PROGS)

$(LIB): $(LIBOBJ)
	$(AR) rcs $@ $^

lib/dvbs2.o: lib/dvbs2.c lib/dvbs2.h lib/dvbs2_tables.h
	$(CC) $(CFLAGS) -c -o $@ $<

%.o: %.c lib/dvbmod.h
	$(CC) $(CFLAGS) -c -o $@ $<

dvbt_mod: dvbt_mod.o $(LIB)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)
dvbc_mod: dvbc_mod.o $(LIB)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)
dvbs_mod: dvbs_mod.o $(LIB)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)
dvbs_rx: tools/dvbs_rx.o $(LIB)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)
dvbs2_mod: dvbs2_mod.o $(LIB)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)
dvbs2_stage: tools/dvbs2_stage.o $(LIB)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)
rrc_fir_gen: rrc_fir_gen.o $(LIB)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)
dvbmod_selftest: tools/dvbmod_selftest.o $(LIB)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

clean:
	rm -f $(PROGS) $(LIBOBJ) $(LIB) *.o tools/*.o

.PHONY: all clean
