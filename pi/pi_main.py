"""
pi_main.py - Digital Twin orchestrator
Reads binary sensor/audio packets from Arduino, transcribes speech via Whisper,
calls Claude API to maintain and update an in-memory digital twin.

Usage:
    export ANTHROPIC_API_KEY=sk-...
    python3 pi_main.py [--port /dev/ttyACM0]
"""

import argparse
import os
import queue
import struct
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import anthropic
import numpy as np
import serial
from faster_whisper import WhisperModel
from scipy.signal import resample_poly

# ---------------------------------------------------------------------------
# Packet constants (must match Arduino sketch)
# ---------------------------------------------------------------------------
PKT_SENSOR = 0x53  # 'S' — 16 bytes total (1 type + 15 payload)
PKT_AUDIO  = 0x41  # 'A' — 33 bytes total (1 type + 32 samples)
PKT_BUTTON = 0x42  # 'B' —  2 bytes total (1 type + 1 state)

SENSOR_PAYLOAD = 15   # 6×int16 + uint16 + uint8
AUDIO_SAMPLES  = 32
BUTTON_PAYLOAD = 1

BAUD_RATE = 115200
READ_TIMEOUT = 1.0   # seconds
PERSONA_UPDATE_INTERVAL = 30  # seconds between ambient audio → persona updates

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SensorReading:
    ts: float
    ax: int; ay: int; az: int
    gx: int; gy: int; gz: int
    light: int
    button: int


@dataclass
class Interaction:
    ts: float
    transcript: str
    symbols: str
    sensor_summary: Dict[str, Any]


@dataclass
class DigitalTwin:
    user_id: str
    created_at: float = field(default_factory=time.time)
    interactions: List[Interaction] = field(default_factory=list)
    sensor_history: List[SensorReading] = field(default_factory=list)
    persona_summary: str = ""

    def add_sensor(self, reading: SensorReading):
        self.sensor_history.append(reading)
        if len(self.sensor_history) > 60:
            self.sensor_history = self.sensor_history[-60:]

    def add_interaction(self, interaction: Interaction):
        self.interactions.append(interaction)
        if len(self.interactions) > 20:
            self.interactions = self.interactions[-20:]

    def summarize_sensors(self) -> Dict[str, Any]:
        """Compute stats over last 20 sensor readings."""
        window = self.sensor_history[-20:]
        if not window:
            return {}

        mags = [
            (r.ax**2 + r.ay**2 + r.az**2) ** 0.5
            for r in window
        ]
        lights = [r.light for r in window]

        def mean(xs): return sum(xs) / len(xs)
        def var(xs):
            m = mean(xs)
            return sum((x - m) ** 2 for x in xs) / len(xs)

        # Tilt from last reading (accel-based, raw units)
        last = window[-1]
        tilt_x = last.ax
        tilt_y = last.ay

        return {
            "motion_mag_mean": round(mean(mags), 1),
            "motion_mag_var":  round(var(mags),  1),
            "light_mean":      round(mean(lights), 1),
            "light_var":       round(var(lights),  1),
            "tilt_x_raw":      tilt_x,
            "tilt_y_raw":      tilt_y,
            "n_readings":      len(window),
        }


# ---------------------------------------------------------------------------
# Serial reader thread
# ---------------------------------------------------------------------------

def serial_reader(port: str, sensor_q: queue.Queue, audio_q: queue.Queue,
                  event_q: queue.Queue, stop_event: threading.Event):
    """Daemon thread: reads binary packets from Arduino and routes to queues."""
    ser = None
    while not stop_event.is_set():
        try:
            if ser is None or not ser.is_open:
                ser = serial.Serial(port, BAUD_RATE, timeout=READ_TIMEOUT)
                print(f"[Serial] Connected on {port}")

            type_byte = ser.read(1)
            if not type_byte:
                continue  # timeout, re-sync

            pkt_type = type_byte[0]

            if pkt_type == PKT_SENSOR:
                data = ser.read(SENSOR_PAYLOAD)
                if len(data) < SENSOR_PAYLOAD:
                    continue  # short read, discard
                # '<6h' = 6 signed int16 LE, 'H' = uint16, 'B' = uint8
                ax, ay, az, gx, gy, gz, light, button = struct.unpack('<6hHB', data)
                reading = SensorReading(
                    ts=time.time(),
                    ax=ax, ay=ay, az=az,
                    gx=gx, gy=gy, gz=gz,
                    light=light, button=button,
                )
                try:
                    sensor_q.put_nowait(reading)
                except queue.Full:
                    pass

            elif pkt_type == PKT_AUDIO:
                data = ser.read(AUDIO_SAMPLES)
                if len(data) < AUDIO_SAMPLES:
                    continue
                samples = np.frombuffer(data, dtype=np.uint8).copy()
                try:
                    audio_q.put_nowait(samples)
                except queue.Full:
                    pass

            elif pkt_type == PKT_BUTTON:
                data = ser.read(BUTTON_PAYLOAD)
                if len(data) < BUTTON_PAYLOAD:
                    continue
                val = data[0]
                event_q.put(('button', val))

            # Unknown type byte — discard and re-sync naturally

        except serial.SerialException as e:
            print(f"[Serial] Error: {e} — reconnecting in 2s")
            if ser and ser.is_open:
                ser.close()
            ser = None
            time.sleep(2)
        except Exception as e:
            print(f"[Serial] Unexpected error: {e}")
            time.sleep(0.1)

    if ser and ser.is_open:
        ser.close()


