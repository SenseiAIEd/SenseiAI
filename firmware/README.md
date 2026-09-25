# Pan-tilt head firmware (ESP32)

`sensei_head/sensei_head.ino` is the source. `sensei_head/bin/sensei_head-esp32.bin` is the same
sketch prebuilt for an ESP32 Dev Module (ESP32-WROOM-32, 4 MB flash; esp32 core 3.3.12,
ESP32Servo 3.2.1), so flashing needs only esptool, not the multi-GB Arduino toolchain:

```sh
uvx esptool --port /dev/cu.usbserial-0001 write-flash 0x0 firmware/sensei_head/bin/sensei_head-esp32.bin
```

(Linux: `--port /dev/ttyUSB0`. No uv? `pip install esptool`, then `esptool ...`. If it hangs
at "Connecting...", hold the BOOT button.) Flashing resets saved presets and limits to the
defaults in the sketch.

The prebuilt image uses the sketch's default HARD limits (pan 5-175, tilt 30-160). If your
bracket can't reach those, change `HARD_PAN_*` / `HARD_TILT_*` and rebuild:
`arduino-cli compile --fqbn esp32:esp32:esp32 --output-dir out firmware/sensei_head`, then flash
`out/sensei_head.ino.merged.bin` the same way. Soft limits can be narrowed any time without
rebuilding: `LIMIT TILT 60 140` over serial, or `POST /head {"limit": "tilt", "lo": 60, "hi": 140}`.
