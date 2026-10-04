// atsc_core.h - the signal node's logic, kept free of Arduino calls.
//
// Everything in this file is plain C++ (no pins, no Serial, no millis), so the same code
// is compiled into the Arduino sketch AND into a PC unit test (tests/firmware/), which
// checks it against the Python side of the protocol (src/atsc/hw/protocol.py).
//
// Wire protocol, one ASCII line per message, 115200 baud, '\n' terminated:
//
//   PC -> board   >L,<aspects>,<crc>        lamps: 2 letters per junction (NS then EW),
//                                            junctions in the order J0_0 J0_1 J1_0 J1_1,
//                                            each R (red), A (amber) or G (green)
//                 >T,<row>,<text>,<crc>     a line of text for the OLED (rows 0-3)
//
//   board -> PC   <D,<tls>,<approach>,<crc>       a vehicle passed an arrival sensor
//                 <Q,<tls>,<approach>,<n>,<crc>   a queue sensor: n vehicles waiting (0 = clear)
//                 <E,<corridor>,<vehicle>,<crc>   an emergency button was pressed
//                 <C,<action>,<crc>               a control button (action: toggle)
//                 <H,<uptime_ms>,<crc>            heartbeat, once a second
//                 <I,<text>,<crc>                 who am I (sent at boot / when the PC appears)
//
// <crc> is CRC-8 (polynomial 0x07, initial value 0x00) of everything between the first
// character and the last comma, written as two hex digits. A line with a wrong CRC is
// ignored, so noise on the wire can never switch a lamp.
#ifndef ATSC_CORE_H
#define ATSC_CORE_H

#include <stdint.h>
#include <string.h>

namespace atsc {

// --------------------------------------------------------------------------------------
// Geometry of the model: a 2x2 grid, junctions in the order the PC uses (topology order)
// --------------------------------------------------------------------------------------
static const uint8_t N_JUNCTIONS = 4;          // J0_0, J0_1, J1_0, J1_1
static const uint8_t N_PHASES = 2;             // phase 0 = north-south, phase 1 = east-west
static const uint8_t N_ASPECTS = N_JUNCTIONS * N_PHASES;   // letters in an L frame (8)
static const uint8_t HEADS_PER_JUNCTION = 4;   // one signal head per approach: N, E, S, W
static const uint8_t MAX_HEADS = N_JUNCTIONS * HEADS_PER_JUNCTION;   // 16
static const uint8_t LAMPS_PER_HEAD = 3;       // red, amber, green
static const uint8_t MAX_CHIPS = (MAX_HEADS * LAMPS_PER_HEAD + 7) / 8;   // 6 x 74HC595

static const uint8_t TEXT_ROWS = 4;            // OLED rows the PC may write (shown as rows 4-7)
static const uint8_t TEXT_COLS = 21;           // characters per OLED row (6-pixel cells)
static const uint8_t LINE_MAX = 40;            // longest line accepted from the PC

enum Color { RED = 0, AMBER = 1, GREEN = 2 };

// Sides of a junction, in the order the heads are wired: N, E, S, W.
static const char SIDE_NAMES[4] = {'N', 'E', 'S', 'W'};

// The north and south heads show the north-south aspect, east and west the east-west one.
inline uint8_t phase_of_side(uint8_t side) { return (side == 0 || side == 2) ? 0 : 1; }

// Number of 74HC595 chips needed for `heads` signal heads (3 LEDs each).
inline uint8_t chips_for_heads(uint8_t heads) { return (uint8_t)((heads * LAMPS_PER_HEAD + 7) / 8); }

// --------------------------------------------------------------------------------------
// CRC-8/ATM (poly 0x07, init 0x00) - identical to crc8() in src/atsc/hw/protocol.py
// --------------------------------------------------------------------------------------
inline uint8_t crc8(const char* data, uint8_t len) {
  uint8_t crc = 0;
  for (uint8_t i = 0; i < len; ++i) {
    crc ^= (uint8_t)data[i];
    for (uint8_t b = 0; b < 8; ++b) {
      crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
    }
  }
  return crc;
}

inline int8_t hex_value(char c) {
  if (c >= '0' && c <= '9') return (int8_t)(c - '0');
  if (c >= 'A' && c <= 'F') return (int8_t)(c - 'A' + 10);
  if (c >= 'a' && c <= 'f') return (int8_t)(c - 'a' + 10);
  return -1;
}

inline char hex_digit(uint8_t v) { return (char)(v < 10 ? '0' + v : 'A' + v - 10); }

// --------------------------------------------------------------------------------------
// Lamp image: one bit per LED, 8 LEDs per 74HC595.
//   LED number = head * 3 + color;   head = junction * 4 + side (N, E, S, W)
//   LED n is output Q(n % 8) of bytes[n / 8]; bytes[0] is the chip wired to the Arduino
//   (called "chip 1" in docs/HARDWARE.md).
// --------------------------------------------------------------------------------------
struct LampImage {
  uint8_t bytes[MAX_CHIPS];

