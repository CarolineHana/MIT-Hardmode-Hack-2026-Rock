  #include <Arduino_GFX_Library.h>
  #include <Adafruit_GFX.h>  // for GFXcanvas16
  #include <Wire.h>
  #include <Arduino_RouterBridge.h>
  #include <Adafruit_MPU6050.h>
  #include <Adafruit_Sensor.h>

  // Display A pins
  #define TFTA_CS  10
  #define TFTA_DC   8
  #define TFTA_RST  9

  // Display B pins
  #define TFTB_CS   7
  #define TFTB_DC   6
  #define TFTB_RST  5

  Arduino_DataBus *busA = new Arduino_HWSPI(TFTA_DC, TFTA_CS);
  Arduino_GFX *gfxA = new Arduino_GC9A01(busA, TFTA_RST, 0, true);

  Arduino_DataBus *busB = new Arduino_HWSPI(TFTB_DC, TFTB_CS);
  Arduino_GFX *gfxB = new Arduino_GC9A01(busB, TFTB_RST, 0, true);

  Adafruit_MPU6050 mpu;

  const int16_t W = 240, H = 240;
  const int16_t EYE_R   = 120;
  const int16_t PUPIL_R = 45;

  const float SCALE = 10.0f;
  const float ALPHA = 0.60f;

  float fx = 0.0f, fy = 0.0f;
  GFXcanvas16 canvas(W, H);

  bool drawA = true;

  void setup() {
    Monitor.begin(115200);

    if (!gfxA->begin() || !gfxB->begin()) {
      Monitor.println("gfx begin failed");
      while (1) delay(10);
    }
    gfxA->setRotation(0);
    gfxB->setRotation(0);

    Wire.begin();
    if (!mpu.begin()) {
      Monitor.println("MPU6050 not found");
      while (1) delay(10);
    }
    mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
    mpu.setFilterBandwidth(MPU6050_BAND_260_HZ);

    if (!canvas.getBuffer()) {
      Monitor.println("canvas alloc failed");
      while (1) delay(10);
    }
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

    int16_t cx = W / 2;
    int16_t cy = H / 2;
    int16_t px = cx + (int16_t)fx;
    int16_t py = cy + (int16_t)fy;

    canvas.fillScreen(0xFFFF);
    canvas.fillCircle(cx, cy, EYE_R, 0xFFFF);
    canvas.drawCircle(cx, cy, EYE_R - 1, 0x0000);
    canvas.fillCircle(px, py, PUPIL_R, 0x0000);

    if (drawA) {
      gfxA->draw16bitRGBBitmap(0, 0, canvas.getBuffer(), W, H);
    } else {
      gfxB->draw16bitRGBBitmap(0, 0, canvas.getBuffer(), W, H);
    }
    drawA = !drawA;

    delay(1);
  }