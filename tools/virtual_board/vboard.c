/*
 * vboard - the signal node's firmware running on a simulated Arduino Uno.
 *
 * Runs the compiled firmware (atsc_signal_node.elf) on simavr's ATmega328P at 16 MHz, in
 * real time, wired to simulated parts:
 *
 *   USB serial   UART0 <-> a pseudo-terminal, printed as "READY pty=/dev/pts/N". The PC
 *                program opens it exactly like the real board's COM port.
 *   74HC595 x6   a 48-bit shift/latch model on D11 (data), D13 (clock), D10 (latch);
 *                every change of the latched outputs is printed as "LAMPS <ms> <hex>"
 *                (bit n = LED n, see firmware/atsc_signal_node/atsc_core.h)
 *   buzzer       D12, printed as "BUZZ <ms> <0|1>"
 *   OLED         simavr's SSD1306 model on I2C address 0x3C (omit with --no-oled)
 *   sensors      inputs driven from stdin: "pin D2 0" puts a car in front of the north
 *                arrival sensor (IR outputs are active LOW), "pin C0 1" holds remote
 *                button A (receiver outputs are active HIGH)
 *
 * Other stdin commands: "oled" dumps the display RAM ("OLED <page> <hex>" x 8, then
 * "OLEDEND"), "time" prints "TIME <ms>", "quit" exits. Times are simulated milliseconds
 * since reset; the simulation is paced to the wall clock so the firmware's timeouts behave
 * as on the bench.
 *
 * Build (Debian/Ubuntu: apt install simavr libsimavr-dev libelf-dev):
 *   cc -O2 -o vboard vboard.c -I/usr/include/simavr -I/usr/include/simavr/parts \
 *      -lsimavrparts -lsimavr -lelf -lpthread -lutil
 */
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "sim_avr.h"
#include "sim_elf.h"
#include "sim_io.h"
#include "avr_ioport.h"
#include "avr_twi.h"
#include "ssd1306_virt.h"
#include "uart_pty.h"

static avr_t *avr;
static uart_pty_t uart;
static ssd1306_t oled;

/* ---- 74HC595 chain: SER = PB3 (D11), SRCLK = PB5 (D13), RCLK = PB2 (D10) ---- */
static uint64_t shift_reg, latched, printed = ~0ULL;
static uint32_t data_level;
static const uint64_t CHAIN_MASK = (1ULL << 48) - 1;

static double now_ms(void) { return (double)avr->cycle / (avr->frequency / 1000.0); }

static void data_hook(avr_irq_t *irq, uint32_t value, void *param) {
    (void)irq; (void)param;
    data_level = value & 1;
}

static void clock_hook(avr_irq_t *irq, uint32_t value, void *param) {
    (void)param;
    if (!irq->value && value) /* rising edge: everything moves one stage along */
        shift_reg = ((shift_reg << 1) | data_level) & CHAIN_MASK;
}

static void latch_hook(avr_irq_t *irq, uint32_t value, void *param) {
    (void)param;
    if (!irq->value && value) { /* rising edge: shift register -> outputs */
        latched = shift_reg;
        if (latched != printed) {
            printed = latched;
            printf("LAMPS %.1f %012llx\n", now_ms(), (unsigned long long)latched);
        }
    }
}

static void buzzer_hook(avr_irq_t *irq, uint32_t value, void *param) {
    (void)param;
    if ((irq->value & 1) != (value & 1))
        printf("BUZZ %.1f %u\n", now_ms(), value & 1);
}

/* ---- inputs driven from outside, as a sensor's output transistor would ---- */
static uint8_t ext_mask[3], ext_value[3];   /* ports B, C, D */

static int port_index(char port) {
    return port == 'B' ? 0 : port == 'C' ? 1 : port == 'D' ? 2 : -1;
}

static void drive_pin(char port, int bit, int level) {
    int i = port_index(port);
    if (i < 0 || bit < 0 || bit > 7)
        return;
    ext_mask[i] |= (uint8_t)(1u << bit);
    if (level) ext_value[i] |= (uint8_t)(1u << bit);
    else ext_value[i] &= (uint8_t)~(1u << bit);
    avr_ioport_external_t ext = {.name = (unsigned)port, .mask = ext_mask[i], .value = ext_value[i]};
    avr_ioctl(avr, AVR_IOCTL_IOPORT_SET_EXTERNAL(port), &ext);
    avr_raise_irq(avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ(port), bit), level ? 1 : 0);
}

static void dump_oled(void) {
    for (int page = 0; page < 8; ++page) {
        printf("OLED %d ", page);
        for (int col = 0; col < 128; ++col)
            printf("%02x", oled.vram[page][col]);
        printf("\n");
    }
    printf("OLEDEND\n");
}

