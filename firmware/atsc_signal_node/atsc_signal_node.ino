/*
  ATSC signal node - Arduino Uno firmware for the 2x2 traffic-signal model
  =======================================================================

  The PC runs the trained reinforcement-learning controller and the traffic simulation
  (python run.py demo --hardware). This board is its hands and eyes:

    * 16 signal heads (4 junctions x 4 approaches, red/amber/green each) through a chain
      of six 74HC595 shift registers - they show exactly what the AI grid on the dashboard
      shows, amber and all-red included;
    * 8 IR sensors at junction J0_0 - a toy car passing an arrival sensor adds a vehicle to
      the simulation, a car parked on a queue sensor adds a waiting queue;
    * a 433 MHz 4-button remote - A ambulance, B police car, C fire engine, D pause/resume;
    * a 0.96" I2C OLED (optional) and a buzzer (optional).

  If the PC stops talking for 3 seconds (cable pulled, program closed), every head goes to
  FLASHING AMBER, like a real signal whose controller has failed, until frames arrive again.

  Upload: Arduino IDE -> File > Open this file -> Tools > Board: "Arduino Uno" ->
  Tools > Port: your board -> Upload. No extra libraries are needed (Wire is built in).
  Full wiring and the step-by-step build: docs/HARDWARE.md

  Wiring summary (Arduino Uno)
    D10 -> 74HC595 #1 pin 12 (ST_CP, latch)      all 6 chips share latch and clock
    D11 -> 74HC595 #1 pin 14 (DS, data)          chip n pin 9 (Q7') -> chip n+1 pin 14
    D13 -> 74HC595 #1 pin 11 (SH_CP, clock)      pin 10 (MR) -> 5V, pin 13 (OE) -> GND
    D2..D5  arrival IR sensors at J0_0: N, E, S, W   (sensor OUT pin; LOW = car seen)
    D6..D9  queue IR sensors at J0_0:   N, E, S, W
    A0..A3  433 MHz receiver outputs D0..D3 (buttons A..D, set to momentary mode)
    D12     buzzer (+), A4 = OLED SDA, A5 = OLED SCL
  Which LED goes where: LED number n = head * 3 + colour (colour 0 red, 1 amber, 2 green;
  head = junction * 4 + side, side 0 N, 1 E, 2 S, 3 W) is output Q(n mod 8) of chip
  (n div 8) + 1, where chip 1 is the one wired to D11. The full table is in docs/HARDWARE.md
  and "python run.py hwtest" lights every head in turn so you can check your wiring.
*/

#include <Arduino.h>
#include <avr/pgmspace.h>
#include "atsc_core.h"

// =====================================================================================
// SETTINGS - change these to match what you built
// =====================================================================================
#define NODE_VERSION      "1.2.0"
#define NODE_TLS          "J0_0"   // junction with the IR sensors (as named on the dashboard)
#define NUM_HEADS         16       // signal heads on the shift-register chain: 4, 8, 12 or 16
                                   // (heads are numbered J0_0 N,E,S,W then J0_1 ... J1_1)
#define ENABLE_SENSORS    1        // 0 = no IR sensors connected yet
#define ENABLE_RF         1        // 0 = no 433 MHz receiver connected yet (A0-A3 unused)
#define ENABLE_OLED       1        // 0 = no OLED display
#define OLED_SH1106       0        // 1 = 1.3" SH1106 OLED (most 0.96" modules are SSD1306)
#define ENABLE_BUZZER     1        // 0 = no buzzer
#define BUZZER_ACTIVE_LOW 0        // 1 = "low level trigger" buzzer module
#define IR_ACTIVE_LOW     1        // FC-51 / TCRT5000 modules pull OUT low when they see a car
#define BEEP_ON_CAR       0        // 1 = short chirp for every car the arrival sensors count
#define QUEUE_LEVEL       3        // how many waiting vehicles a blocked queue sensor means

static const uint16_t LINK_TIMEOUT_MS = 3000;  // no frame from the PC -> flashing amber
static const uint16_t HEARTBEAT_MS = 1000;     // "<H" liveness message to the PC
static const uint16_t ARRIVE_HOLD_MS = 15;     // an arrival sensor must see the car this long
static const uint16_t ARRIVE_GAP_MS = 250;     // ...and counts at most one car per 250 ms
static const uint16_t QUEUE_HOLD_MS = 400;     // a queue sensor must stay blocked/clear this long
static const uint16_t QUEUE_REPEAT_MS = 5000;  // a blocked queue sensor is re-reported this often
static const uint16_t RF_HOLD_MS = 40;         // a remote button must be held this long
static const uint16_t RF_LOCKOUT_MS = 1500;    // one press -> one request, even if held

