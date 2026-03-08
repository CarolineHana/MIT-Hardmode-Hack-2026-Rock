/*
 * uno_signals.ino
 * Digital Twin - Arduino sensor capture
 * Target: Arduino UNO R4 (Renesas RA4M1)
 *
 * Pins:
 *   A0 - MAX4466 mic (audio)
 *   A1 - LM393 light sensor
 *   D2 - Button (INPUT_PULLUP, active LOW)
 *   A4 - MPU6050 SDA
 *   A5 - MPU6050 SCL
 *
 * Serial: 115200 baud, binary packets
 *   'S' (0x53): 1 + 12 + 2 + 1 = 16 bytes total
 *   'A' (0x41): 1 + 32 = 33 bytes total
 *   'B' (0x42): 1 + 1 = 2 bytes total
 *
 * ADC: 14-bit resolution (analogReadResolution(14))
 *   Audio: analogRead(A0) >> 6  (14-bit → 8-bit)
 *   Light: analogRead(A1)       (0–16383, fits uint16)
 */

#include <Wire.h>

// MPU6050 I2C address and registers
#define MPU6050_ADDR   0x68
#define REG_PWR_MGMT_1 0x6B
#define REG_ACCEL_XOUT 0x3B
#define REG_GYRO_XOUT  0x43

// Packet type bytes
#define PKT_SENSOR 0x53  // 'S'
#define PKT_AUDIO  0x41  // 'A'
#define PKT_BUTTON 0x42  // 'B'

// Timing
#define SENSOR_INTERVAL_MS  50   // 20 Hz
#define AUDIO_SAMPLE_COUNT  32
// ~8kHz audio: 32 samples * 125us each = 4ms per audio packet

// Button
#define BUTTON_PIN    2
#define DEBOUNCE_MS   20

// State
bool recording = false;
bool lastButtonState = HIGH;
bool buttonState = HIGH;
unsigned long lastDebounceTime = 0;

// Sensor timing
unsigned long lastSensorTime = 0;

// MPU6050 data
int16_t ax, ay, az, gx, gy, gz;

// Audio buffer
uint8_t audioBuf[AUDIO_SAMPLE_COUNT];

void setup() {
  Serial.begin(115200);
  analogReadResolution(14);  // UNO R4: 14-bit ADC (0–16383)
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  Wire.begin();
  Wire.setClock(400000);  // 400kHz fast mode

  // Wake MPU6050
  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(REG_PWR_MGMT_1);
  Wire.write(0x00);  // clear sleep bit
  Wire.endTransmission();

  delay(100);  // MPU6050 startup
}

void readMPU6050() {
  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(REG_ACCEL_XOUT);
  Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)MPU6050_ADDR, (size_t)14, true);

  ax = (Wire.read() << 8) | Wire.read();
  ay = (Wire.read() << 8) | Wire.read();
  az = (Wire.read() << 8) | Wire.read();
  Wire.read(); Wire.read();  // skip temperature
  gx = (Wire.read() << 8) | Wire.read();
  gy = (Wire.read() << 8) | Wire.read();
  gz = (Wire.read() << 8) | Wire.read();
}

void sendSensorPacket() {
  readMPU6050();
  uint16_t light = (uint16_t)analogRead(A1);
  uint8_t btn = recording ? 1 : 0;

  uint8_t buf[16];
  buf[0] = PKT_SENSOR;
  // 6 x int16 LE: ax, ay, az, gx, gy, gz
  buf[1]  = (uint8_t)(ax & 0xFF); buf[2]  = (uint8_t)(ax >> 8);
  buf[3]  = (uint8_t)(ay & 0xFF); buf[4]  = (uint8_t)(ay >> 8);
  buf[5]  = (uint8_t)(az & 0xFF); buf[6]  = (uint8_t)(az >> 8);
  buf[7]  = (uint8_t)(gx & 0xFF); buf[8]  = (uint8_t)(gx >> 8);
  buf[9]  = (uint8_t)(gy & 0xFF); buf[10] = (uint8_t)(gy >> 8);
  buf[11] = (uint8_t)(gz & 0xFF); buf[12] = (uint8_t)(gz >> 8);
  // uint16 LE: light
  buf[13] = (uint8_t)(light & 0xFF); buf[14] = (uint8_t)(light >> 8);
  // button state
  buf[15] = btn;

  Serial.write(buf, 16);
}

void sendButtonPacket(uint8_t state) {
  uint8_t buf[2] = { PKT_BUTTON, state };
  Serial.write(buf, 2);
}

void sendAudioPacket(uint8_t* samples) {
  Serial.write(PKT_AUDIO);
  Serial.write(samples, AUDIO_SAMPLE_COUNT);
}

void handleButton() {
  bool reading = digitalRead(BUTTON_PIN);  // LOW when pressed

  if (reading != lastButtonState) {
    lastDebounceTime = millis();
  }
  lastButtonState = reading;

  if ((millis() - lastDebounceTime) >= DEBOUNCE_MS) {
    bool pressed = (reading == LOW);
    if (pressed != !buttonState) {  // state changed
      buttonState = reading;
      if (pressed) {
        recording = true;
        sendButtonPacket(1);
      } else {
        recording = false;
        sendButtonPacket(0);
      }
    }
  }
}

// Collect 32 audio samples at ~8kHz (125us between samples)
// Uses micros() for timing — no delay()
// UNO R4: ADC is 14-bit → shift right 6 to get 8-bit (0–255)
void collectAndSendAudio() {
  static unsigned long lastSampleTime = 0;
  static uint8_t sampleIdx = 0;

  unsigned long now = micros();
  if (now - lastSampleTime >= 125) {  // 8kHz
    lastSampleTime = now;
    audioBuf[sampleIdx++] = (uint8_t)(analogRead(A0) >> 6);
    if (sampleIdx >= AUDIO_SAMPLE_COUNT) {
      sampleIdx = 0;
      sendAudioPacket(audioBuf);
    }
  }
}

void loop() {
  unsigned long now = millis();

  handleButton();

  // Mic is always on — audio packets stream continuously
  collectAndSendAudio();

  // Sensor packet at 20Hz always
  if (now - lastSensorTime >= SENSOR_INTERVAL_MS) {
    lastSensorTime = now;
    sendSensorPacket();
  }
}