static int handle_command(char *line) {
    char port;
    int bit, level;
    if (sscanf(line, "pin %c%d %d", &port, &bit, &level) == 3) {
        drive_pin(port, bit, level);
    } else if (!strncmp(line, "oled", 4)) {
        dump_oled();
    } else if (!strncmp(line, "time", 4)) {
        printf("TIME %.1f\n", now_ms());
    } else if (!strncmp(line, "quit", 4)) {
        return 0;
    } else if (line[0] && line[0] != '\n') {
        printf("ERR unknown command: %s", line);
    }
    return 1;
}

static double wall_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

int main(int argc, char **argv) {
    const char *elf_path = NULL;
    int with_oled = 1;
    double speed = 1.0;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--no-oled")) with_oled = 0;
        else if (!strcmp(argv[i], "--speed") && i + 1 < argc) speed = atof(argv[++i]);
        else elf_path = argv[i];
    }
    if (!elf_path) {
        fprintf(stderr, "usage: vboard [--no-oled] [--speed X] atsc_signal_node.elf\n");
        return 2;
    }
    setvbuf(stdout, NULL, _IOLBF, 0);

    elf_firmware_t fw;
    memset(&fw, 0, sizeof(fw));
    if (elf_read_firmware(elf_path, &fw) != 0) {
        fprintf(stderr, "cannot read %s\n", elf_path);
        return 1;
    }
    strcpy(fw.mmcu, "atmega328p");
    fw.frequency = 16000000;
    avr = avr_make_mcu_by_name(fw.mmcu);
    if (!avr) return 1;
    avr_init(avr);
    avr_load_firmware(avr, &fw);
    avr->log = 0;

    /* idle inputs: IR sensors see nothing (HIGH), remote outputs low */
    const int ir_d[] = {2, 3, 4, 5, 6, 7};
    for (unsigned i = 0; i < sizeof(ir_d) / sizeof(ir_d[0]); ++i) drive_pin('D', ir_d[i], 1);
    drive_pin('B', 0, 1);
    drive_pin('B', 1, 1);
    for (int b = 0; b < 4; ++b) drive_pin('C', b, 0);

    avr_irq_register_notify(avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ('B'), 3), data_hook, NULL);
    avr_irq_register_notify(avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ('B'), 5), clock_hook, NULL);
    avr_irq_register_notify(avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ('B'), 2), latch_hook, NULL);
    avr_irq_register_notify(avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ('B'), 4), buzzer_hook, NULL);

    if (with_oled) {
        ssd1306_init(avr, &oled, 128, 64);
        ssd1306_wiring_t wiring = {.reset = {.port = 'B', .pin = 7}};   /* PB7: unused on a Uno */
        ssd1306_connect_twi(&oled, &wiring);
    }

    uart_pty_init(avr, &uart);
    uart_pty_connect(&uart, '0');
    printf("READY pty=%s\n", uart.pty.slavename);

    int flags = fcntl(0, F_GETFL, 0);
    fcntl(0, F_SETFL, flags | O_NONBLOCK);
    char cmd[256];
    size_t cmd_len = 0;
    int running = 1, stdin_open = 1;

    const avr_cycle_count_t slice = avr->frequency / 1000;      /* 1 ms of simulated time */
    avr_cycle_count_t next_check = slice;
    double wall0 = wall_ms();

    while (running) {
        int state = avr_run(avr);
        if (state == cpu_Done || state == cpu_Crashed) {
            printf("HALT %d\n", state);
            break;
        }
        if (avr->cycle < next_check)
            continue;
        next_check = avr->cycle + slice;

        /* keep simulated time in step with the wall clock */
        double ahead = now_ms() / speed - (wall_ms() - wall0);
        if (ahead > 2.0) {
            struct timespec ts = {0, (long)(ahead * 1e6)};
            nanosleep(&ts, NULL);
        }

        /* commands from stdin */
        while (stdin_open) {
            char ch;
            ssize_t n = read(0, &ch, 1);
            if (n == 1) {
                if (cmd_len < sizeof(cmd) - 2) cmd[cmd_len++] = ch;
                if (ch == '\n') {
                    cmd[cmd_len] = 0;
                    running = handle_command(cmd);
                    cmd_len = 0;
                    if (!running) break;
                }
            } else if (n == 0) {
                stdin_open = 0;          /* parent went away: stop */
                running = 0;
            } else {
                break;                   /* EAGAIN: nothing more for now */
            }
        }
    }
    uart_pty_stop(&uart);
    return 0;
}
