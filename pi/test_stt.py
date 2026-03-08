"""
test_stt.py — Continuous speech-to-text test
Reads mic audio from Arduino UNO Q MCU via Bridge, transcribes via OpenAI Whisper API.
Uses only stdlib + arduino.app_utils — no numpy, no pydantic, no compiled extensions.
"""

import array
import io
import json
import math
import os
import sys
import time
import traceback
import urllib.error
import urllib.request
import wave

from arduino.app_utils import Bridge, App

AUDIO_SAMPLES       = 32
ARDUINO_SAMPLE_RATE = 8000
WHISPER_SAMPLE_RATE = 16000

OPENAI_API_KEY = "YOUR_KEY_HERE"  # ← paste your real key here (do not commit)

# VAD thresholds — tune if it triggers too easily or misses speech
SPEECH_THRESHOLD   = 6    # std above this = speech detected
SILENCE_THRESHOLD  = 4.5  # std below this = silence
SILENCE_PACKETS    = 50   # ~0.2s of silence before transcribing
MIN_SPEECH_PACKETS = 10   # minimum packets to bother transcribing
PRE_ROLL_PACKETS   = 50   # ~0.2s of audio kept before speech starts
MAX_RECORD_PACKETS = 2500 # ~10s max recording before forced transcription

_pre_roll      = []   # rolling window before speech
_speech_buf    = []   # audio during speech
_speaking      = False
_silence_count = 0


def transcribe(wav_bytes: bytes, api_key: str) -> str:
    """Call Whisper API with urllib — no openai SDK, no pydantic."""
    boundary = "----ArduinoSTTBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\n'
        f"whisper-1\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="language"\r\n\r\n'
        f"en\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode() + wav_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read()).get("text", "").strip()


def build_wav(chunks: list) -> bytes:
    """uint8 bytes @ 8kHz → WAV @ 16kHz. Pure Python, no numpy."""
    raw = b''.join(chunks)
    upsampled = array.array('h')
    for i in range(len(raw) - 1):
        a, b = raw[i], raw[i + 1]
        upsampled.append((a - 128) * 256)
        upsampled.append(((a + b) // 2 - 128) * 256)
    upsampled.append((raw[-1] - 128) * 256)
    upsampled.append((raw[-1] - 128) * 256)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(WHISPER_SAMPLE_RATE)
        wf.writeframes(upsampled.tobytes())
    return buf.getvalue()


def packet_std(samples: bytes) -> float:
    mean = sum(samples) / len(samples)
    return math.sqrt(sum((x - mean) ** 2 for x in samples) / len(samples))


_packet_count = 0

def mcu_line(msg: str):
    global _pre_roll, _speech_buf, _speaking, _silence_count, _packet_count
    line = msg.strip()

    _packet_count += 1

    # 1. Confirm mcu_line is being called at all
    if _packet_count <= 3:
        print(f"[DEBUG] mcu_line called #{_packet_count}: {line[:40]}")

    if _packet_count % 250 == 0:
        print(f"[DEBUG] {_packet_count} packets, speaking={_speaking}, speech_buf={len(_speech_buf)}")

    if not line.startswith("A:"):
        print(f"[BRIDGE] received: {line[:60]}")
        return

    try:
        samples = bytes([int(x) for x in line[2:].split(",")])
    except ValueError as e:
        print(f"[DEBUG] parse error: {e} on line: {line[:40]}")
        return

    if len(samples) != AUDIO_SAMPLES:
        print(f"[DEBUG] wrong sample count: {len(samples)}")
        return

    std = packet_std(samples)

    if not _speaking:
        _pre_roll.append(samples)
        if len(_pre_roll) > PRE_ROLL_PACKETS:
            _pre_roll.pop(0)

        if _packet_count % 50 == 0:
            print(f"[idle] std={std:.1f} (trigger>{SPEECH_THRESHOLD})", flush=True)

        if std > SPEECH_THRESHOLD:
            _speaking = True
            _silence_count = 0
            _speech_buf = list(_pre_roll)
            print(f"[SPEECH DETECTED] std={std:.1f} — recording...", flush=True)
    else:
        _speech_buf.append(samples)

        if std < SILENCE_THRESHOLD:
            _silence_count += 1
        else:
            _silence_count = 0

        dur = len(_speech_buf) * AUDIO_SAMPLES / ARDUINO_SAMPLE_RATE
        if len(_speech_buf) % 50 == 0:
            print(f"[recording] {dur:.1f}s  std={std:.1f}  silence_count={_silence_count}", flush=True)

        if _silence_count >= SILENCE_PACKETS or len(_speech_buf) >= MAX_RECORD_PACKETS:
            reason = "silence" if _silence_count >= SILENCE_PACKETS else "max duration"
            print(f"[STOP] {reason} — {len(_speech_buf)} packets ({dur:.1f}s)")
            _speaking      = False
            chunks         = _speech_buf[:]
            _speech_buf    = []
            _pre_roll      = []
            _silence_count = 0

            if len(chunks) < MIN_SPEECH_PACKETS:
                print("[SKIP] too short\n")
                return

            print(f"[TRANSCRIBE] building wav from {len(chunks)} chunks...")
            wav       = build_wav(chunks)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            wav_path  = f"/app/python/clip_{timestamp}.wav"
            with open(wav_path, "wb") as f:
                f.write(wav)
            print(f"[FILE] wav saved to {wav_path}")
            print(f"[TRANSCRIBE] wav size={len(wav)} bytes, calling Whisper API...")
            t0  = time.time()
            try:
                text    = transcribe(wav, OPENAI_API_KEY)
                elapsed = time.time() - t0
                print(f"[TRANSCRIBE] done in {elapsed:.1f}s")
                result = f'"{text}"' if text else "(silence)"
                print(f"\n>>> {result}\n")
                log_path = "/app/python/transcript.log"
                with open(log_path, "a") as f:
                    f.write(result + "\n")
                print(f"[FILE] saved to {log_path}")
            except urllib.error.HTTPError as e:
                body = e.read().decode()
                print(f"[ERROR] HTTP {e.code} from Whisper API:")
                print(f"        {body}\n")
            except urllib.error.URLError as e:
                print(f"[ERROR] Network error (no internet?): {e.reason}\n")
            except Exception as e:
                print(f"[ERROR] {type(e).__name__}: {e}")
                traceback.print_exc()


# ── Startup checks ────────────────────────────────────────────────
print("=" * 50)
print("[STARTUP] test_stt.py starting")
print(f"[STARTUP] Python {sys.version}")
print(f"[STARTUP] OPENAI_API_KEY set: {OPENAI_API_KEY != 'YOUR_KEY_HERE'}")
print(f"[STARTUP] API key prefix: {OPENAI_API_KEY[:8]}...")
print(f"[STARTUP] SPEECH_THRESHOLD={SPEECH_THRESHOLD}  SILENCE_THRESHOLD={SILENCE_THRESHOLD}")
print(f"[STARTUP] MAX_RECORD_PACKETS={MAX_RECORD_PACKETS} (~{MAX_RECORD_PACKETS*AUDIO_SAMPLES/ARDUINO_SAMPLE_RATE:.1f}s)")
print("=" * 50)
print("Listening... speak near the mic\n")

Bridge.provide("mcu_line", mcu_line)
App.run()
