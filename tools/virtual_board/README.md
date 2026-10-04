# Virtual signal board

The real firmware (`firmware/atsc_signal_node`), compiled for the Arduino Uno, running on a
simulated ATmega328P — so the hardware path can be developed and tested without a board.

| File | What it is |
|---|---|
| `build_firmware.py` | compiles the sketch with the Arduino IDE's own compiler flags (no IDE needed) |
| `vboard.c` | the simulated board ([simavr](https://github.com/buserror/simavr)): CPU at 16 MHz in real time, USB serial on a pseudo-terminal, six 74HC595s, SSD1306 OLED, buzzer, IR sensor and remote inputs |
| `virtual_board.py` | builds and starts it, and lets Python press sensors and buttons, read the lamps and the OLED |
| `oled.py` | turns the simulated OLED's memory back into text, or a picture |

Linux only (Debian / Ubuntu):

```bash
sudo apt install gcc-avr avr-libc arduino-core-avr simavr libsimavr-dev libelf-dev
pip install pyserial==3.5

python tools/virtual_board/build_firmware.py        # flash and RAM use, as the IDE reports them
python tools/virtual_board/virtual_board.py         # prints its port, then every lamp change
python run.py demo --hardware --port /dev/pts/N     # in a second terminal: the port printed above
```

`tests/test_hardware.py::test_virtual_board_end_to_end` boots the board, checks the lamp test
and the flashing-amber failsafe, connects the real dashboard session, presses a sensor and a
remote button, and checks that every lamp change on the board matches the RL grid and that
the OLED shows the live figures. It is skipped automatically where the tools are missing.