// =====================================================================================
// PINS (Arduino Uno)
// =====================================================================================
static const uint8_t PIN_LATCH = 10;
static const uint8_t PIN_DATA = 11;
static const uint8_t PIN_CLOCK = 13;
static const uint8_t PIN_ARRIVE[4] = {2, 3, 4, 5};   // N, E, S, W
static const uint8_t PIN_QUEUE[4] = {6, 7, 8, 9};    // N, E, S, W
static const uint8_t PIN_RF[4] = {A0, A1, A2, A3};   // receiver D0..D3 = buttons A..D
static const uint8_t PIN_BUZZER = 12;

#if NUM_HEADS < 1 || NUM_HEADS > 16
#error "NUM_HEADS must be between 1 and 16"
#endif
static const uint8_t N_CHIPS = (NUM_HEADS * 3 + 7) / 8;
// position of NODE_TLS ("J<row>_<col>") in the PC's junction order J0_0, J0_1, J1_0, J1_1
static const uint8_t NODE_JUNCTION = (uint8_t)((NODE_TLS[1] - '0') * 2 + (NODE_TLS[3] - '0'));

#if ENABLE_OLED
#include <Wire.h>
#endif

// =====================================================================================
// State
// =====================================================================================
static atsc::LineReader reader;
static atsc::LinkWatch pc_link;
static atsc::LampImage lamps_pc;        // the picture the PC asked for
static atsc::LampImage lamps_shown;     // what the shift registers hold right now
static char pc_aspects[atsc::N_ASPECTS + 1] = "RRRRRRRR";
static uint32_t last_shift_ms = 0;
static uint32_t last_heartbeat_ms = 0;
static uint16_t cars_counted = 0;

// =====================================================================================
// Sending to the PC
// =====================================================================================
static void send_payload(const char* payload) {
  char line[atsc::UPLINK_MAX];
  uint8_t n = atsc::frame_uplink(payload, line);
  Serial.write((const uint8_t*)line, n);
}

static void send_info() {
  char payload[40];
  char* p = atsc::append_str(payload, "I,atsc-node " NODE_VERSION " " NODE_TLS " heads=");
  atsc::append_uint(p, NUM_HEADS);
  send_payload(payload);
}

// =====================================================================================
// Lamps
// =====================================================================================
static void shift_out_image(const atsc::LampImage& img) {
  digitalWrite(PIN_LATCH, LOW);
  // the byte for the LAST chip goes out first: it is pushed through the whole chain
  for (int8_t chip = N_CHIPS - 1; chip >= 0; --chip) {
    shiftOut(PIN_DATA, PIN_CLOCK, MSBFIRST, img.bytes[chip]);
  }
  digitalWrite(PIN_LATCH, HIGH);   // rising edge copies the shifted bits to the outputs
}

// Shows img; rewrites the chain at least once a second anyway, so a register disturbed by
// electrical noise on a breadboard corrects itself.
static void show_lamps(const atsc::LampImage& img, uint32_t now) {
  if (!img.equals(lamps_shown) || (uint32_t)(now - last_shift_ms) >= 1000) {
    shift_out_image(img);
    lamps_shown = img;
    last_shift_ms = now;
  }
}

static void lamp_test_step(uint8_t color, uint16_t ms) {
  atsc::LampImage img;
  img.fill(NUM_HEADS, color);
  shift_out_image(img);
  delay(ms);
}

// =====================================================================================
// Buzzer: a few beeps, or a siren for an emergency request
// =====================================================================================
#if ENABLE_BUZZER
static uint8_t buzz_edges = 0;      // on/off changes still to make
static uint16_t buzz_on_ms = 0, buzz_off_ms = 0;
static uint32_t buzz_next_ms = 0;
static bool buzz_level = false;

