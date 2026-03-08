#include <SPI.h>
#include <Adafruit_GFX.h>
#include "Adafruit_GC9A01A.h"

#define TFT_CS  10
#define TFT_DC   8
#define TFT_RST  9

Adafruit_GC9A01A display(TFT_CS, TFT_DC, TFT_RST);

void setup() {
  display.begin();
  display.setRotation(0);

  display.fillScreen(0xFFFF); // White
  int16_t cx = display.width() / 2;
  int16_t cy = display.height() / 2;
  int16_t r  = min(display.width(), display.height()) / 4;
  display.fillCircle(cx, cy, r, 0x0000); // Black
}

void loop() {}