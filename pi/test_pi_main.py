"""
test_pi_main.py — Local tests for pi_main.py (no hardware required)

Tests all logic that doesn't need Arduino/Whisper/actual Claude API.
External packages (anthropic, serial, whisper, scipy) are stubbed at
import time so this runs without installing the full requirements.txt.

Run:
    python3 pi/test_pi_main.py
  or with pytest:
    python3 -m pytest pi/test_pi_main.py -v
"""

import struct
import sys
import time
import unittest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Stub out hardware/ML packages before importing pi_main
# ---------------------------------------------------------------------------

_anthropic_stub = MagicMock()
_anthropic_stub.APIError = Exception          # so except anthropic.APIError works
_anthropic_stub.Anthropic = MagicMock        # constructor
sys.modules.setdefault("anthropic", _anthropic_stub)
sys.modules.setdefault("serial", MagicMock())
sys.modules.setdefault("whisper", MagicMock())

_scipy_stub = MagicMock()
sys.modules.setdefault("scipy", _scipy_stub)
sys.modules.setdefault("scipy.signal", _scipy_stub.signal)

# numpy: use real install if available, fall back to a minimal stub
try:
    import numpy as _real_np
    sys.modules["numpy"] = _real_np
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False
    # minimal stub — only the attributes pi_main touches at module level
    _np_stub = MagicMock()
    _np_stub.uint8 = int
    _np_stub.float32 = float
    sys.modules["numpy"] = _np_stub

# Now it's safe to import pi_main
import pi_main  # noqa: E402  (import after sys.modules manipulation)
from pi_main import (  # noqa: E402
    DigitalTwin,
    Interaction,
    SensorReading,
    INTUITION_SYSTEM,
    PERSONA_UPDATE_TEMPLATE,
    SENSOR_PAYLOAD,
    PKT_SENSOR,
    PKT_AUDIO,
    PKT_BUTTON,
    build_sensor_context,
    call_claude_intuition,
    call_claude_persona_update,
)
if _NUMPY_AVAILABLE:
    from pi_main import process_audio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_reading(ax=0, ay=0, az=16384, gx=0, gy=0, gz=0, light=512, button=0):
    """Create a SensorReading with sensible defaults (1g on Z axis)."""
    return SensorReading(
        ts=time.time(),
        ax=ax, ay=ay, az=az,
        gx=gx, gy=gy, gz=gz,
        light=light,
        button=button,
    )


def make_interaction(transcript="test", symbols="🌊"):
    return Interaction(
        ts=time.time(),
        transcript=transcript,
        symbols=symbols,
        sensor_summary={},
    )


def make_twin(**kwargs) -> DigitalTwin:
    return DigitalTwin(user_id="test_user", **kwargs)


# ---------------------------------------------------------------------------
# DigitalTwin dataclass
# ---------------------------------------------------------------------------

class TestDigitalTwinSensorWindow(unittest.TestCase):
    """sensor_history is capped at 60 readings."""

    def test_add_sensors_within_cap(self):
        twin = make_twin()
        for _ in range(50):
            twin.add_sensor(make_reading())
        self.assertEqual(len(twin.sensor_history), 50)

    def test_add_sensors_trims_at_60(self):
        twin = make_twin()
        for i in range(70):
            twin.add_sensor(make_reading(ax=i))
        self.assertEqual(len(twin.sensor_history), 60)
        # Oldest readings were dropped — first remaining ax should be 10
        self.assertEqual(twin.sensor_history[0].ax, 10)

    def test_add_sensors_exactly_at_cap(self):
        twin = make_twin()
        for _ in range(60):
            twin.add_sensor(make_reading())
        self.assertEqual(len(twin.sensor_history), 60)


class TestDigitalTwinInteractionWindow(unittest.TestCase):
    """interactions list is capped at 20."""

    def test_add_interactions_within_cap(self):
        twin = make_twin()
        for _ in range(15):
            twin.add_interaction(make_interaction())
        self.assertEqual(len(twin.interactions), 15)

    def test_add_interactions_trims_at_20(self):
        twin = make_twin()
        for i in range(25):
            twin.add_interaction(make_interaction(transcript=str(i)))
        self.assertEqual(len(twin.interactions), 20)
        # Oldest dropped — first remaining transcript should be "5"
        self.assertEqual(twin.interactions[0].transcript, "5")

    def test_persona_summary_default_empty(self):
        twin = make_twin()
        self.assertEqual(twin.persona_summary, "")


# ---------------------------------------------------------------------------
# summarize_sensors
# ---------------------------------------------------------------------------

