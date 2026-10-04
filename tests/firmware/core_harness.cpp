// Test harness for firmware/atsc_signal_node/atsc_core.h, compiled and driven by
// tests/test_hardware.py with a PC C++ compiler. One command per stdin line, fields
// separated by TAB; one answer per command on stdout.
//
//   crc     <text>                      -> two hex digits
//   parse   <line>                      -> LAMPS <aspects> | TEXT <row> <text> | INVALID
//   image   <aspects> <heads>           -> 12 hex digits (bit n = LED n) | BAD
//   uplink  <payload>                   -> the framed line, without its newline
//   reader  <hex bytes>                 -> LINE <text> ... then END
//   debounce <hold_ms> <t:level,...>    -> CHANGES t:level ...
//   link    <timeout_ms> <K@t,...>      -> K: L lamp frame, T other frame, P poll;
//                                          prints START@t / LOST@t ... then STATE <n>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "atsc_core.h"

static std::vector<std::string> split(const std::string& s, char sep) {
  std::vector<std::string> out;
  std::string cur;
  for (char c : s) {
    if (c == sep) { out.push_back(cur); cur.clear(); } else { cur += c; }
  }
  out.push_back(cur);
  return out;
}

int main() {
  char raw[512];
  while (std::fgets(raw, sizeof(raw), stdin)) {
    std::string line(raw);
    while (!line.empty() && (line.back() == '\n' || line.back() == '\r')) line.pop_back();
    std::vector<std::string> f = split(line, '\t');
    const std::string& cmd = f[0];

    if (cmd == "crc" && f.size() == 2) {
      std::printf("%02X\n", atsc::crc8(f[1].c_str(), (uint8_t)f[1].size()));
    } else if (cmd == "parse" && f.size() == 2) {
      atsc::DownFrame fr;
      uint8_t kind = atsc::parse_downlink(f[1].c_str(), (uint8_t)f[1].size(), &fr);
      if (kind == atsc::FRAME_LAMPS) std::printf("LAMPS %s\n", fr.aspects);
      else if (kind == atsc::FRAME_TEXT) std::printf("TEXT %u %s\n", fr.row, fr.text);
      else std::printf("INVALID\n");
    } else if (cmd == "image" && f.size() == 3) {
      atsc::LampImage img;
      if (f[1].size() != atsc::N_ASPECTS ||
          !atsc::image_from_aspects(f[1].c_str(), (uint8_t)std::atoi(f[2].c_str()), &img)) {
        std::printf("BAD\n");
      } else {
        unsigned long long v = 0;
        for (int i = atsc::MAX_CHIPS - 1; i >= 0; --i) v = (v << 8) | img.bytes[i];
        std::printf("%012llx\n", v);
      }
    } else if (cmd == "uplink" && f.size() == 2) {
      char out[atsc::UPLINK_MAX];
      uint8_t n = atsc::frame_uplink(f[1].c_str(), out);
      if (n && out[n - 1] == '\n') out[n - 1] = 0;
      std::printf("%s\n", out);
    } else if (cmd == "reader" && f.size() == 2) {
      atsc::LineReader r;
      r.reset();
      const std::string& hex = f[1];
      for (size_t i = 0; i + 1 < hex.size(); i += 2) {
        char c = (char)std::strtol(hex.substr(i, 2).c_str(), nullptr, 16);
        if (r.feed(c)) { std::printf("LINE %s\n", r.buf); r.reset(); }
      }
      std::printf("END\n");
    } else if (cmd == "debounce" && f.size() == 3) {
      atsc::Debouncer d;
      uint16_t hold = (uint16_t)std::atoi(f[1].c_str());
      bool first = true;
      std::printf("CHANGES");
      for (const std::string& ev : split(f[2], ',')) {
        std::vector<std::string> tl = split(ev, ':');
        uint32_t t = (uint32_t)std::strtoul(tl[0].c_str(), nullptr, 10);
        uint8_t level = (uint8_t)std::atoi(tl[1].c_str());
        if (first) { d.begin(level, t); first = false; continue; }
        if (d.update(level, t, hold)) std::printf(" %u:%u", t, d.stable);
      }
      std::printf("\n");
    } else if (cmd == "link" && f.size() == 3) {
      atsc::LinkWatch w;
      w.begin((uint32_t)std::strtoul(f[1].c_str(), nullptr, 10));
      for (const std::string& ev : split(f[2], ',')) {
        char kind = ev[0];
        uint32_t t = (uint32_t)std::strtoul(ev.c_str() + 2, nullptr, 10);
        if (kind == 'L') { if (w.lamp_frame(t)) std::printf("START@%u ", t); }
        else if (kind == 'T') { w.other_frame(t); }
        else if (kind == 'P') { if (w.poll(t)) std::printf("LOST@%u ", t); }
      }
      std::printf("STATE %u\n", w.state);
    } else {
      std::printf("ERR %s\n", cmd.c_str());
    }
    std::fflush(stdout);
  }
  return 0;
}