  void clear() { memset(bytes, 0, sizeof(bytes)); }
  void set(uint8_t head, uint8_t color) {
    uint8_t n = (uint8_t)(head * LAMPS_PER_HEAD + color);
    bytes[n >> 3] |= (uint8_t)(1u << (n & 7));
  }
  bool lit(uint8_t head, uint8_t color) const {
    uint8_t n = (uint8_t)(head * LAMPS_PER_HEAD + color);
    return (bytes[n >> 3] >> (n & 7)) & 1u;
  }
  bool equals(const LampImage& o) const { return memcmp(bytes, o.bytes, sizeof(bytes)) == 0; }
  // every head shows `color` (lamp test, flashing amber)
  void fill(uint8_t heads, uint8_t color) {
    clear();
    for (uint8_t h = 0; h < heads; ++h) set(h, color);
  }
};

inline int8_t color_of(char aspect) {
  switch (aspect) {
    case 'R': return RED;
    case 'A': return AMBER;
    case 'G': return GREEN;
    default:  return -1;
  }
}

// Turn the 8 aspect letters of an L frame into the lamp image for `heads` heads (the
// first `heads` heads in wiring order, so a smaller build simply has fewer of them).
inline bool image_from_aspects(const char* aspects, uint8_t heads, LampImage* out) {
  out->clear();
  if (heads > MAX_HEADS) heads = MAX_HEADS;
  for (uint8_t h = 0; h < heads; ++h) {
    uint8_t junction = h / HEADS_PER_JUNCTION;
    uint8_t side = h % HEADS_PER_JUNCTION;
    int8_t color = color_of(aspects[junction * N_PHASES + phase_of_side(side)]);
    if (color < 0) return false;
    out->set(h, (uint8_t)color);
  }
  return true;
}

// --------------------------------------------------------------------------------------
// Assembling lines from the serial port
// --------------------------------------------------------------------------------------
struct LineReader {
  char buf[LINE_MAX + 1];
  uint8_t len;
  bool overflow;

  void reset() { len = 0; overflow = false; buf[0] = 0; }