class TestSummarizeSensors(unittest.TestCase):

    def test_empty_history_returns_empty_dict(self):
        twin = make_twin()
        self.assertEqual(twin.summarize_sensors(), {})

    def test_single_reading_no_variance(self):
        twin = make_twin()
        twin.add_sensor(make_reading(ax=100, ay=200, az=300, light=400))
        summary = twin.summarize_sensors()
        self.assertEqual(summary["n_readings"], 1)
        self.assertAlmostEqual(summary["motion_mag_var"], 0.0, places=1)
        self.assertAlmostEqual(summary["light_var"], 0.0, places=1)
        self.assertEqual(summary["tilt_x_raw"], 100)
        self.assertEqual(summary["tilt_y_raw"], 200)

    def test_motion_magnitude_mean(self):
        twin = make_twin()
        # 3-4-5 right triangle: mag = sqrt(9+16+0) = 5.0 (approx at int16 scale)
        twin.add_sensor(make_reading(ax=300, ay=400, az=0))
        summary = twin.summarize_sensors()
        self.assertAlmostEqual(summary["motion_mag_mean"], 500.0, places=0)

    def test_light_mean_and_variance(self):
        twin = make_twin()
        twin.add_sensor(make_reading(light=100))
        twin.add_sensor(make_reading(light=300))
        summary = twin.summarize_sensors()
        self.assertAlmostEqual(summary["light_mean"], 200.0, places=1)
        self.assertGreater(summary["light_var"], 0.0)

    def test_window_uses_last_20_only(self):
        twin = make_twin()
        # Add 30 readings with light=0, then 20 with light=1000
        for _ in range(30):
            twin.add_sensor(make_reading(light=0))
        for _ in range(20):
            twin.add_sensor(make_reading(light=1000))
        summary = twin.summarize_sensors()
        # Window of 20 should only see light=1000
        self.assertAlmostEqual(summary["light_mean"], 1000.0, places=1)
        self.assertEqual(summary["n_readings"], 20)

    def test_tilt_comes_from_last_reading(self):
        twin = make_twin()
        twin.add_sensor(make_reading(ax=111, ay=222))
        twin.add_sensor(make_reading(ax=999, ay=888))  # last
        summary = twin.summarize_sensors()
        self.assertEqual(summary["tilt_x_raw"], 999)
        self.assertEqual(summary["tilt_y_raw"], 888)


# ---------------------------------------------------------------------------
# Packet constants & struct format
# ---------------------------------------------------------------------------

class TestPacketConstants(unittest.TestCase):

    def test_packet_type_bytes(self):
        self.assertEqual(PKT_SENSOR, ord('S'))
        self.assertEqual(PKT_AUDIO,  ord('A'))
        self.assertEqual(PKT_BUTTON, ord('B'))

    def test_sensor_payload_size(self):
        # 6×int16 (12B) + uint16 (2B) + uint8 (1B) = 15B
        self.assertEqual(SENSOR_PAYLOAD, 15)

    def test_sensor_struct_unpacking(self):
        """Round-trip: pack known values → unpack → verify match."""
        ax, ay, az = 100, -200, 16384
        gx, gy, gz = 50, -50, 0
        light = 512
        button = 1

        payload = struct.pack('<6hHB', ax, ay, az, gx, gy, gz, light, button)
        self.assertEqual(len(payload), SENSOR_PAYLOAD)

        u_ax, u_ay, u_az, u_gx, u_gy, u_gz, u_light, u_button = \
            struct.unpack('<6hHB', payload)

        self.assertEqual(u_ax, ax)
        self.assertEqual(u_ay, ay)
        self.assertEqual(u_az, az)
        self.assertEqual(u_gx, gx)
        self.assertEqual(u_gy, gy)
        self.assertEqual(u_gz, gz)
        self.assertEqual(u_light, light)
        self.assertEqual(u_button, button)

    def test_struct_endianness_little_endian(self):
        """Verify LE byte order: low byte first."""
        payload = struct.pack('<6hHB', 256, 0, 0, 0, 0, 0, 0, 0)
        # 256 LE = 0x00 0x01
        self.assertEqual(payload[0], 0x00)
        self.assertEqual(payload[1], 0x01)

    def test_sensor_packet_total_size(self):
        # Type byte (1) + payload (15) = 16 bytes total
        self.assertEqual(1 + SENSOR_PAYLOAD, 16)


# ---------------------------------------------------------------------------
# build_sensor_context
# ---------------------------------------------------------------------------