static void buzzer_write(bool on) {
  buzz_level = on;
  digitalWrite(PIN_BUZZER, (on != (BUZZER_ACTIVE_LOW != 0)) ? HIGH : LOW);
}
static void buzzer_pattern(uint8_t pulses, uint16_t on_ms, uint16_t off_ms) {
  buzz_edges = (uint8_t)(pulses * 2);
  buzz_on_ms = on_ms;
  buzz_off_ms = off_ms;
  buzz_next_ms = millis();
  buzzer_write(false);
}
static void buzzer_update(uint32_t now) {
  if (!buzz_edges || (int32_t)(now - buzz_next_ms) < 0) return;
  buzzer_write(!buzz_level);
  buzz_edges--;
  buzz_next_ms = now + (buzz_level ? buzz_on_ms : buzz_off_ms);
  if (!buzz_edges) buzzer_write(false);
}
#else
static void buzzer_pattern(uint8_t, uint16_t, uint16_t) {}
static void buzzer_update(uint32_t) {}
#endif

// =====================================================================================
// OLED: a tiny text-only driver (SSD1306 / SH1106, 128x64, I2C) - 21 columns x 8 rows.
// It keeps no frame buffer (the Uno has 2 KB of RAM) and refreshes a few characters per
// loop, so it never holds up the serial port or the sensors.
// =====================================================================================
static char screen[8][atsc::TEXT_COLS];   // the text that should be on the display
static uint8_t dirty_lo[8], dirty_hi[8];  // columns still to redraw (lo > hi: row is clean)
static uint8_t refresh_row = 0;

static void display_row(uint8_t row, const char* text) {
  bool ended = false;
  for (uint8_t col = 0; col < atsc::TEXT_COLS; ++col) {
    char c = ' ';
    if (!ended) {
      if (text[col] == 0) ended = true;
      else c = text[col];
    }
    if (screen[row][col] != c) {
      screen[row][col] = c;
      if (col < dirty_lo[row]) dirty_lo[row] = col;
      if (col > dirty_hi[row]) dirty_hi[row] = col;
    }
  }
}

