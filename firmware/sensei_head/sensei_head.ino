// Sensei Desk pan-tilt head: ESP32 firmware.
//
// One-line text commands over USB serial at 115200 baud. Each servo steps toward its target one
// degree at a time (slower over the last few degrees), so the phone moves smoothly and stops
// without wobbling.
//
//   PING                      -> PONG
//   MOVE <pan> <tilt>         -> OK, later ARRIVED <pan> <tilt>
//   PAN <deg> / TILT <deg>    -> OK, later ARRIVED ...
//   NUDGE <dpan> <dtilt>      -> OK, later ARRIVED ...        (relative)
//   PRESET <name>             -> OK, later ARRIVED ...  or ERR unknown preset
//   SAVE <name>               -> OK            store where the head points now as that preset
//   PRESETS?                  -> PRESETS home 90 90 notebook 90 135 student 90 70
//   LIMIT PAN <min> <max>     -> OK            soft limits, inside the hard ones below
//   LIMIT TILT <min> <max>    -> OK
//   LIMITS?                   -> LIMITS <pan min> <pan max> <tilt min> <tilt max>
//   SPEED <ms/step>           -> OK            (lower = faster)
//   STOP                      -> OK, ARRIVED   stop where it is
//   RELAX                     -> OK            servos off (for the balance check); any move wakes them
//   POS?                      -> POS <pan> <tilt>
//
// No over-rotation, in two layers: HARD limits are compiled in and can never be exceeded; the
// SOFT limits (LIMIT ...) narrow them for your build and are kept in flash. Every target is
// clamped, whatever the command.
//
// Presets, limits and the last position are kept in flash (Preferences), so calibration survives
// a reboot and the head wakes up where it was left instead of snapping to the centre.
//
// Wiring: pan signal GPIO 18, tilt signal GPIO 19. Servos on their own 5 V >= 4 A supply,
// never the ESP32's 5V/VIN pin; ESP32 GND tied to the supply GND.
// Needs the Espressif "esp32" board package and the ESP32Servo library.
#include <Arduino.h>
#include <ESP32Servo.h>
#include <Preferences.h>

const int PAN_PIN = 18;
const int TILT_PIN = 19;

// Hard limits: what the bracket can physically do without hitting itself. Measure once with the
// servos unpowered and move the head by hand; set these a few degrees inside what you find.
const int HARD_PAN_MIN = 5,   HARD_PAN_MAX = 175;
const int HARD_TILT_MIN = 30, HARD_TILT_MAX = 160;

struct Preset { char name[12]; int pan; int tilt; };
Preset presets[] = {        // defaults until you SAVE your own
  {"home",     90,  90},
  {"notebook", 90, 135},
  {"student",  90,  70},
};
const int NUM_PRESETS = sizeof(presets) / sizeof(presets[0]);

int panMin = 10, panMax = 170, tiltMin = 40, tiltMax = 150;   // soft limits (defaults)

Servo panServo, tiltServo;
Preferences prefs;
bool attached = false;
int panPos = 90, tiltPos = 90;
int panTarget = 90, tiltTarget = 90;
int stepMs = 15;              // ms per 1-degree step (~66 deg/s)
const int EASE_DEG = 6;       // last few degrees at half speed
bool moving = false;
unsigned long lastStep = 0;
unsigned long arrivedAt = 0;
bool posDirty = false;        // last position not yet saved to flash
String buf;

int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }

void loadSettings() {
  prefs.begin("sensei", false);
  panMin  = clampi(prefs.getInt("panMin", panMin), HARD_PAN_MIN, HARD_PAN_MAX);
  panMax  = clampi(prefs.getInt("panMax", panMax), HARD_PAN_MIN, HARD_PAN_MAX);
  tiltMin = clampi(prefs.getInt("tiltMin", tiltMin), HARD_TILT_MIN, HARD_TILT_MAX);
  tiltMax = clampi(prefs.getInt("tiltMax", tiltMax), HARD_TILT_MIN, HARD_TILT_MAX);
  for (int i = 0; i < NUM_PRESETS; i++) {
    String k = String(presets[i].name);
    presets[i].pan = prefs.getInt((k + "P").c_str(), presets[i].pan);
    presets[i].tilt = prefs.getInt((k + "T").c_str(), presets[i].tilt);
  }
  // Wake up where we were left (if nobody turned the head by hand, nothing moves).
  panPos = clampi(prefs.getInt("lastP", presets[0].pan), panMin, panMax);
  tiltPos = clampi(prefs.getInt("lastT", presets[0].tilt), tiltMin, tiltMax);
  panTarget = panPos;
  tiltTarget = tiltPos;
}

void attachServos() {
  if (attached) return;
  panServo.setPeriodHertz(50);
  tiltServo.setPeriodHertz(50);
  panServo.attach(PAN_PIN, 500, 2400);   // pulse range in microseconds
  tiltServo.attach(TILT_PIN, 500, 2400);
  panServo.write(panPos);
  tiltServo.write(tiltPos);
  attached = true;
}

void setTargets(int p, int t) {
  attachServos();
  panTarget = clampi(p, panMin, panMax);
  tiltTarget = clampi(t, tiltMin, tiltMax);
  moving = true;
}

Preset* findPreset(String name) {
  for (int i = 0; i < NUM_PRESETS; i++)
    if (name == presets[i].name) return &presets[i];
  return nullptr;
}