class TestBuildSensorContext(unittest.TestCase):

    def _ctx(self, motion_mean=0.0, motion_var=0.0, light_mean=512.0, light_var=0.0):
        summary = {
            "motion_mag_mean": motion_mean,
            "motion_mag_var":  motion_var,
            "light_mean":      light_mean,
            "light_var":       light_var,
        }
        return build_sensor_context(summary)

    def test_returns_all_expected_keys(self):
        ctx = self._ctx()
        for key in ("hr", "hrv", "gsr", "movement", "location_type", "time_of_day", "weather"):
            self.assertIn(key, ctx)

    def test_location_bright_outdoor(self):
        ctx = self._ctx(light_mean=900)
        self.assertIn("outdoor", ctx["location_type"])

    def test_location_indoor_lit(self):
        ctx = self._ctx(light_mean=600)
        self.assertIn("indoor", ctx["location_type"])

    def test_location_dim_indoor(self):
        ctx = self._ctx(light_mean=100)
        self.assertIn("dim", ctx["location_type"])

    def test_location_threshold_boundaries(self):
        self.assertIn("outdoor", self._ctx(light_mean=801)["location_type"])
        self.assertIn("indoor",  self._ctx(light_mean=800)["location_type"])
        self.assertIn("dim",     self._ctx(light_mean=400)["location_type"])
        self.assertIn("indoor",  self._ctx(light_mean=401)["location_type"])

    def test_weather_always_unknown(self):
        ctx = self._ctx()
        self.assertEqual(ctx["weather"], "unknown")

    def test_hr_increases_with_motion(self):
        ctx_low  = self._ctx(motion_mean=0)
        ctx_high = self._ctx(motion_mean=1000)
        # Extract numeric part (before " bpm")
        hr_low  = int(ctx_low["hr"].split("~")[1].split(" ")[0])
        hr_high = int(ctx_high["hr"].split("~")[1].split(" ")[0])
        self.assertGreater(hr_high, hr_low)

    def test_movement_field_contains_magnitude(self):
        ctx = self._ctx(motion_mean=42.7)
        self.assertIn("42.7", ctx["movement"])

    def test_time_of_day_format(self):
        import re
        ctx = self._ctx()
        self.assertRegex(ctx["time_of_day"], r"^\d{2}:\d{2}$")

    def test_empty_summary_uses_defaults(self):
        # Should not raise even with empty dict
        ctx = build_sensor_context({})
        self.assertIn("location_type", ctx)


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

class TestPromptTemplates(unittest.TestCase):

    def test_intuition_system_contains_key_constraints(self):
        self.assertIn("1-3 symbols", INTUITION_SYSTEM)
        self.assertIn("No words", INTUITION_SYSTEM)
        self.assertIn("No explanations", INTUITION_SYSTEM)

    def test_persona_update_template_formatting(self):
        result = PERSONA_UPDATE_TEMPLATE.format(
            old_persona="User is calm and focused.",
            signals="  hr: ~62 bpm\n  movement: 0.5",
        )
        self.assertIn("User is calm and focused.", result)
        self.assertIn("~62 bpm", result)
        self.assertIn("2-3 sentences", result)

    def test_persona_update_template_has_required_placeholders(self):
        # Both placeholders must exist
        self.assertIn("{old_persona}", PERSONA_UPDATE_TEMPLATE)
        self.assertIn("{signals}", PERSONA_UPDATE_TEMPLATE)


# ---------------------------------------------------------------------------
# Claude call wrappers (mocked client)
# ---------------------------------------------------------------------------