#if ENABLE_OLED
// Classic 5x7 font, ASCII 32..126, 5 columns per character, bit 0 = top row.
static const uint8_t FONT5X7[] PROGMEM = {
  0x00,0x00,0x00,0x00,0x00, 0x00,0x00,0x5F,0x00,0x00, 0x00,0x07,0x00,0x07,0x00, 0x14,0x7F,0x14,0x7F,0x14, //  !"#
  0x24,0x2A,0x7F,0x2A,0x12, 0x23,0x13,0x08,0x64,0x62, 0x36,0x49,0x55,0x22,0x50, 0x00,0x05,0x03,0x00,0x00, // $%&'
  0x00,0x1C,0x22,0x41,0x00, 0x00,0x41,0x22,0x1C,0x00, 0x14,0x08,0x3E,0x08,0x14, 0x08,0x08,0x3E,0x08,0x08, // ()*+
  0x00,0x50,0x30,0x00,0x00, 0x08,0x08,0x08,0x08,0x08, 0x00,0x60,0x60,0x00,0x00, 0x20,0x10,0x08,0x04,0x02, // ,-./
  0x3E,0x51,0x49,0x45,0x3E, 0x00,0x42,0x7F,0x40,0x00, 0x42,0x61,0x51,0x49,0x46, 0x21,0x41,0x45,0x4B,0x31, // 0123
  0x18,0x14,0x12,0x7F,0x10, 0x27,0x45,0x45,0x45,0x39, 0x3C,0x4A,0x49,0x49,0x30, 0x01,0x71,0x09,0x05,0x03, // 4567
  0x36,0x49,0x49,0x49,0x36, 0x06,0x49,0x49,0x29,0x1E, 0x00,0x36,0x36,0x00,0x00, 0x00,0x56,0x36,0x00,0x00, // 89:;
  0x08,0x14,0x22,0x41,0x00, 0x14,0x14,0x14,0x14,0x14, 0x00,0x41,0x22,0x14,0x08, 0x02,0x01,0x51,0x09,0x06, // <=>?
  0x32,0x49,0x79,0x41,0x3E, 0x7E,0x11,0x11,0x11,0x7E, 0x7F,0x49,0x49,0x49,0x36, 0x3E,0x41,0x41,0x41,0x22, // @ABC
  0x7F,0x41,0x41,0x22,0x1C, 0x7F,0x49,0x49,0x49,0x41, 0x7F,0x09,0x09,0x09,0x01, 0x3E,0x41,0x49,0x49,0x7A, // DEFG
  0x7F,0x08,0x08,0x08,0x7F, 0x00,0x41,0x7F,0x41,0x00, 0x20,0x40,0x41,0x3F,0x01, 0x7F,0x08,0x14,0x22,0x41, // HIJK
  0x7F,0x40,0x40,0x40,0x40, 0x7F,0x02,0x0C,0x02,0x7F, 0x7F,0x04,0x08,0x10,0x7F, 0x3E,0x41,0x41,0x41,0x3E, // LMNO
  0x7F,0x09,0x09,0x09,0x06, 0x3E,0x41,0x51,0x21,0x5E, 0x7F,0x09,0x19,0x29,0x46, 0x46,0x49,0x49,0x49,0x31, // PQRS
  0x01,0x01,0x7F,0x01,0x01, 0x3F,0x40,0x40,0x40,0x3F, 0x1F,0x20,0x40,0x20,0x1F, 0x3F,0x40,0x38,0x40,0x3F, // TUVW
  0x63,0x14,0x08,0x14,0x63, 0x07,0x08,0x70,0x08,0x07, 0x61,0x51,0x49,0x45,0x43, 0x00,0x7F,0x41,0x41,0x00, // XYZ[
  0x02,0x04,0x08,0x10,0x20, 0x00,0x41,0x41,0x7F,0x00, 0x04,0x02,0x01,0x02,0x04, 0x40,0x40,0x40,0x40,0x40, // \]^_
  0x00,0x01,0x02,0x04,0x00, 0x20,0x54,0x54,0x54,0x78, 0x7F,0x48,0x44,0x44,0x38, 0x38,0x44,0x44,0x44,0x20, // `abc
  0x38,0x44,0x44,0x48,0x7F, 0x38,0x54,0x54,0x54,0x18, 0x08,0x7E,0x09,0x01,0x02, 0x0C,0x52,0x52,0x52,0x3E, // defg
  0x7F,0x08,0x04,0x04,0x78, 0x00,0x44,0x7D,0x40,0x00, 0x20,0x40,0x44,0x3D,0x00, 0x7F,0x10,0x28,0x44,0x00, // hijk
  0x00,0x41,0x7F,0x40,0x00, 0x7C,0x04,0x18,0x04,0x78, 0x7C,0x08,0x04,0x04,0x78, 0x38,0x44,0x44,0x44,0x38, // lmno
  0x7C,0x14,0x14,0x14,0x08, 0x08,0x14,0x14,0x18,0x7C, 0x7C,0x08,0x04,0x04,0x08, 0x48,0x54,0x54,0x54,0x20, // pqrs
  0x04,0x3F,0x44,0x40,0x20, 0x3C,0x40,0x40,0x20,0x7C, 0x1C,0x20,0x40,0x20,0x1C, 0x3C,0x40,0x30,0x40,0x3C, // tuvw
  0x44,0x28,0x10,0x28,0x44, 0x0C,0x50,0x50,0x50,0x3C, 0x44,0x64,0x54,0x4C,0x44, 0x00,0x08,0x36,0x41,0x00, // xyz{
  0x00,0x00,0x7F,0x00,0x00, 0x00,0x41,0x36,0x08,0x00, 0x08,0x04,0x08,0x10,0x08,                            // |}~
};

static const uint8_t OLED_INIT[] PROGMEM = {
  0xAE,        // display off
  0xD5, 0x80,  // clock
  0xA8, 0x3F,  // 64 rows
  0xD3, 0x00,  // no vertical offset
  0x40,        // start line 0
  0x8D, 0x14,  // charge pump on (SSD1306)
  0x20, 0x02,  // page addressing
  0xA1,        // mirror columns  } text upright with the
  0xC8,        // mirror rows     } pin header at the top
  0xDA, 0x12,  // COM pins for 128x64
  0x81, 0xCF,  // contrast
  0xD9, 0xF1,  // pre-charge
  0xDB, 0x40,  // VCOMH
  0xA4,        // show RAM
  0xA6,        // not inverted
  0xAF,        // display on
};

static const uint8_t OLED_COL_OFFSET = OLED_SH1106 ? 2 : 0;
static uint8_t oled_addr = 0;     // 0 = no display found

static void oled_cursor(uint8_t page, uint8_t col) {
  col += OLED_COL_OFFSET;
  Wire.beginTransmission(oled_addr);
  Wire.write((uint8_t)0x00);                     // a command stream follows
  Wire.write((uint8_t)(0xB0 | page));
  Wire.write((uint8_t)(0x00 | (col & 0x0F)));
  Wire.write((uint8_t)(0x10 | (col >> 4)));
  Wire.endTransmission();
}