  // Feed one received byte. Returns true when buf holds a complete line (without the
  // newline); call reset() after using it. A '>' always starts a new line, so the reader
  // resynchronises by itself after noise or a half-received line.
  bool feed(char c) {
    if (c == '\r') return false;
    if (c == '\n') {
      if (overflow || len == 0) { reset(); return false; }
      buf[len] = 0;
      return true;
    }
    if (c == '>') { len = 0; overflow = false; }
    if (len < LINE_MAX) buf[len++] = c;
    else overflow = true;
    return false;
  }
};

// --------------------------------------------------------------------------------------
// Decoding a line from the PC
// --------------------------------------------------------------------------------------
enum FrameKind { FRAME_INVALID = 0, FRAME_LAMPS = 1, FRAME_TEXT = 2 };

struct DownFrame {
  uint8_t kind;                    // FrameKind
  char aspects[N_ASPECTS + 1];     // FRAME_LAMPS
  uint8_t row;                     // FRAME_TEXT
  char text[TEXT_COLS + 1];        // FRAME_TEXT
};

// Checks the sentinel and the CRC. Returns the payload length (payload = line + 1), or -1.
inline int checked_payload(const char* line, uint8_t len, char sentinel) {
  if (len < 5 || line[0] != sentinel) return -1;
  int comma = -1;
  for (int i = len - 1; i > 0; --i) {
    if (line[i] == ',') { comma = i; break; }
  }
  if (comma < 2 || len - comma - 1 != 2) return -1;
  int8_t hi = hex_value(line[comma + 1]);
  int8_t lo = hex_value(line[comma + 2]);
  if (hi < 0 || lo < 0) return -1;
  uint8_t payload_len = (uint8_t)(comma - 1);
  if (crc8(line + 1, payload_len) != (uint8_t)((hi << 4) | lo)) return -1;
  return payload_len;
}

inline bool printable_text_char(char c) { return c >= ' ' && c <= '~' && c != ','; }

// Parses a complete line from the PC. Unknown or damaged lines give FRAME_INVALID.
inline uint8_t parse_downlink(const char* line, uint8_t len, DownFrame* out) {
  out->kind = FRAME_INVALID;
  int n = checked_payload(line, len, '>');
  if (n < 0) return FRAME_INVALID;
  const char* p = line + 1;

  if (n == 2 + N_ASPECTS && p[0] == 'L' && p[1] == ',') {
    for (uint8_t i = 0; i < N_ASPECTS; ++i) {
      if (color_of(p[2 + i]) < 0) return FRAME_INVALID;
      out->aspects[i] = p[2 + i];
    }
    out->aspects[N_ASPECTS] = 0;
    out->kind = FRAME_LAMPS;
    return FRAME_LAMPS;
  }

  if (n >= 4 && p[0] == 'T' && p[1] == ',' && p[2] >= '0' && p[2] < (char)('0' + TEXT_ROWS) &&
      p[3] == ',') {
    uint8_t tlen = (uint8_t)(n - 4);
    if (tlen > TEXT_COLS) return FRAME_INVALID;
    for (uint8_t i = 0; i < tlen; ++i) {
      if (!printable_text_char(p[4 + i])) return FRAME_INVALID;
      out->text[i] = p[4 + i];
    }
    out->text[tlen] = 0;
    out->row = (uint8_t)(p[2] - '0');
    out->kind = FRAME_TEXT;
    return FRAME_TEXT;
  }
  return FRAME_INVALID;
}

// --------------------------------------------------------------------------------------
// Building a line for the PC: "<" + payload + "," + CRC + "\n"
// --------------------------------------------------------------------------------------
static const uint8_t UPLINK_MAX = 48;

// Writes the framed line into out (at least UPLINK_MAX bytes); returns its length.
inline uint8_t frame_uplink(const char* payload, char* out) {
  uint8_t n = (uint8_t)strlen(payload);
  if (n > UPLINK_MAX - 6) n = UPLINK_MAX - 6;
  out[0] = '<';
  memcpy(out + 1, payload, n);
  uint8_t crc = crc8(payload, n);
  out[n + 1] = ',';
  out[n + 2] = hex_digit(crc >> 4);
  out[n + 3] = hex_digit(crc & 0x0F);
  out[n + 4] = '\n';
  out[n + 5] = 0;
  return (uint8_t)(n + 5);
}

// Appends the decimal digits of v to dst (NUL-terminated); returns the new end.
inline char* append_uint(char* dst, uint32_t v) {
  char tmp[11];
  uint8_t i = 0;
  do { tmp[i++] = (char)('0' + v % 10); v /= 10; } while (v && i < sizeof(tmp));
  while (i) *dst++ = tmp[--i];
  *dst = 0;
  return dst;
}

inline char* append_str(char* dst, const char* s) {
  while (*s) *dst++ = *s++;
  *dst = 0;
  return dst;
}

// --------------------------------------------------------------------------------------
// Debouncing a digital input
// --------------------------------------------------------------------------------------
struct Debouncer {
  uint8_t stable;       // the accepted level (1 = active)
  uint8_t candidate;    // the level currently being timed
  uint32_t since;       // when the candidate level was first seen (ms)

  void begin(uint8_t level, uint32_t now) { stable = candidate = level; since = now; }

  // Returns true when the accepted level changes, i.e. the input has held a new level
  // for at least hold_ms milliseconds.
  bool update(uint8_t level, uint32_t now, uint16_t hold_ms) {
    if (level != candidate) { candidate = level; since = now; return false; }
    if (candidate != stable && (uint32_t)(now - since) >= hold_ms) {
      stable = candidate;
      return true;
    }
    return false;
  }
};

// --------------------------------------------------------------------------------------
// Link supervision: the lamps follow the PC only while it keeps talking
// --------------------------------------------------------------------------------------
enum LinkState { LINK_WAITING = 0, LINK_LIVE = 1, LINK_LOST = 2 };

struct LinkWatch {
  uint8_t state;
  uint32_t last_frame_ms;
  uint32_t timeout_ms;

  void begin(uint32_t timeout) { state = LINK_WAITING; last_frame_ms = 0; timeout_ms = timeout; }

  // A valid lamp frame arrived. Returns true if the link just (re)started.
  bool lamp_frame(uint32_t now) {
    bool started = state != LINK_LIVE;
    state = LINK_LIVE;
    last_frame_ms = now;
    return started;
  }
  // Any other valid frame keeps a live link alive, but cannot start one on its own.
  void other_frame(uint32_t now) {
    if (state == LINK_LIVE) last_frame_ms = now;
  }
  // Returns true if the link just timed out (the board must go to its failsafe).
  bool poll(uint32_t now) {
    if (state == LINK_LIVE && (uint32_t)(now - last_frame_ms) > timeout_ms) {
      state = LINK_LOST;
      return true;
    }
    return false;
  }
  bool live() const { return state == LINK_LIVE; }
};

}  // namespace atsc

#endif  // ATSC_CORE_H