class TestCallClaudeIntuition(unittest.TestCase):

    def _make_client(self, return_text="🌊⚡"):
        client = MagicMock()
        msg = MagicMock()
        msg.content = [MagicMock(text=f"  {return_text}  ")]  # whitespace stripped
        client.messages.create.return_value = msg
        return client

    def test_returns_stripped_symbols(self):
        client = self._make_client("🔥")
        twin = make_twin()
        result = call_claude_intuition(client, twin, "Should I go?", {})
        self.assertEqual(result, "🔥")

    def test_uses_intuition_system_prompt(self):
        client = self._make_client()
        twin = make_twin()
        call_claude_intuition(client, twin, "test", {})
        _, kwargs = client.messages.create.call_args
        self.assertEqual(kwargs["system"], INTUITION_SYSTEM)

    def test_max_tokens_is_20(self):
        client = self._make_client()
        twin = make_twin()
        call_claude_intuition(client, twin, "test", {})
        _, kwargs = client.messages.create.call_args
        self.assertEqual(kwargs["max_tokens"], 20)

    def test_transcript_appears_in_user_message(self):
        client = self._make_client()
        twin = make_twin()
        call_claude_intuition(client, twin, "Should I quit my job?", {})
        _, kwargs = client.messages.create.call_args
        user_content = kwargs["messages"][0]["content"]
        self.assertIn("Should I quit my job?", user_content)

    def test_persona_snapshot_in_user_message(self):
        client = self._make_client()
        twin = make_twin()
        twin.persona_summary = "High energy, scattered focus."
        call_claude_intuition(client, twin, "test", {})
        _, kwargs = client.messages.create.call_args
        user_content = kwargs["messages"][0]["content"]
        self.assertIn("High energy, scattered focus.", user_content)

    def test_no_persona_falls_back_to_not_established(self):
        client = self._make_client()
        twin = make_twin()
        call_claude_intuition(client, twin, "test", {})
        _, kwargs = client.messages.create.call_args
        user_content = kwargs["messages"][0]["content"]
        self.assertIn("not yet established", user_content)

    def test_api_error_returns_none(self):
        client = MagicMock()
        client.messages.create.side_effect = _anthropic_stub.APIError("boom")
        twin = make_twin()
        result = call_claude_intuition(client, twin, "test", {})
        self.assertIsNone(result)


class TestCallClaudePersonaUpdate(unittest.TestCase):

    def _make_client(self, return_text="User is calm and grounded."):
        client = MagicMock()
        msg = MagicMock()
        msg.content = [MagicMock(text=f"\n{return_text}\n")]
        client.messages.create.return_value = msg
        return client

    def test_returns_stripped_persona(self):
        client = self._make_client("Calm, focused, indoor.")
        twin = make_twin()
        result = call_claude_persona_update(client, twin, {})
        self.assertEqual(result, "Calm, focused, indoor.")

    def test_no_system_prompt(self):
        client = self._make_client()
        twin = make_twin()
        call_claude_persona_update(client, twin, {})
        _, kwargs = client.messages.create.call_args
        self.assertNotIn("system", kwargs)

    def test_max_tokens_is_200(self):
        client = self._make_client()
        twin = make_twin()
        call_claude_persona_update(client, twin, {})
        _, kwargs = client.messages.create.call_args
        self.assertEqual(kwargs["max_tokens"], 200)

    def test_old_persona_in_prompt(self):
        client = self._make_client()
        twin = make_twin()
        twin.persona_summary = "Previously tired."
        call_claude_persona_update(client, twin, {})
        _, kwargs = client.messages.create.call_args
        user_content = kwargs["messages"][0]["content"]
        self.assertIn("Previously tired.", user_content)

    def test_first_reading_fallback_text(self):
        client = self._make_client()
        twin = make_twin()  # persona_summary = ""
        call_claude_persona_update(client, twin, {})
        _, kwargs = client.messages.create.call_args
        user_content = kwargs["messages"][0]["content"]
        self.assertIn("first reading", user_content)

    def test_ambient_transcript_included_in_signals(self):
        client = self._make_client()
        twin = make_twin()
        call_claude_persona_update(client, twin, {}, ambient_transcript="I keep second-guessing myself.")
        _, kwargs = client.messages.create.call_args
        user_content = kwargs["messages"][0]["content"]
        self.assertIn("I keep second-guessing myself.", user_content)

    def test_no_ambient_transcript_omits_audio_line(self):
        client = self._make_client()
        twin = make_twin()
        call_claude_persona_update(client, twin, {}, ambient_transcript="")
        _, kwargs = client.messages.create.call_args
        user_content = kwargs["messages"][0]["content"]
        self.assertNotIn("ambient_audio", user_content)

    def test_api_error_returns_none(self):
        client = MagicMock()
        client.messages.create.side_effect = _anthropic_stub.APIError("boom")
        twin = make_twin()
        result = call_claude_persona_update(client, twin, {})
        self.assertIsNone(result)

    def test_intuition_never_uses_persona_update_path(self):
        """Button-press intuition call must use system prompt; persona update must not."""
        intuition_client = self._make_client("🌊")
        persona_client = self._make_client("Calm and present.")
        twin = make_twin()

        call_claude_intuition(intuition_client, twin, "stay or go?", {})
        _, ikwargs = intuition_client.messages.create.call_args
        self.assertIn("system", ikwargs)           # intuition has system prompt
        self.assertEqual(ikwargs["max_tokens"], 20)

        call_claude_persona_update(persona_client, twin, {})
        _, pkwargs = persona_client.messages.create.call_args
        self.assertNotIn("system", pkwargs)        # persona update has no system prompt
        self.assertEqual(pkwargs["max_tokens"], 200)


