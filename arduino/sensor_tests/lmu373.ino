#include <Arduino_RouterBridge.h>

void setup() {
  Monitor.begin(115200);
}

void loop() {
  int lightVal = analogRead(A4);
  Monitor.print("lightVal: ");
  Monitor.println(lightVal);
  delay(200);
}
