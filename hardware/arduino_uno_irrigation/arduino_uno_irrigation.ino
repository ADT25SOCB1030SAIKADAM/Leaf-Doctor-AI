/*
  Leaf Doctor smart irrigation node (Arduino Uno)

  The Uno has no Wi-Fi, so it talks to your computer over the USB cable:
    Uno -> computer :  M,<moisture %>,<raw value>      (every few seconds)
    computer -> Uno :  P,1  (pump ON)   or   P,0  (pump OFF)
  A small Python program on the computer (serial_bridge.py) passes these messages
  to the Leaf Doctor website and brings the pump command back.

  If the computer stops answering, the Uno keeps working on its own with simple local rules.
  A safety timer stops the pump if it runs too long.
*/

// ---- 1. Pins ----
const int  SENSOR_PIN       = A0;    // soil sensor analog output (AO / SIG)
const int  RELAY_PIN        = 7;     // relay module IN pin
const bool RELAY_ACTIVE_LOW = true;  // most relay boards switch ON when the pin is LOW
const int  SENSOR_POWER_PIN = -1;    // optional: set to e.g. 8 to power the sensor only while reading
                                     // (helps resistive sensors last longer). -1 = always powered from 5V.

// ---- 2. Calibration: open the Serial Monitor (9600 baud) and read the raw value
//         with the sensor in dry air, then in a glass of water, and type them here ----
const int RAW_DRY = 1000;            // dry air  = 0 %   (typical: resistive about 1000, capacitive about 520)
const int RAW_WET = 400;             // in water = 100 % (typical: resistive about 400, capacitive about 260)

// ---- 3. Timing and safety ----
const unsigned long SEND_EVERY_MS   = 3000;
const unsigned long LINK_TIMEOUT_MS = 15000;     // no answer from the computer for this long = use local rules
const unsigned long MAX_PUMP_MS     = 60000UL;   // never run the pump longer than 60 s in one go
const unsigned long COOLDOWN_MS     = 600000UL;  // rest 10 minutes after a safety cut-off
const float LOCAL_ON_BELOW  = 30.0;              // local rules, used only without the computer
const float LOCAL_OFF_ABOVE = 50.0;

bool pumpOn = false;
bool wasLinked = false;
unsigned long pumpStart = 0;
unsigned long lockedUntil = 0;
unsigned long lastSend = 0;
unsigned long lastCommand = 0;
char lineBuf[24];
byte lineLen = 0;

bool isLocked() {
  return lockedUntil != 0 && (long)(millis() - lockedUntil) < 0;
}

void setPump(bool on) {
  if (on && isLocked()) on = false;
  if (on && !pumpOn) pumpStart = millis();
  pumpOn = on;
  digitalWrite(RELAY_PIN, (on ^ RELAY_ACTIVE_LOW) ? HIGH : LOW);
}

float readMoisture(int &rawOut) {
  if (SENSOR_POWER_PIN >= 0) { digitalWrite(SENSOR_POWER_PIN, HIGH); delay(20); }
  long sum = 0;
  for (byte i = 0; i < 10; i++) {          // average 10 samples to smooth out noise
    sum += analogRead(SENSOR_PIN);
    delay(5);
  }
  if (SENSOR_POWER_PIN >= 0) digitalWrite(SENSOR_POWER_PIN, LOW);
  rawOut = sum / 10;
  float pct = (float)(RAW_DRY - rawOut) * 100.0 / (float)(RAW_DRY - RAW_WET);
  return constrain(pct, 0.0, 100.0);
}

void handleLine() {
  lineBuf[lineLen] = '\0';
  if (lineLen >= 3 && lineBuf[0] == 'P' && lineBuf[1] == ',') {
    setPump(lineBuf[2] == '1');
    lastCommand = millis();
    if (lastCommand == 0) lastCommand = 1;
  }
}

void readSerial() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n') {
      handleLine();
      lineLen = 0;
    } else if (c != '\r' && lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = c;
    }
  }
}

void setup() {
  digitalWrite(RELAY_PIN, RELAY_ACTIVE_LOW ? HIGH : LOW);   // pump OFF before the pin becomes an output
  pinMode(RELAY_PIN, OUTPUT);
  if (SENSOR_POWER_PIN >= 0) pinMode(SENSOR_POWER_PIN, OUTPUT);
  Serial.begin(9600);
  Serial.println(F("# Leaf Doctor irrigation node ready"));
}

void loop() {
  readSerial();

  // Safety: cut the pump if it has been on too long
  if (pumpOn && millis() - pumpStart > MAX_PUMP_MS) {
    setPump(false);
    lockedUntil = millis() + COOLDOWN_MS;
    Serial.println(F("# SAFETY: pump ran too long, switched off and resting"));
  }

  if (millis() - lastSend >= SEND_EVERY_MS) {
    lastSend = millis();
    int raw;
    float moisture = readMoisture(raw);

    Serial.print("M,");
    Serial.print(moisture, 1);
    Serial.print(",");
    Serial.println(raw);

    bool linked = lastCommand != 0 && (millis() - lastCommand) < LINK_TIMEOUT_MS;
    if (linked != wasLinked) {
      Serial.println(linked ? F("# computer link OK") : F("# no answer from the computer, using local rules"));
      wasLinked = linked;
    }
    if (!linked) {                         // local rules when the computer is not answering
      if (moisture < LOCAL_ON_BELOW) setPump(true);
      else if (moisture > LOCAL_OFF_ABOVE) setPump(false);
    }
  }
}