static void oled_chars(uint8_t row, uint8_t col, const char* s, uint8_t n) {
  oled_cursor(row, (uint8_t)(col * 6));
  Wire.beginTransmission(oled_addr);
  Wire.write((uint8_t)0x40);                     // a data stream follows
  for (uint8_t i = 0; i < n; ++i) {
    uint8_t c = (uint8_t)s[i];
    if (c < 32 || c > 126) c = '?';
    const uint8_t* glyph = FONT5X7 + (c - 32) * 5;
    for (uint8_t k = 0; k < 5; ++k) Wire.write(pgm_read_byte(glyph + k));
    Wire.write((uint8_t)0x00);                   // 1-pixel gap between characters
  }
  Wire.endTransmission();
}

static void oled_begin() {
  Wire.begin();
  Wire.setClock(400000);
#if defined(WIRE_HAS_TIMEOUT)
  Wire.setWireTimeout(25000, true);              // a mis-wired bus must not hang the board
#endif
  const uint8_t candidates[2] = {0x3C, 0x3D};
  for (uint8_t i = 0; i < 2 && !oled_addr; ++i) {
    Wire.beginTransmission(candidates[i]);
    if (Wire.endTransmission() == 0) oled_addr = candidates[i];
  }
  if (!oled_addr) return;
  Wire.beginTransmission(oled_addr);
  Wire.write((uint8_t)0x00);
  for (uint8_t i = 0; i < sizeof(OLED_INIT); ++i) Wire.write(pgm_read_byte(OLED_INIT + i));
  Wire.endTransmission();
  for (uint8_t page = 0; page < 8; ++page) {     // clear the whole display
    oled_cursor(page, 0);
    for (uint8_t chunk = 0; chunk < 8; ++chunk) {
      Wire.beginTransmission(oled_addr);
      Wire.write((uint8_t)0x40);
      for (uint8_t k = 0; k < 16; ++k) Wire.write((uint8_t)0x00);
      Wire.endTransmission();
    }
  }
}

// Redraws at most 4 changed characters (about 0.7 ms of I2C at 400 kHz).
static void display_refresh() {
  if (!oled_addr) return;
  for (uint8_t i = 0; i < 8; ++i) {
    uint8_t row = (uint8_t)((refresh_row + i) & 7);
    if (dirty_lo[row] > dirty_hi[row]) continue;
    uint8_t col = dirty_lo[row];
    uint8_t n = (uint8_t)(dirty_hi[row] - col + 1);
    if (n > 4) n = 4;
    oled_chars(row, col, &screen[row][col], n);
    dirty_lo[row] = (uint8_t)(col + n);
    if (dirty_lo[row] > dirty_hi[row]) { dirty_lo[row] = 0xFF; dirty_hi[row] = 0; }
    refresh_row = row;
    return;
  }
}
#else
static void oled_begin() {}
static void display_refresh() {}
#endif

static bool display_dirty() {
  for (uint8_t r = 0; r < 8; ++r) {
    if (dirty_lo[r] <= dirty_hi[r]) return true;
  }
  return false;
}

// Draws everything pending (only used in setup, where blocking is fine).
static void display_flush() {
#if ENABLE_OLED
  while (oled_addr && display_dirty()) display_refresh();
#endif
}

static void display_init() {
  memset(screen, ' ', sizeof(screen));
  for (uint8_t r = 0; r < 8; ++r) { dirty_lo[r] = 0xFF; dirty_hi[r] = 0; }
  oled_begin();
}

// ----- what the board itself shows on rows 0-3 (rows 4-7 belong to the PC) -----------
static void show_title() { display_row(0, "ATSC node " NODE_VERSION " " NODE_TLS); }

static void show_link_status() {
  if (pc_link.state == atsc::LINK_LIVE) display_row(1, "PC: live");
  else if (pc_link.state == atsc::LINK_LOST) display_row(1, "PC: LOST - flashing");
  else display_row(1, "PC: waiting...");
}

static const char* aspect_word(char a) {
  return a == 'G' ? "GREEN" : a == 'A' ? "AMBER" : "RED";
}

static void show_local_junction() {
  if (!pc_link.live()) { display_row(2, "all heads: AMBER"); return; }
  char line[atsc::TEXT_COLS + 1];
  char* p = atsc::append_str(line, "NS:");
  p = atsc::append_str(p, aspect_word(pc_aspects[NODE_JUNCTION * 2]));
  p = atsc::append_str(p, "  EW:");
  atsc::append_str(p, aspect_word(pc_aspects[NODE_JUNCTION * 2 + 1]));
  display_row(2, line);
}

