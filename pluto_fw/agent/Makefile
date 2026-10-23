CC ?= arm-linux-gnueabihf-gcc
CFLAGS ?= -O2 -mfpu=neon -mfloat-abi=hard
CFLAGS += -Wall -Wextra -Wno-unused-parameter -Wno-misleading-indentation -Wno-unused-result -std=gnu99 -pthread
LDLIBS += -liio -lad9361 -lpthread -lm
PREFIX ?= /usr

all: dablab-agent

dablab-agent: dablab-agent.c
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $< $(LDLIBS)

install: dablab-agent
	install -D -m 0755 dablab-agent $(DESTDIR)$(PREFIX)/bin/dablab-agent

clean:
	rm -f dablab-agent
