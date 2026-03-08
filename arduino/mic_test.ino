/*
 * mic_test.ino
 * Streams MAX4466 mic audio over serial for STT testing.
 * Target: Arduino UNO R4 (Renesas RA4M1)
 *
 * Wiring:
 *   MAX4466 OUT → A0
 *   MAX4466 VCC → 3.3V
 *   MAX4466 GND → GND
 *
 * Serial: 115200 baud
 * Packet: 'A' (0x41) + 32 uint8 samples @ ~8kHz
 *         ADC 14-bit → 8-bit via >> 6
 */
#include <Arduino_RouterBridge.h>

#define AUDIO_PIN         A0
#define AUDIO_SAMPLE_COUNT 32
#define PKT_AUDIO         0x41

uint8_t audioBuf[AUDIO_SAMPLE_COUNT];

void setup() {
  Monitor.begin(115200);
  Bridge.begin(115200);
}

void loop() {
  static unsigned long lastSampleTime = 0;
  static unsigned long lastPingTime   = 0;
  static uint8_t sampleIdx = 0;

  // Send PING once per second so Python confirms Bridge is alive
  unsigned long now2 = millis();
  if (now2 - lastPingTime >= 1000) {
    lastPingTime = now2;
    Bridge.call("mcu_line", "PING");
  }

  unsigned long now = micros();  // audio sample timer
  if (now - lastSampleTime >= 125) {  // 8kHz
    lastSampleTime = now;
    audioBuf[sampleIdx++] = (uint8_t)(analogRead(AUDIO_PIN) >> 2);
    if (sampleIdx >= AUDIO_SAMPLE_COUNT) {
      sampleIdx = 0;
      // Build line and send via both Monitor (serial display) and Bridge (Python callback)
      String line = "A:";
      for (int i = 0; i < AUDIO_SAMPLE_COUNT; i++) {
        line += String(audioBuf[i]);
        if (i < AUDIO_SAMPLE_COUNT - 1) line += ",";
      }
      Monitor.println(line);
      Bridge.call("mcu_line", line);
    }
  }
}
