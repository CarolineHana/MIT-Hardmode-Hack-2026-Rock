#include <SPI.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include "Adafruit_GC9A01A.h"
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>

// Display pins (single screen)
#define TFT_CS  10
#define TFT_DC   8
#define TFT_RST  9

Adafruit_GC9A01A display(TFT_CS, TFT_DC, TFT_RST);
Adafruit_MPU6050 mpu;

const int16_t W = 240, H = 240;
const int16_t EYE_R   = 55;
const int16_t PUPIL_R = 20;
const int16_t EYE_GAP = 10;

const float SCALE = 10.0f;
const float ALPHA = 0.60f;

float fx = 0.0f, fy = 0.0f;

void setup() {
  Serial.begin(115200);

  display.begin();
  display.setRotation(0);

  Wire.begin();
  if (!mpu.begin()) {
    Serial.println("MPU6050 not found");
    while (1) delay(10);
  }
  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
  mpu.setFilterBandwidth(MPU6050_BAND_260_HZ);
}

void loop() {
  sensors_event_t a, g, temp;
  mpu.getEvent(&a, &g, &temp);

  // 90° clockwise: (x, y) -> (y, -x)
  float ax = a.acceleration.y;
  float ay = -a.acceleration.x;

  float dx = -ax * SCALE;
  float dy =  ay * SCALE;

  int16_t maxOffset = EYE_R - PUPIL_R - 4;
  dx = constrain(dx, -maxOffset, maxOffset);
  dy = constrain(dy, -maxOffset, maxOffset);

  fx += ALPHA * (dx - fx);
  fy += ALPHA * (dy - fy);

  int16_t cy = H / 2;
  int16_t leftCx = (W / 4) - (EYE_GAP / 2);
  int16_t rightCx = (3 * W / 4) + (EYE_GAP / 2);

  int16_t leftPx = leftCx + (int16_t)fx;
  int16_t leftPy = cy + (int16_t)fy;
  int16_t rightPx = rightCx + (int16_t)fx;
  int16_t rightPy = cy + (int16_t)fy;

  display.fillScreen(0xFFFF);
  // Left eye
  display.fillCircle(leftCx, cy, EYE_R, 0xFFFF);
  display.drawCircle(leftCx, cy, EYE_R - 1, 0x0000);
  display.fillCircle(leftPx, leftPy, PUPIL_R, 0x0000);
  // Right eye
  display.fillCircle(rightCx, cy, EYE_R, 0xFFFF);
  display.drawCircle(rightCx, cy, EYE_R - 1, 0x0000);
  display.fillCircle(rightPx, rightPy, PUPIL_R, 0x0000);

  delay(1);
}
