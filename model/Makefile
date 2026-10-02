CC ?= cc
CFLAGS ?= -O2 -g -std=c11 -Wall -Wextra -Wshadow
SRCS = chip.c board.c asm.c host.c proto.c peers.c peers_net.c test_main.c

.PHONY: test clean

pe_test: $(SRCS) pe.h host.h peers.h proto.h
	$(CC) $(CFLAGS) -o $@ $(SRCS) -lm

test: pe_test
	./pe_test programs

clean:
	rm -f pe_test
