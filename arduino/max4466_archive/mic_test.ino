/*
 * mic_test.ino
 * Streams MAX4466 mic audio over Bridge for STT testing.
 * Target: Arduino UNO Q MCU
 *
 * Wiring:
 *   MAX4466 OUT → A0
 *   MAX4466 VCC → 3.3V
 *   MAX4466 GND → GND
 *
 * Packet: "B:" + base64(uint8 PCM @ 8kHz, 20ms frames)
 */
#include <Arduino_RouterBridge.h>

#define AUDIO_PIN           A0
#define SAMPLE_RATE_HZ      8000
#define SAMPLE_PERIOD_US    (1000000 / SAMPLE_RATE_HZ)
#define FRAME_SAMPLES       160   // 20ms @ 8kHz

static uint8_t audioBuf[FRAME_SAMPLES];

static const char B64_TABLE[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

size_t base64_encode(const uint8_t *in, size_t len, char *out) {
  size_t i = 0, o = 0;
  while (i < len) {
    size_t rem = len - i;
    uint32_t a = in[i++];
    uint32_t b = (rem > 1) ? in[i++] : 0;
    uint32_t c = (rem > 2) ? in[i++] : 0;
    uint32_t triple = (a << 16) | (b << 8) | c;
    out[o++] = B64_TABLE[(triple >> 18) & 0x3F];
    out[o++] = B64_TABLE[(triple >> 12) & 0x3F];
    out[o++] = (rem > 1) ? B64_TABLE[(triple >> 6) & 0x3F] : '=';
    out[o++] = (rem > 2) ? B64_TABLE[triple & 0x3F] : '=';
  }
  out[o] = '\0';
  return o;
}

void setup() {
  Monitor.begin(115200);
  if (!Bridge.begin()) {
    Monitor.println("Bridge init failed");
    while (1) delay(100);
  }
}

void loop() {
  static unsigned long lastSampleTime = 0;
  static uint16_t sampleIdx = 0;

  unsigned long now = micros();
  if (now - lastSampleTime >= SAMPLE_PERIOD_US) {
    lastSampleTime += SAMPLE_PERIOD_US;  // reduce drift
    // 12-bit ADC -> 8-bit unsigned PCM
    audioBuf[sampleIdx++] = (uint8_t)(analogRead(AUDIO_PIN) >> 4);
    if (sampleIdx >= FRAME_SAMPLES) {
      sampleIdx = 0;
      // Quick range debug (min,max) to verify signal is changing.
      uint8_t mn = 255, mx = 0;
      for (int i = 0; i < FRAME_SAMPLES; i++) {
        if (audioBuf[i] < mn) mn = audioBuf[i];
        if (audioBuf[i] > mx) mx = audioBuf[i];
      }
      String dbg = "R:" + String(mn) + "," + String(mx);
      Bridge.call("mcu_line", dbg);

      // Base64 encode the frame and send as "B:<base64>"
      const size_t b64_len = 4 * ((FRAME_SAMPLES + 2) / 3);
      static char b64[b64_len + 1];
      base64_encode(audioBuf, FRAME_SAMPLES, b64);
      String line;
      line.reserve(2 + b64_len);
      line = "B:";
      line += b64;
      Bridge.call("mcu_line", line);
    }
  }
}