bool twoInts(String arg, int &a, int &b) {
  int sp = arg.indexOf(' ');
  if (sp < 0) return false;
  a = arg.substring(0, sp).toInt();
  b = arg.substring(sp + 1).toInt();
  return true;
}

void handleLine(String line) {
  line.trim();
  if (line.length() == 0) return;
  int sp = line.indexOf(' ');
  String cmd = (sp < 0) ? line : line.substring(0, sp);
  String arg = (sp < 0) ? "" : line.substring(sp + 1);
  cmd.toUpperCase();
  arg.trim();
  int a, b;

  if (cmd == "PING") {
    Serial.println("PONG");
  } else if (cmd == "PAN") {
    setTargets(arg.toInt(), tiltTarget); Serial.println("OK");
  } else if (cmd == "TILT") {
    setTargets(panTarget, arg.toInt()); Serial.println("OK");
  } else if (cmd == "MOVE") {
    if (!twoInts(arg, a, b)) { Serial.println("ERR usage: MOVE <pan> <tilt>"); return; }
    setTargets(a, b); Serial.println("OK");
  } else if (cmd == "NUDGE") {
    if (!twoInts(arg, a, b)) { Serial.println("ERR usage: NUDGE <dpan> <dtilt>"); return; }
    setTargets(panTarget + a, tiltTarget + b); Serial.println("OK");
  } else if (cmd == "PRESET") {
    arg.toLowerCase();
    Preset* p = findPreset(arg);
    if (!p) { Serial.println("ERR unknown preset"); return; }
    setTargets(p->pan, p->tilt); Serial.println("OK");
  } else if (cmd == "SAVE") {
    arg.toLowerCase();
    Preset* p = findPreset(arg);
    if (!p) { Serial.println("ERR unknown preset"); return; }
    p->pan = panPos; p->tilt = tiltPos;
    prefs.putInt((arg + "P").c_str(), panPos);
    prefs.putInt((arg + "T").c_str(), tiltPos);
    Serial.println("OK");
  } else if (cmd == "PRESETS?") {
    Serial.print("PRESETS");
    for (int i = 0; i < NUM_PRESETS; i++) Serial.printf(" %s %d %d", presets[i].name, presets[i].pan, presets[i].tilt);
    Serial.println();
  } else if (cmd == "LIMIT") {
    int sp2 = arg.indexOf(' ');
    String axis = (sp2 < 0) ? arg : arg.substring(0, sp2);
    axis.toUpperCase();
    if (sp2 < 0 || !twoInts(arg.substring(sp2 + 1), a, b) || a >= b) {
      Serial.println("ERR usage: LIMIT PAN|TILT <min> <max>"); return;
    }
    if (axis == "PAN") {
      panMin = clampi(a, HARD_PAN_MIN, HARD_PAN_MAX); panMax = clampi(b, HARD_PAN_MIN, HARD_PAN_MAX);
      prefs.putInt("panMin", panMin); prefs.putInt("panMax", panMax);
    } else if (axis == "TILT") {
      tiltMin = clampi(a, HARD_TILT_MIN, HARD_TILT_MAX); tiltMax = clampi(b, HARD_TILT_MIN, HARD_TILT_MAX);
      prefs.putInt("tiltMin", tiltMin); prefs.putInt("tiltMax", tiltMax);
    } else { Serial.println("ERR usage: LIMIT PAN|TILT <min> <max>"); return; }
    setTargets(panTarget, tiltTarget);   // re-clamp where we are headed
    Serial.println("OK");
  } else if (cmd == "LIMITS?") {
    Serial.printf("LIMITS %d %d %d %d\n", panMin, panMax, tiltMin, tiltMax);
  } else if (cmd == "SPEED") {
    stepMs = clampi(arg.toInt(), 2, 100); Serial.println("OK");
  } else if (cmd == "STOP") {
    panTarget = panPos; tiltTarget = tiltPos; moving = true;   // ARRIVED on the next loop
    Serial.println("OK");
  } else if (cmd == "RELAX") {
    panServo.detach(); tiltServo.detach(); attached = false; moving = false;
    Serial.println("OK");
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
  loadSettings();
  attachServos();
  Serial.println("READY");
}

void loop() {
  // 1. Read serial commands, one per line.
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') { handleLine(buf); buf = ""; }
    else if (c != '\r' && buf.length() < 64) { buf += c; }
  }
  // 2. Step servos toward targets; half speed over the last EASE_DEG degrees.
  int remaining = max(abs(panTarget - panPos), abs(tiltTarget - tiltPos));
  unsigned long interval = (unsigned long)stepMs * (remaining <= EASE_DEG ? 2 : 1);
  if (moving && millis() - lastStep >= interval) {
    lastStep = millis();
    if (panPos != panTarget)   panPos  += (panTarget  > panPos)  ? 1 : -1;
    if (tiltPos != tiltTarget) tiltPos += (tiltTarget > tiltPos) ? 1 : -1;
    panServo.write(panPos);
    tiltServo.write(tiltPos);
    if (panPos == panTarget && tiltPos == tiltTarget) {
      moving = false;
      arrivedAt = millis();
      posDirty = true;
      Serial.printf("ARRIVED %d %d\n", panPos, tiltPos);
    }
  }
  // 3. Remember where we are once the head has rested 2 s (spares the flash during busy moves).
  if (posDirty && !moving && millis() - arrivedAt > 2000) {
    prefs.putInt("lastP", panPos);
    prefs.putInt("lastT", tiltPos);
    posDirty = false;
  }
}