# ---------------------------------------------------------------------------
# Audio processing (requires numpy)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_NUMPY_AVAILABLE, "numpy not installed — skipping audio tests")
class TestProcessAudio(unittest.TestCase):

    def _silence(self, n=256):
        """128 = silence (maps to 0.0 after normalization)."""
        import numpy as np
        return [np.full(n, 128, dtype=np.uint8)]

    def test_silence_normalizes_near_zero(self):
        import numpy as np
        audio = process_audio(self._silence())
        self.assertAlmostEqual(float(np.abs(audio).mean()), 0.0, places=3)

    def test_output_dtype_float32(self):
        import numpy as np
        audio = process_audio(self._silence())
        self.assertEqual(audio.dtype, np.float32)

    def test_output_length_doubled(self):
        """8kHz → 16kHz via up=2 should roughly double the sample count."""
        import numpy as np
        chunks = [np.full(512, 128, dtype=np.uint8)]
        audio = process_audio(chunks)
        # resample_poly with up=2 yields 1024 samples
        self.assertEqual(len(audio), 1024)

    def test_max_value_maps_near_plus_one(self):
        """uint8 255 → (255-128)/128 ≈ 1.0"""
        import numpy as np
        chunks = [np.full(64, 255, dtype=np.uint8)]
        audio = process_audio(chunks)
        self.assertAlmostEqual(float(audio.mean()), 1.0, places=1)

    def test_min_value_maps_near_minus_one(self):
        """uint8 0 → (0-128)/128 ≈ -1.0"""
        import numpy as np
        chunks = [np.full(64, 0, dtype=np.uint8)]
        audio = process_audio(chunks)
        self.assertAlmostEqual(float(audio.mean()), -1.0, places=1)

    def test_multiple_chunks_concatenated(self):
        """Two 128-sample chunks → 256 samples in → 512 out."""
        import numpy as np
        c1 = np.full(128, 128, dtype=np.uint8)
        c2 = np.full(128, 128, dtype=np.uint8)
        audio = process_audio([c1, c2])
        self.assertEqual(len(audio), 512)


# ---------------------------------------------------------------------------
# Integration: twin state updates through button-press flow (no I/O)
# ---------------------------------------------------------------------------

class TestTwinIntegrationFlow(unittest.TestCase):
    """Simulate one complete button-press cycle end-to-end using mocked Claude."""

    def test_full_cycle_updates_twin(self):
        twin = make_twin()

        # Populate some sensor history
        for i in range(10):
            twin.add_sensor(make_reading(light=400 + i * 10))

        sensor_summary = twin.summarize_sensors()
        self.assertGreater(sensor_summary["n_readings"], 0)

        # Simulate Claude intuition response
        intuition_client = MagicMock()
        intuition_msg = MagicMock()
        intuition_msg.content = [MagicMock(text="⚡🌿")]
        intuition_client.messages.create.return_value = intuition_msg

        symbols = call_claude_intuition(
            intuition_client, twin, "Should I take the meeting?", sensor_summary
        )
        self.assertEqual(symbols, "⚡🌿")

        # Simulate Claude persona update response
        persona_client = MagicMock()
        persona_msg = MagicMock()
        persona_msg.content = [MagicMock(text="User is alert and slightly restless.")]
        persona_client.messages.create.return_value = persona_msg

        new_persona = call_claude_persona_update(
            persona_client, twin, sensor_summary
        )

        # Apply updates to twin
        twin.add_interaction(Interaction(
            ts=time.time(),
            transcript="Should I take the meeting?",
            symbols=symbols,
            sensor_summary=sensor_summary,
        ))
        twin.persona_summary = new_persona

        # Verify twin state
        self.assertEqual(len(twin.interactions), 1)
        self.assertEqual(twin.interactions[0].symbols, "⚡🌿")
        self.assertEqual(twin.interactions[0].transcript, "Should I take the meeting?")
        self.assertIn("restless", twin.persona_summary)

    def test_second_cycle_sees_prior_persona_in_prompt(self):
        """Second button press should include updated persona in the user message."""
        twin = make_twin()
        twin.persona_summary = "Calm but distracted."

        client = MagicMock()
        msg = MagicMock()
        msg.content = [MagicMock(text="🌊")]
        client.messages.create.return_value = msg

        call_claude_intuition(client, twin, "Go for a run?", {})

        _, kwargs = client.messages.create.call_args
        self.assertIn("Calm but distracted.", kwargs["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
