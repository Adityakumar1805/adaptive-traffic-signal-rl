#!/usr/bin/env python3
"""Compile firmware/atsc_signal_node for the Arduino Uno without the Arduino IDE.

Uses the same compiler flags as the Arduino IDE (platform.txt of the AVR core), so the
result is what the IDE would upload. It exists so the firmware can be built and tested
automatically (see tools/virtual_board/README.md); to put the firmware on a real board,
just open the sketch in the Arduino IDE and press Upload.

    python tools/virtual_board/build_firmware.py            # -> build/firmware/atsc_signal_node.elf/.hex
    python tools/virtual_board/build_firmware.py --define ENABLE_OLED=0

Needs avr-gcc and the Arduino AVR core, found in this order: $ATSC_AVR_CORE, the Debian /
Ubuntu packages (apt install gcc-avr avr-libc arduino-core-avr), or an Arduino IDE 2
install (~/.arduino15 or ~/Library/Arduino15).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
SKETCH_DIR = ROOT / "firmware" / "atsc_signal_node"
SKETCH = SKETCH_DIR / "atsc_signal_node.ino"

MCU = "atmega328p"
F_CPU = "16000000L"
DEFINES = ["-DF_CPU=" + F_CPU, "-DARDUINO=10819", "-DARDUINO_AVR_UNO", "-DARDUINO_ARCH_AVR"]
C_FLAGS = ["-c", "-g", "-Os", "-w", "-std=gnu11", "-ffunction-sections", "-fdata-sections",
           "-flto", "-fno-fat-lto-objects"]
CPP_FLAGS = ["-c", "-g", "-Os", "-w", "-std=gnu++11", "-fpermissive", "-fno-exceptions",
             "-ffunction-sections", "-fdata-sections", "-fno-threadsafe-statics",
             "-Wno-error=narrowing", "-flto"]
S_FLAGS = ["-c", "-g", "-x", "assembler-with-cpp", "-flto"]
LD_FLAGS = ["-w", "-Os", "-g", "-flto", "-fuse-linker-plugin", "-Wl,--gc-sections"]
FLASH_MAX = 32256          # Uno with the Optiboot bootloader
RAM_MAX = 2048


class BuildError(RuntimeError):
    pass


def _versions(path: Path) -> List[Path]:
    if not path.is_dir():
        return []
    def key(p: Path) -> Tuple[int, ...]:
        return tuple(int(x) for x in re.findall(r"\d+", p.name)[:4])
    return sorted((p for p in path.iterdir() if p.is_dir()), key=key, reverse=True)


def find_core() -> Optional[Path]:
    """Directory holding cores/arduino, variants/standard and libraries/Wire."""
    candidates: List[Path] = []
    env = os.environ.get("ATSC_AVR_CORE")
    if env:
        candidates.append(Path(env))
    candidates.append(Path("/usr/share/arduino/hardware/arduino/avr"))
    for base in (Path.home() / ".arduino15", Path.home() / "Library" / "Arduino15",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Arduino15"):
        candidates += _versions(base / "packages" / "arduino" / "hardware" / "avr")
    for c in candidates:
        if (c / "cores" / "arduino" / "Arduino.h").is_file() and (c / "variants" / "standard").is_dir():
            return c
    return None


def find_tool(name: str) -> Optional[str]:
    found = shutil.which(name)
    if found:
        return found
    for base in (Path.home() / ".arduino15", Path.home() / "Library" / "Arduino15"):
        for ver in _versions(base / "packages" / "arduino" / "tools" / "avr-gcc"):
            exe = ver / "bin" / name
            if exe.is_file():
                return str(exe)
    return None


def toolchain_available() -> bool:
    return find_core() is not None and all(find_tool(t) for t in
                                           ("avr-gcc", "avr-g++", "avr-gcc-ar", "avr-objcopy", "avr-size"))


def _run(cmd: Sequence[str]) -> str:
    proc = subprocess.run(list(cmd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise BuildError(f"command failed: {' '.join(cmd)}\n{proc.stdout}{proc.stderr}")
    return proc.stdout + proc.stderr


def _compile(src: Path, obj: Path, includes: List[str], extra: Sequence[str] = ()) -> None:
    obj.parent.mkdir(parents=True, exist_ok=True)
    if obj.exists() and obj.stat().st_mtime >= src.stat().st_mtime and not extra:
        return
    if src.suffix == ".c":
        cmd = [find_tool("avr-gcc")] + C_FLAGS
    elif src.suffix == ".S":
        cmd = [find_tool("avr-gcc")] + S_FLAGS
    else:
        cmd = [find_tool("avr-g++")] + CPP_FLAGS
    _run(cmd + ["-mmcu=" + MCU] + DEFINES + list(extra) + includes + [str(src), "-o", str(obj)])


def build(out_dir: Path, defines: Dict[str, str] = None, warnings: bool = False) -> Dict[str, object]:
    """Build the sketch; returns paths and the flash / RAM usage."""
    core = find_core()
    if core is None or not toolchain_available():
        raise BuildError("avr-gcc or the Arduino AVR core was not found "
                         "(apt install gcc-avr avr-libc arduino-core-avr)")
    out_dir = Path(out_dir)
    core_src = core / "cores" / "arduino"
    variant = core / "variants" / "standard"
    wire = core / "libraries" / "Wire" / "src"
    includes = ["-I" + str(core_src), "-I" + str(variant), "-I" + str(wire), "-I" + str(SKETCH_DIR)]

    # 1. the Arduino core, archived like the IDE does
    core_objs: List[str] = []
    for src in sorted(core_src.iterdir()):
        if src.suffix in (".c", ".cpp", ".S"):
            obj = out_dir / "core" / (src.name + ".o")
            _compile(src, obj, includes)
            core_objs.append(str(obj))
    core_a = out_dir / "core.a"
    if core_a.exists():
        core_a.unlink()
    _run([find_tool("avr-gcc-ar"), "rcs", str(core_a)] + core_objs)

    # 2. the Wire library (the OLED)
    lib_objs: List[str] = []
    for src in [wire / "Wire.cpp", wire / "utility" / "twi.c"]:
        obj = out_dir / "libraries" / (src.name + ".o")
        _compile(src, obj, includes)
        lib_objs.append(str(obj))

    # 3. the sketch: what the IDE does to an .ino - include Arduino.h, keep line numbers.
    #    A setting given in `defines` replaces the sketch's own "#define NAME value" line.
    source = SKETCH.read_text(encoding="utf-8")
    for name, value in (defines or {}).items():
        source, n = re.subn(r"(?m)^#define %s\s+\S+" % re.escape(name),
                            "#define %s %s" % (name, value), source, count=1)
        if n != 1:
            raise BuildError(f"no '#define {name}' setting in {SKETCH.name}")
    sketch_cpp = out_dir / "sketch" / "atsc_signal_node.ino.cpp"
    sketch_cpp.parent.mkdir(parents=True, exist_ok=True)
    sketch_cpp.write_text('#include <Arduino.h>\n#line 1 "%s"\n%s' % (SKETCH.as_posix(), source),
                          encoding="utf-8")
    flags = [f for f in CPP_FLAGS if f != "-w"] + (["-Wall", "-Wextra"] if warnings else ["-w"])
    sketch_obj = out_dir / "sketch" / "atsc_signal_node.ino.cpp.o"
    compile_log = _run([find_tool("avr-g++")] + flags + ["-mmcu=" + MCU] + DEFINES + includes
                       + [str(sketch_cpp), "-o", str(sketch_obj)])

    # 4. link, hex, size
    elf = out_dir / "atsc_signal_node.elf"
    _run([find_tool("avr-gcc")] + LD_FLAGS + ["-mmcu=" + MCU, "-o", str(elf), str(sketch_obj)]
         + lib_objs + [str(core_a), "-L" + str(out_dir), "-lm"])
    hexf = out_dir / "atsc_signal_node.hex"
    _run([find_tool("avr-objcopy"), "-O", "ihex", "-R", ".eeprom", str(elf), str(hexf)])
    sizes = _section_sizes(elf)
    flash = sizes.get(".text", 0) + sizes.get(".data", 0)
    ram = sizes.get(".data", 0) + sizes.get(".bss", 0)
    return {"elf": elf, "hex": hexf, "flash": flash, "ram": ram, "core": core,
            "warnings": compile_log.strip()}


def _section_sizes(elf: Path) -> Dict[str, int]:
    out = _run([find_tool("avr-size"), "-A", str(elf)])
    sizes: Dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith(".") and parts[1].isdigit():
            sizes[parts[0]] = int(parts[1])
    return sizes


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / "build" / "firmware"))
    ap.add_argument("--define", action="append", default=[], metavar="NAME=VALUE",
                    help="override a setting, e.g. ENABLE_OLED=0 (repeatable)")
    ap.add_argument("--warnings", action="store_true", help="compile the sketch with -Wall -Wextra")
    args = ap.parse_args(argv)
    defines = dict(d.split("=", 1) if "=" in d else (d, "1") for d in args.define)
    try:
        res = build(Path(args.out), defines, warnings=args.warnings)
    except BuildError as exc:
        print(exc, file=sys.stderr)
        return 1
    if res["warnings"]:
        print(res["warnings"])
    print(f"core      {res['core']}")
    print(f"firmware  {res['hex']}")
    print(f"flash     {res['flash']} of {FLASH_MAX} bytes ({100 * res['flash'] / FLASH_MAX:.0f} %)")
    print(f"RAM       {res['ram']} of {RAM_MAX} bytes ({100 * res['ram'] / RAM_MAX:.0f} %) "
          f"used by globals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
