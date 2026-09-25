// Sensei Desk pan-tilt head: ESP32 firmware (from the build guide, section 7).
//
// One-line text commands over USB serial at 115200 baud; each servo steps toward its
// target one degree at a time so the phone moves smoothly.
//
//   PING               -> PONG
//   PAN <deg>          -> OK, later ARRIVED <pan> <tilt>
//   TILT <deg>         -> OK, later ARRIVED ...
//   MOVE <pan> <tilt>  -> OK, later ARRIVED ...
//   PRESET <name>      -> OK (later ARRIVED ...) or ERR unknown preset
//   SPEED <ms/step>    -> OK   (lower = faster)
//   POS?               -> POS <pan> <tilt>
//
// Wiring: pan signal GPIO 18, tilt signal GPIO 19. Servos on their own 5 V >= 4 A supply,
// never the ESP32's 5V/VIN pin; ESP32 GND tied to the supply GND.
// Needs the Espressif "esp32" board package and the ESP32Servo library.
#include <Arduino.h>
#include <ESP32Servo.h>

const int PAN_PIN = 18;
const int TILT_PIN = 19;

// Safe mechanical limits in degrees. Calibrate on your build.
const int PAN_MIN = 10,  PAN_MAX = 170;
const int TILT_MIN = 40, TILT_MAX = 150;

struct Preset { const char* name; int pan; int tilt; };
// Placeholder angles: nudge the head from the gateway console (http://<spark>:8787),
// then copy the pan/tilt it shows into these.
Preset presets[] = {
  {"home",     90,  90},
  {"notebook", 90, 135},
  {"student",  90,  70},
};
const int NUM_PRESETS = sizeof(presets) / sizeof(presets[0]);

Servo panServo, tiltServo;
int panPos = 90, tiltPos = 90;
int panTarget = 90, tiltTarget = 90;
int stepMs = 15;              // ms per 1-degree step (~66 deg/s)
bool moving = false;
unsigned long lastStep = 0;
String buf;

int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }

void setTargets(int p, int t) {
  panTarget = clampi(p, PAN_MIN, PAN_MAX);
  tiltTarget = clampi(t, TILT_MIN, TILT_MAX);
  moving = true;
}

void handleLine(String line) {
  line.trim();
  if (line.length() == 0) return;
  int sp = line.indexOf(' ');
  String cmd = (sp < 0) ? line : line.substring(0, sp);
  String arg = (sp < 0) ? "" : line.substring(sp + 1);
  cmd.toUpperCase();
  arg.trim();

  if (cmd == "PING") {
    Serial.println("PONG");
  } else if (cmd == "PAN") {
    setTargets(arg.toInt(), tiltTarget); Serial.println("OK");
  } else if (cmd == "TILT") {
    setTargets(panTarget, arg.toInt()); Serial.println("OK");
  } else if (cmd == "MOVE") {
    int sp2 = arg.indexOf(' ');
    if (sp2 < 0) { Serial.println("ERR usage: MOVE <pan> <tilt>"); return; }
    setTargets(arg.substring(0, sp2).toInt(), arg.substring(sp2 + 1).toInt());
    Serial.println("OK");
  } else if (cmd == "PRESET") {
    arg.toLowerCase();
    for (int i = 0; i < NUM_PRESETS; i++) {
      if (arg == presets[i].name) {
        setTargets(presets[i].pan, presets[i].tilt);
        Serial.println("OK");
        return;
      }
    }
    Serial.println("ERR unknown preset");
  } else if (cmd == "SPEED") {
    stepMs = clampi(arg.toInt(), 2, 100); Serial.println("OK");
  } else if (cmd == "POS?") {
    Serial.printf("POS %d %d\n", panPos, tiltPos);
  } else {
    Serial.println("ERR unknown command");
  }
}

void setup() {
  Serial.begin(115200);
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  panServo.setPeriodHertz(50);
  tiltServo.setPeriodHertz(50);
  panServo.attach(PAN_PIN, 500, 2400);   // pulse range in microseconds
  tiltServo.attach(TILT_PIN, 500, 2400);
  panServo.write(panPos);                // servos snap to 90/90 at power-up: keep hands clear
  tiltServo.write(tiltPos);
  Serial.println("READY");
}

void loop() {
  // 1. Read serial commands, one per line.
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') { handleLine(buf); buf = ""; }
    else if (c != '\r' && buf.length() < 64) { buf += c; }
  }
  // 2. Step servos toward targets for smooth motion.
  if (moving && millis() - lastStep >= (unsigned long)stepMs) {
    lastStep = millis();
    if (panPos != panTarget)   panPos  += (panTarget  > panPos)  ? 1 : -1;
    if (tiltPos != tiltTarget) tiltPos += (tiltTarget > tiltPos) ? 1 : -1;
    panServo.write(panPos);
    tiltServo.write(tiltPos);
    if (panPos == panTarget && tiltPos == tiltTarget) {
      moving = false;
      Serial.printf("ARRIVED %d %d\n", panPos, tiltPos);
    }
  }
}