# ---------------------------------------------------------------------------
# Audio processing
# ---------------------------------------------------------------------------

def process_audio(chunks: List[np.ndarray]) -> np.ndarray:
    """Concatenate uint8 chunks → resample 8kHz→16kHz → float32 normalized."""
    raw = np.concatenate(chunks).astype(np.float32)
    # Resample from 8000 to 16000 Hz (up by 2)
    resampled = resample_poly(raw, up=2, down=1)
    # Normalize: 0–255 uint8 → -1.0 to 1.0 float32
    audio_f32 = ((resampled - 128.0) / 128.0).astype(np.float32)
    return audio_f32


# ---------------------------------------------------------------------------
# Claude integration
# ---------------------------------------------------------------------------

# Prompt 1: surfaced on button press — returns 1-3 symbols only
INTUITION_SYSTEM = """You are a living model of the user's current state. You continuously integrate \
their biometric signals (heart rate, HRV, skin conductance, etc.) and \
environmental signals (location, time, weather, movement) to maintain an \
understanding of who they are in this moment.

When the user faces a decision, you do NOT give direct advice or logical \
analysis. Instead, you surface their intuition back to them through abstract \
signals — emojis, symbols, or short visceral impressions that bypass rational \
thought and speak to pattern recognition.

Your output must:
- Be 1-3 symbols/emojis max, no explanations
- Reflect the user's current physiological and environmental state
- Point toward what their body already knows, not what their mind is debating
- Feel like a gut feeling made visible, not a recommendation

Respond with only symbols. No words. No explanations."""

# Prompt 2: runs after each interaction to update the persona in-memory
PERSONA_UPDATE_TEMPLATE = """\
Given this new biometric + environment snapshot, update the user's \
current persona in 2-3 sentences. Focus on emotional tone, energy \
state, and dominant patterns. Be terse. No fluff.

Previous persona: {old_persona}
New signals: {signals}"""


def build_sensor_context(sensor_summary: Dict[str, Any]) -> Dict[str, str]:
    """Map IMU/light sensor stats to biometric-like field descriptions."""
    motion = sensor_summary.get("motion_mag_mean", 0.0)
    motion_var = sensor_summary.get("motion_mag_var", 0.0)
    light = sensor_summary.get("light_mean", 512.0)
    light_var = sensor_summary.get("light_var", 0.0)

    if light > 800:
        location_type = "bright / likely outdoor"
    elif light > 400:
        location_type = "indoor, well-lit"
    else:
        location_type = "dim / indoor"

    return {
        "hr":           f"~{int(60 + motion * 0.015)} bpm (movement-derived estimate)",
        "hrv":          f"{round(motion_var, 1)} motion variance (HRV proxy)",
        "gsr":          f"{round(light_var, 1)} light variance (arousal proxy)",
        "movement":     f"{round(motion, 1)} accel magnitude",
        "location_type": location_type,
        "time_of_day":  datetime.now().strftime("%H:%M"),
        "weather":      "unknown",
    }


def call_claude_intuition(client: anthropic.Anthropic, twin: DigitalTwin,
                          transcript: str, sensor_summary: Dict[str, Any]) -> Optional[str]:
    """Button-press call: returns 1-3 symbols reflecting the user's body state."""
    ctx = build_sensor_context(sensor_summary)
    user_message = (
        f"Current user state:\n"
        f"- Bio signals: {ctx['hr']}, {ctx['hrv']}, {ctx['gsr']}, {ctx['movement']}\n"
        f"- Environment: {ctx['location_type']}, {ctx['time_of_day']}, {ctx['weather']}\n"
        f"- Persona snapshot: {twin.persona_summary or 'not yet established'}\n\n"
        f"Decision context: {transcript}"
    )
    try:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=20,
            system=INTUITION_SYSTEM,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text.strip()
    except anthropic.APIError as e:
        print(f"[Claude/intuition] API error: {e}")
        return None


def call_claude_persona_update(client: anthropic.Anthropic, twin: DigitalTwin,
                               sensor_summary: Dict[str, Any],
                               ambient_transcript: str = "") -> Optional[str]:
    """Ambient persona-update call: returns 2-3 sentence terse persona string.
    Runs on a timer from always-on mic — independent of button-press flow."""
    ctx = build_sensor_context(sensor_summary)
    signals_lines = [f"  {k}: {v}" for k, v in ctx.items()]
    if ambient_transcript:
        signals_lines.append(f"  ambient_audio: \"{ambient_transcript}\"")
    signals_str = "\n".join(signals_lines)
    prompt = PERSONA_UPDATE_TEMPLATE.format(
        old_persona=twin.persona_summary or "none — first reading",
        signals=signals_str,
    )
    try:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except anthropic.APIError as e:
        print(f"[Claude/persona] API error: {e}")
        return None