static void show_event(const char* what) { display_row(3, what); }

static void clear_pc_rows(const char* first) {
  display_row(4, first);
  for (uint8_t r = 5; r < 8; ++r) display_row(r, "");
}

// =====================================================================================
// Frames from the PC
// =====================================================================================
static void handle_line(uint32_t now) {
  atsc::DownFrame f;
  uint8_t kind = atsc::parse_downlink(reader.buf, reader.len, &f);
  if (kind == atsc::FRAME_LAMPS) {
    if (!atsc::image_from_aspects(f.aspects, NUM_HEADS, &lamps_pc)) return;
    memcpy(pc_aspects, f.aspects, sizeof(pc_aspects));
    if (pc_link.lamp_frame(now)) {          // the PC has (re)appeared
      send_info();
      show_link_status();
      clear_pc_rows("");
    }
    show_local_junction();
  } else if (kind == atsc::FRAME_TEXT) {
    pc_link.other_frame(now);
    display_row((uint8_t)(4 + f.row), f.text);
  }
}

static void poll_serial(uint32_t now) {
  uint8_t budget = 64;                   // never stay here for more than one buffer's worth
  while (Serial.available() > 0 && budget--) {
    if (reader.feed((char)Serial.read())) {
      handle_line(now);
      reader.reset();
    }
  }
}

static void update_lamps(uint32_t now) {
  if (pc_link.poll(now)) {                  // the PC went quiet: failsafe
    show_link_status();
    show_local_junction();
    clear_pc_rows("-- no data from PC --");
    buzzer_pattern(2, 80, 120);
  }
  if (pc_link.live()) {
    show_lamps(lamps_pc, now);
  } else {                               // flashing amber, 1 Hz
    atsc::LampImage img;
    if ((now / 500) % 2 == 0) img.fill(NUM_HEADS, atsc::AMBER);
    else img.clear();
    show_lamps(img, now);
  }
}

// =====================================================================================
// IR sensors at the instrumented junction
// =====================================================================================
#if ENABLE_SENSORS
static atsc::Debouncer arrive_db[4], queue_db[4];
static uint32_t arrive_last_ms[4];
static uint32_t queue_sent_ms[4];

static uint8_t ir_active(uint8_t pin) {
  uint8_t level = (uint8_t)digitalRead(pin);
  return IR_ACTIVE_LOW ? (level == LOW) : (level == HIGH);
}

static void send_queue(uint8_t side, uint8_t n, uint32_t now) {
  char payload[24];
  char* p = atsc::append_str(payload, "Q," NODE_TLS ",");
  *p++ = atsc::SIDE_NAMES[side];
  *p++ = ',';
  atsc::append_uint(p, n);
  send_payload(payload);
  queue_sent_ms[side] = now;
}

static void sensors_begin(uint32_t now) {
  for (uint8_t s = 0; s < 4; ++s) {
    pinMode(PIN_ARRIVE[s], INPUT_PULLUP);
    pinMode(PIN_QUEUE[s], INPUT_PULLUP);
    arrive_db[s].begin(ir_active(PIN_ARRIVE[s]), now);   // a car already there is not counted
    queue_db[s].begin(0, now);                            // a blocked queue sensor is reported
    arrive_last_ms[s] = now - ARRIVE_GAP_MS;
  }
}

static void poll_sensors(uint32_t now) {
  for (uint8_t s = 0; s < 4; ++s) {
    if (arrive_db[s].update(ir_active(PIN_ARRIVE[s]), now, ARRIVE_HOLD_MS) &&
        arrive_db[s].stable && (uint32_t)(now - arrive_last_ms[s]) >= ARRIVE_GAP_MS) {
      arrive_last_ms[s] = now;
      char payload[16];
      char* p = atsc::append_str(payload, "D," NODE_TLS ",");
      *p++ = atsc::SIDE_NAMES[s];
      *p = 0;
      send_payload(payload);
      cars_counted++;
      char line[atsc::TEXT_COLS + 1];
      p = atsc::append_str(line, "car ");
      *p++ = atsc::SIDE_NAMES[s];
      p = atsc::append_str(p, "    counted ");
      atsc::append_uint(p, cars_counted);
      show_event(line);
      if (BEEP_ON_CAR) buzzer_pattern(1, 25, 0);
    }
    if (queue_db[s].update(ir_active(PIN_QUEUE[s]), now, QUEUE_HOLD_MS)) {
      send_queue(s, queue_db[s].stable ? QUEUE_LEVEL : 0, now);
      char line[atsc::TEXT_COLS + 1];
      char* p = atsc::append_str(line, "queue ");
      *p++ = atsc::SIDE_NAMES[s];
      atsc::append_str(p, queue_db[s].stable ? ": waiting" : ": clear");
      show_event(line);
    } else if (queue_db[s].stable && (uint32_t)(now - queue_sent_ms[s]) >= QUEUE_REPEAT_MS) {
      send_queue(s, QUEUE_LEVEL, now);   // still blocked: keep the demand alive
    }
  }
}
#else
static void sensors_begin(uint32_t) {}
static void poll_sensors(uint32_t) {}
#endif

