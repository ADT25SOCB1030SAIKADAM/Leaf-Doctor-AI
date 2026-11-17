/*
  Leaf Doctor smart irrigation node (ESP32)

  - Reads a soil moisture sensor
  - Sends the reading to the Leaf Doctor server every few seconds
  - Switches a relay (pump) according to the server's answer
  - If Wi-Fi or the server is down, it falls back to simple local rules
  - A safety timer stops the pump if it runs too long
*/
#include <WiFi.h>
#include <HTTPClient.h>

// ---- 1. Change these ----
const char* WIFI_SSID  = "YOUR_WIFI_NAME";
const char* WIFI_PASS  = "YOUR_WIFI_PASSWORD";
const char* SERVER_URL = "http://192.168.1.37:8000/api/sensor";  // your Mac's IP address + :8000
const char* DEVICE_ID  = "esp32-1";
const char* API_KEY    = "";   // same value as SENSOR_KEY on the server (leave empty while developing)

// ---- 2. Pins ----
const int  SENSOR_PIN       = 34;    // soil sensor analog output (use an ADC1 pin: 32 to 39)
const int  RELAY_PIN        = 26;    // relay module IN pin
const bool RELAY_ACTIVE_LOW = true;  // most relay boards switch ON when the pin is LOW

// ---- 3. Calibration: open the Serial Monitor, note the raw value in dry air, then in a glass of water ----
const int RAW_DRY = 3200;            // sensor in dry air   = 0 %
const int RAW_WET = 1300;            // sensor in water     = 100 %

// ---- 4. Timing and safety ----
const unsigned long SEND_EVERY_MS = 5000;
const unsigned long MAX_PUMP_MS   = 60000;    // never run the pump longer than 60 s in one go
const unsigned long COOLDOWN_MS   = 600000;   // rest 10 minutes after a safety cut-off
const float LOCAL_ON_BELOW  = 30.0;           // fallback rules, used only when the server is unreachable
const float LOCAL_OFF_ABOVE = 50.0;

bool pumpOn = false;
unsigned long pumpStart = 0;
unsigned long lockedUntil = 0;
unsigned long lastSend = 0;

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
  long sum = 0;
  for (int i = 0; i < 10; i++) {          // average 10 samples to smooth out noise
    sum += analogRead(SENSOR_PIN);
    delay(5);
  }
  rawOut = sum / 10;
  float pct = (RAW_DRY - rawOut) * 100.0 / (RAW_DRY - RAW_WET);
  return constrain(pct, 0.0, 100.0);
}

// Returns 1 = server says pump ON, 0 = pump OFF, -1 = could not reach the server
int sendReading(float moisture, int raw) {
  if (WiFi.status() != WL_CONNECTED) return -1;
  HTTPClient http;
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  if (strlen(API_KEY) > 0) http.addHeader("X-API-Key", API_KEY);
  http.setTimeout(4000);
  String body = String("{\"device\":\"") + DEVICE_ID + "\",\"moisture\":" + String(moisture, 1) +
                ",\"raw\":" + raw + "}";
  int code = http.POST(body);
  int result = -1;
  if (code == 200) {
    String resp = http.getString();
    result = (resp.indexOf("\"pump\":true") >= 0) ? 1 : 0;
  }
  http.end();
  return result;
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  pinMode(RELAY_PIN, OUTPUT);
  setPump(false);                          // always start with the pump OFF

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting to Wi-Fi");
  unsigned long t = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t < 15000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("Connected. ESP32 address: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("Wi-Fi not connected yet. Using local rules until it connects.");
  }
}

void loop() {
  // Safety: cut the pump if it has been on too long
  if (pumpOn && millis() - pumpStart > MAX_PUMP_MS) {
    setPump(false);
    lockedUntil = millis() + COOLDOWN_MS;
    Serial.println("SAFETY: pump ran too long, switched off and resting.");
  }

  if (millis() - lastSend >= SEND_EVERY_MS) {
    lastSend = millis();
    if (WiFi.status() != WL_CONNECTED) WiFi.reconnect();   // non-blocking retry

    int raw;
    float moisture = readMoisture(raw);
    int answer = sendReading(moisture, raw);

    if (answer >= 0) {
      setPump(answer == 1);                // the server decides
    } else {                               // server unreachable: simple local rules
      if (moisture < LOCAL_ON_BELOW) setPump(true);
      else if (moisture > LOCAL_OFF_ABOVE) setPump(false);
    }

    Serial.printf("raw=%d  moisture=%.1f%%  pump=%s  server=%s\n", raw, moisture,
                  pumpOn ? "ON" : "OFF", answer >= 0 ? "ok" : "unreachable");
  }
}
