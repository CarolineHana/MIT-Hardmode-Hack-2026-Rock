#define samples (256)
#include <Arduino_RouterBridge.h>
int audioInRaw;
int audioInRectified;

void setup()
{
  Monitor.begin(115200);
  //analogReference(EXTERNAL);
}

void loop()
{
  audioIn();
}

void audioIn()
{
  long audioAverage = 0, audioMax = 0, audioMin = 1024;

  for (int i = 0; i < samples; i++)
  {
    int audioInRaw = analogRead(A4);

    audioInRectified = abs(audioInRaw - 337); // level shift for 3,3V
    audioMin = min(audioMin, audioInRaw);
    audioMax = max(audioMax, audioInRaw);
    audioAverage += audioInRaw;
  }

  audioAverage /= samples;

  Monitor.print("audioInRectified:"); Monitor.print(audioInRectified); Monitor.print(" ");
  Monitor.print("audioMin:"); Monitor.print(audioMin); Monitor.print(" ");
  Monitor.print("audioMax:"); Monitor.print(audioMax); Monitor.print(" ");
  Monitor.print("audioAverage:"); Monitor.print(audioAverage); Monitor.print(" ");
  Monitor.print("audioPeakToPeak:"); Monitor.println(audioMax - audioMin);
}