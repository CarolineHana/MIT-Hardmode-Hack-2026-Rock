# Digital Twin — Arduino + Pi + Claude

A wearable signal-capture system. Arduino Uno collects IMU, light, and mic data and streams binary packets to a Raspberry Pi. When you hold a button, audio is recorded. On release, Whisper transcribes the audio and Claude maintains an in-memory "digital twin" that returns insights about your current state.

---

## Hardware

| Component | Arduino Pin |
|-----------|-------------|
| MAX4466 mic OUT | A0 |
| LM393 light OUT | A1 |
| Button (one leg to GND) | D2 (INPUT_PULLUP) |
| MPU6050 SDA | A4 |
| MPU6050 SCL | A5 |
| MPU6050 VCC | 3.3V |
| MPU6050 GND | GND |

> The MPU6050 AD0 pin should be tied to GND (I2C address 0x68).

---

## Arduino Setup

No external libraries required — MPU6050 is accessed via raw I2C registers.

**Upload options:**

1. Arduino IDE: open `arduino/uno_signals.ino`, select board "Arduino Uno", upload.
2. arduino-cli:
   ```bash
   arduino-cli compile --fqbn arduino:avr:uno arduino/uno_signals.ino
   arduino-cli upload  --fqbn arduino:avr:uno --port /dev/ttyACM0 arduino/uno_signals.ino
   ```

**Verify:** Open serial monitor at **250000 baud** — you'll see binary garbage (not human-readable text), which is correct.

---

## Pi Setup

### 1. Install system dependencies

```bash
sudo apt update
sudo apt install -y python3-pip ffmpeg
```

ffmpeg is required by Whisper for audio decoding.

### 2. Serial port permission

```bash
sudo usermod -a -G dialout $USER
# Log out and back in for the group change to take effect
```

### 3. Python dependencies

```bash
cd pi/
pip install -r requirements.txt
```

Whisper will download the `tiny` model (~73 MB) on first run.

### 4. Set API key

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

---

## Running

```bash
python3 pi/pi_main.py
# or specify a different port:
python3 pi/pi_main.py --port /dev/ttyUSB0
```

Common serial ports:
- `/dev/ttyACM0` — USB (ATmega16U2 or similar)
- `/dev/ttyUSB0` — USB-to-serial adapter (CH340, CP2102)

---

## Usage

1. Run `pi_main.py` — wait for `[Init] Whisper loaded` and `[Serial] Connected`
2. **Hold the button** — recording starts
3. **Speak** your question or observation (e.g., "Should I take the meeting or skip it?")
4. **Release the button** — Whisper transcribes, Claude responds within ~15s
5. The insight, updated state, and any new patterns print to the console
6. Hold the button again — Claude receives the full prior twin context

---

## Packet Protocol (Serial)

All packets are binary, no framing/checksum.

| Type byte | Payload | Total bytes |
|-----------|---------|-------------|
| `0x53` 'S' | 12B IMU (6×int16 LE) + 2B light (uint16 LE) + 1B button | 16 |
| `0x41` 'A' | 32 audio samples (uint8, 8-bit from `analogRead >> 2`) | 33 |
| `0x42` 'B' | 1B: 1=pressed, 0=released | 2 |

**Timing:**
- Idle: `S` packets at 20 Hz
- Recording: `A` packets at ~250 Hz (32 samples × 8kHz); `S` packet every 1s

---

## Architecture

```
Arduino Uno
  └── serial (250000 baud, binary) ──► Pi serial thread
                                            ├── sensor_q ──► twin.sensor_history
                                            ├── audio_q  ──► audio_buffer (while recording)
                                            └── event_q  ──► button press/release

Button release:
  audio_buffer
    └── resample 8kHz→16kHz (scipy)
    └── normalize uint8→float32
    └── Whisper tiny → transcript
    └── Claude claude-opus-4-6
          ├── input: twin state + transcript + sensor summary
          └── output: insight + updated_state + new_patterns
                └── twin.current_state updated in-memory
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `[Serial] Error: [Errno 13] Permission denied` | Run `sudo usermod -a -G dialout $USER` and re-login |
| `[Serial] Error: could not open port` | Check port with `ls /dev/tty*` before and after plugging Arduino |
| Empty transcript every time | Check MAX4466 wiring; verify A0 reads non-constant values in Arduino IDE Serial Plotter |
| MPU6050 reads all zeros | Confirm SDA/SCL on A4/A5; confirm VCC on 3.3V not 5V if your module lacks a regulator |
| Whisper slow on Pi | Model `tiny` is used; Pi 4 takes ~5s per transcription. Pi 3 may take longer. |