// =====================================================================================
// 433 MHz remote: A ambulance (east-west), B police car (north-south), C fire engine
// (east-west), D pause/resume the simulation
// =====================================================================================
#if ENABLE_RF
static atsc::Debouncer rf_db[4];
static uint32_t rf_last_ms[4];
static const char* const RF_PAYLOAD[4] = {"E,ew,ambulance", "E,ns,police", "E,ew,fire", "C,toggle"};
static const char* const RF_LABEL[4] = {"A: AMBULANCE", "B: POLICE CAR", "C: FIRE ENGINE",
                                        "D: pause/resume"};

static void rf_begin(uint32_t now) {
  for (uint8_t b = 0; b < 4; ++b) {
    pinMode(PIN_RF[b], INPUT);           // the receiver drives these pins
    rf_db[b].begin((uint8_t)digitalRead(PIN_RF[b]), now);
    rf_last_ms[b] = now - RF_LOCKOUT_MS;
  }
}

static void poll_rf(uint32_t now) {
  for (uint8_t b = 0; b < 4; ++b) {
    if (!rf_db[b].update((uint8_t)digitalRead(PIN_RF[b]), now, RF_HOLD_MS)) continue;
    if (!rf_db[b].stable) continue;                      // act on the press, not the release
    if ((uint32_t)(now - rf_last_ms[b]) < RF_LOCKOUT_MS) continue;
    rf_last_ms[b] = now;
    send_payload(RF_PAYLOAD[b]);
    show_event(RF_LABEL[b]);
    if (b < 3) buzzer_pattern(6, 150, 100);              // siren for an emergency vehicle
    else buzzer_pattern(1, 60, 0);
  }
}
#else
static void rf_begin(uint32_t) {}
static void poll_rf(uint32_t) {}
#endif

// =====================================================================================
// setup / loop
// =====================================================================================
void setup() {
  pinMode(PIN_LATCH, OUTPUT);
  pinMode(PIN_DATA, OUTPUT);
  pinMode(PIN_CLOCK, OUTPUT);
  atsc::LampImage dark;
  dark.clear();
  shift_out_image(dark);                 // 74HC595s power up holding random bits
#if ENABLE_BUZZER
  pinMode(PIN_BUZZER, OUTPUT);
  buzzer_write(false);
#endif
  Serial.begin(115200);
  reader.reset();
  pc_link.begin(LINK_TIMEOUT_MS);

  display_init();
  show_title();
  display_row(1, "lamp test...");
  display_flush();

  lamp_test_step(atsc::RED, 500);        // every head red, then amber, then green
  lamp_test_step(atsc::AMBER, 500);
  lamp_test_step(atsc::GREEN, 500);
  lamp_test_step(atsc::RED, 200);
  lamps_shown.clear();
  last_shift_ms = 0;

  uint32_t now = millis();
  sensors_begin(now);
  rf_begin(now);
  show_link_status();
  show_local_junction();
  show_event("ready");
  send_info();
  buzzer_pattern(1, 60, 0);
  last_heartbeat_ms = now;
}

void loop() {
  uint32_t now = millis();
  poll_serial(now);
  update_lamps(now);
  poll_sensors(now);
  poll_rf(now);
  if ((uint32_t)(now - last_heartbeat_ms) >= HEARTBEAT_MS) {
    last_heartbeat_ms = now;
    char payload[16];
    atsc::append_uint(atsc::append_str(payload, "H,"), now);
    send_payload(payload);
  }
  buzzer_update(now);
  display_refresh();
}