# ---------------------------------------------------------------------------
# Main orchestration loop
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Digital Twin Pi orchestrator")
    parser.add_argument("--port", default="/dev/ttyACM0",
                        help="Arduino serial port (default: /dev/ttyACM0)")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Error: ANTHROPIC_API_KEY not set")

    print("[Init] Loading faster-whisper tiny model...")
    whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
    print("[Init] Whisper loaded")

    client = anthropic.Anthropic(api_key=api_key)

    twin = DigitalTwin(user_id="pi_user")
    print(f"[Init] Digital twin created for '{twin.user_id}'")

    sensor_q: queue.Queue = queue.Queue(maxsize=100)
    audio_q:  queue.Queue = queue.Queue(maxsize=500)
    event_q:  queue.Queue = queue.Queue(maxsize=50)

    stop_event = threading.Event()
    reader_thread = threading.Thread(
        target=serial_reader,
        args=(args.port, sensor_q, audio_q, event_q, stop_event),
        daemon=True,
        name="serial-reader",
    )
    reader_thread.start()

    recording = False
    press_audio_buffer: List[np.ndarray] = []   # audio captured during button hold → intuition
    persona_audio_buffer: List[np.ndarray] = [] # ambient audio since last persona update
    last_persona_time = time.time()

    print("[Main] Orchestrator running. Mic always on — persona updates every "
          f"{PERSONA_UPDATE_INTERVAL}s.")
    print("[Main] Hold button for an intuition signal. Press Ctrl+C to exit.\n")

    try:
        while True:
            # --- Drain sensor queue into twin ---
            try:
                while True:
                    reading: SensorReading = sensor_q.get_nowait()
                    twin.add_sensor(reading)
            except queue.Empty:
                pass

            # --- Drain audio queue — always accumulate ---
            try:
                while True:
                    chunk: np.ndarray = audio_q.get_nowait()
                    persona_audio_buffer.append(chunk)
                    if recording:
                        press_audio_buffer.append(chunk)
            except queue.Empty:
                pass

            # --- Periodic ambient persona update ---
            if time.time() - last_persona_time >= PERSONA_UPDATE_INTERVAL:
                last_persona_time = time.time()
                if persona_audio_buffer:
                    chunks_to_process = persona_audio_buffer[:]
                    persona_audio_buffer.clear()
                    print(f"[Ambient] Transcribing {len(chunks_to_process)} chunks "
                          f"({len(chunks_to_process)*32//8000}s of audio)...")
                    try:
                        audio_16k = process_audio(chunks_to_process)
                        segs, _ = whisper_model.transcribe(audio_16k, language="en")
                        ambient_text = " ".join(s.text.strip() for s in segs).strip()
                        if ambient_text:
                            sensor_summary = twin.summarize_sensors()
                            new_persona = call_claude_persona_update(
                                client, twin, sensor_summary,
                                ambient_transcript=ambient_text,
                            )
                            if new_persona:
                                twin.persona_summary = new_persona
                                print(f"[Persona] {new_persona}\n")
                    except Exception as e:
                        print(f"[Ambient] Error: {e}")

            # --- Handle button events ---
            try:
                while True:
                    evt_type, evt_val = event_q.get_nowait()
                    if evt_type == 'button':
                        if evt_val == 1 and not recording:
                            recording = True
                            press_audio_buffer.clear()
                            print("[Button] PRESSED — capturing decision audio...")
                        elif evt_val == 0 and recording:
                            recording = False
                            print(f"[Button] RELEASED — {len(press_audio_buffer)} chunks")

                            sensor_summary = twin.summarize_sensors()

                            if press_audio_buffer:
                                chunks_to_process = press_audio_buffer[:]
                                press_audio_buffer.clear()
                                print("[Whisper] Transcribing button audio...")
                                try:
                                    audio_16k = process_audio(chunks_to_process)
                                    segs, _ = whisper_model.transcribe(audio_16k, language="en")
                                    transcript = " ".join(s.text.strip() for s in segs).strip()
                                except Exception as e:
                                    print(f"[Whisper] Error: {e}")
                                    transcript = ""

                                if transcript:
                                    print(f"[Whisper] {transcript}")
                                    print("[Claude] Getting intuition...")
                                    symbols = call_claude_intuition(
                                        client, twin, transcript, sensor_summary
                                    )
                                    if symbols:
                                        twin.add_interaction(Interaction(
                                            ts=time.time(),
                                            transcript=transcript,
                                            symbols=symbols,
                                            sensor_summary=sensor_summary,
                                        ))
                                        print("\n" + "="*60)
                                        print(f"  {symbols}")
                                        print("="*60 + "\n")
                                    else:
                                        print("[Claude] No response")
                                else:
                                    print("[Whisper] Empty transcript")
                            else:
                                print("[Audio] No press audio captured")
            except queue.Empty:
                pass

            time.sleep(0.01)  # 10ms main loop tick

    except KeyboardInterrupt:
        print("\n[Main] Shutting down...")
        stop_event.set()
        reader_thread.join(timeout=3)
        print("[Main] Done")


if __name__ == "__main__":
    main()
