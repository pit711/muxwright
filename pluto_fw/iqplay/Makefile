CC ?= arm-linux-gnueabihf-gcc
CFLAGS ?= -O2 -mfpu=neon -mfloat-abi=hard
CFLAGS += -Wall -Wno-unused-result -std=gnu99
LDLIBS += -liio -lad9361 -lm
PREFIX ?= /usr

all: iqplay

iqplay: iqplay.c
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $< $(LDLIBS)

install: iqplay
	install -D -m 0755 iqplay $(DESTDIR)$(PREFIX)/bin/iqplay

clean:
	rm -f iqplay
