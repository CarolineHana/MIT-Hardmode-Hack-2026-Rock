// Basic demo for accelerometer readings from Adafruit MPU6050
#include <Arduino_RouterBridge.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Wire.h>

Adafruit_MPU6050 mpu;

void setup(void) {
  Monitor.begin(115200);
  while (!Monitor)
    delay(10); // will pause Zero, Leonardo, etc until Monitor console opens

  Monitor.println("Adafruit MPU6050 test!");

  // Try to initialize!
  if (!mpu.begin()) {
    Monitor.println("Failed to find MPU6050 chip");
    while (1) {
      delay(10);
    }
  }
  Monitor.println("MPU6050 Found!");

  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
  Monitor.print("Accelerometer range set to: ");
  switch (mpu.getAccelerometerRange()) {
  case MPU6050_RANGE_2_G:
    Monitor.println("+-2G");
    break;
  case MPU6050_RANGE_4_G:
    Monitor.println("+-4G");
    break;
  case MPU6050_RANGE_8_G:
    Monitor.println("+-8G");
    break;
  case MPU6050_RANGE_16_G:
    Monitor.println("+-16G");
    break;
  }
  mpu.setGyroRange(MPU6050_RANGE_500_DEG);
  Monitor.print("Gyro range set to: ");
  switch (mpu.getGyroRange()) {
  case MPU6050_RANGE_250_DEG:
    Monitor.println("+- 250 deg/s");
    break;
  case MPU6050_RANGE_500_DEG:
    Monitor.println("+- 500 deg/s");
    break;
  case MPU6050_RANGE_1000_DEG:
    Monitor.println("+- 1000 deg/s");
    break;
  case MPU6050_RANGE_2000_DEG:
    Monitor.println("+- 2000 deg/s");
    break;
  }

  mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);
  Monitor.print("Filter bandwidth set to: ");
  switch (mpu.getFilterBandwidth()) {
  case MPU6050_BAND_260_HZ:
    Monitor.println("260 Hz");
    break;
  case MPU6050_BAND_184_HZ:
    Monitor.println("184 Hz");
    break;
  case MPU6050_BAND_94_HZ:
    Monitor.println("94 Hz");
    break;
  case MPU6050_BAND_44_HZ:
    Monitor.println("44 Hz");
    break;
  case MPU6050_BAND_21_HZ:
    Monitor.println("21 Hz");
    break;
  case MPU6050_BAND_10_HZ:
    Monitor.println("10 Hz");
    break;
  case MPU6050_BAND_5_HZ:
    Monitor.println("5 Hz");
    break;
  }

  Monitor.println("");
  delay(100);
}

void loop() {

  /* Get new sensor events with the readings */
  sensors_event_t a, g, temp;
  mpu.getEvent(&a, &g, &temp);

  /* Print out the values */
  Monitor.print("Acceleration X: ");
  Monitor.print(a.acceleration.x);
  Monitor.print(", Y: ");
  Monitor.print(a.acceleration.y);
  Monitor.print(", Z: ");
  Monitor.print(a.acceleration.z);
  Monitor.println(" m/s^2");

  Monitor.print("Rotation X: ");
  Monitor.print(g.gyro.x);
  Monitor.print(", Y: ");
  Monitor.print(g.gyro.y);
  Monitor.print(", Z: ");
  Monitor.print(g.gyro.z);
  Monitor.println(" rad/s");

  Monitor.print("Temperature: ");
  Monitor.print(temp.temperature);
  Monitor.println(" degC");

  Monitor.println("");
  delay(500);
}