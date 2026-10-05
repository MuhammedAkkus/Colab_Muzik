import copy
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace
import wave

import numpy as np

import studio


def example_brief():
    return studio.Brief(
        title="Empty Platform", story="An invented story about learning to leave an empty railway platform behind.",
        bpm=100, key="A minor", hook="Scale degrees 1 3 5 3, short short long rest, varied return.",
        vocal_direction="Intimate chest voice, breathe before the last line, open vowels.",
        lyrics=studio.Lyrics(
            verse_1=["one invented verse line here", "two new images here now", "three quiet words here stay", "four words now open doors"],
            pre=["one thought becomes a question", "another phrase waits for dawn"],
            chorus=["carry the morning home again", "leave these empty roads behind", "let the window light return", "hold a different thought today"],
            verse_2=["new shoes cross the station", "somebody takes the next train", "paper maps fold into pockets", "a small decision grows today"],
            bridge=["stop the clock for nobody", "hear the city waking slowly", "take a step without permission", "leave a place for hope"]),
        sections=[studio.Section(name=name, arrangement="Warm guitar with expressive live drums and open piano.",
                                 chords="Am F C G", density=0.45) for name, _ in studio.FORM],
        editorial_notes="Test fixture only, not a produced song or example Turkish lyric.")


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = json.loads(Path("config.json").read_text())

    def tearDown(self):
        self.temp.cleanup()

    def make_wav(self, kind="tone", seconds=5):
        t = np.arange(48000 * seconds) / 48000
        if kind == "silent":
            samples = np.zeros(len(t), dtype="<i2")
        elif kind == "clipped":
            samples = np.full(len(t), 32767, dtype="<i2")
        else:
            samples = (np.sin(2 * np.pi * 440 * t) * 5000).astype("<i2")
        destination = self.root / "fixture.wav"
        with wave.open(str(destination), "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(48000)
            wav.writeframes(np.repeat(samples, 2).tobytes())
        return destination

    def test_billing_and_paid_model_guards(self):
        for change in ({"monthly_budget_usd": 1}, {"free_project_confirmed": False},
                       {"music_model": "lyria-3.5"}, {"candidates": 3}, {"text_model": "unknown"}):
            file = self.root / "config.json"
            studio.save_json(file, {**self.cfg, **change})
            with self.assertRaises(ValueError):
                studio.load_config(file)

    def test_claim_is_idempotent(self):
        folder = self.root / "2026-10-05"
        self.assertTrue(studio.claim(folder))
        self.assertFalse(studio.claim(folder))
        self.assertEqual(json.loads((folder / "manifest.json").read_text())["status"], "claimed")

    def test_recovery_once_before_music_only(self):
        folder = self.root / "2026-10-05"
        studio.save_json(folder / "manifest.json", {"status": "failed", "error_type": "InternalServerError",
                                                   "request_count": 1})
        self.assertFalse(studio.claim(folder))
        self.assertTrue(studio.claim(folder, repair_failed=True))
        data = json.loads((folder / "manifest.json").read_text())
        self.assertEqual(data["request_count"], 1)
        data["status"] = "failed"
        studio.save_json(folder / "manifest.json", data)
        self.assertFalse(studio.claim(folder, repair_failed=True))

    def test_quota_error_never_rearmed(self):
        folder = self.root / "2026-10-05"
        studio.save_json(folder / "manifest.json", {"status": "failed", "error_type": "ClientError",
                                                   "request_count": 1})
        self.assertFalse(studio.claim(folder, repair_failed=True))

    def test_server_error_uses_only_verified_free_text_fallback(self):
        class InternalServerError(Exception):
            pass
        producer = object.__new__(studio.Producer)
        producer.text_model = "gemini-3.8-flash"
        producer.request = Mock(side_effect=[InternalServerError(), type("Response", (), {
            "output_text": example_brief().model_dump_json()})()])
        with patch.object(studio.time, "sleep"):
            result = producer.structured("test", studio.Brief)
        self.assertEqual(result.title, "Empty Platform")
        self.assertEqual(producer.text_model, "gemini-3.5-flash")
        self.assertEqual(producer.request.call_count, 2)

    def test_quota_is_not_retried_by_structured_call(self):
        class ClientError(Exception):
            code = 429
        producer = object.__new__(studio.Producer)
        producer.text_model = "gemini-3.8-flash"
        producer.request = Mock(side_effect=ClientError())
        with self.assertRaises(ClientError):
            producer.structured("test", studio.Brief)
        self.assertEqual(producer.request.call_count, 1)

    def test_real_sdk_has_no_hidden_quota_retries(self):
        import httpx
        from google import genai
        count = []
        def handler(request):
            count.append(request)
            return httpx.Response(429, json={"error": {"code": 429, "message": "fixture quota error",
                                                       "status": "RESOURCE_EXHAUSTED"}})
        original = genai.Client
        def client_factory(**kwargs):
            kwargs["http_options"].httpx_client = httpx.Client(transport=httpx.MockTransport(handler))
            return original(**kwargs)
        studio.claim(self.root)
        with patch.object(genai, "Client", side_effect=client_factory):
            producer = studio.Producer("fixture-only", self.cfg, self.root, self.root)
        try:
            with self.assertRaises(Exception):
                producer.structured("No real network request", studio.Brief)
            self.assertEqual(len(count), 1)
        finally:
            producer.client.close()

    def test_request_cap_before_network(self):
        producer = object.__new__(studio.Producer)
        producer.calls = 6
        producer.client = Mock()
        with self.assertRaises(RuntimeError):
            producer.request("test", studio.Brief)
        producer.client.interactions.create.assert_not_called()
        self.assertEqual(producer.calls, 6)

    def test_diagnostics_redact_secrets_and_urls(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "secret-fixture-value"}):
            result = studio.safe_error(RuntimeError(
                "x-goog-api-key: secret-fixture-value https://example.test/?key=secret-fixture-value"))
        self.assertNotIn("secret-fixture-value", result)
        self.assertNotIn("https://", result)

    def test_existing_draft_can_finish_once_without_new_composition(self):
        folder = self.root / "2026-10-05"
        studio.save_json(folder / "manifest.json", {"status": "failed", "error_type": "InternalServerError",
                                                   "request_count": 4, "repair_count": 1})
        (folder / "editing_prompt.txt").write_text("Editor prompt. " + example_brief().model_dump_json()
                                                   + " Required order: []", encoding="utf-8")
        self.assertTrue(studio.claim(folder, finish_draft=True))
        self.assertTrue((folder / "draft_brief.json").exists())
        data = json.loads((folder / "manifest.json").read_text())
        self.assertEqual(data["request_count"], 4)
        data["status"] = "failed"
        studio.save_json(folder / "manifest.json", data)
        self.assertFalse(studio.claim(folder, finish_draft=True))

    def test_fixed_arrangement_and_duration(self):
        brief = example_brief()
        result = studio.timeline(brief)
        self.assertEqual(sum(s["bars"] for s in result), 68)
        self.assertAlmostEqual(result[-1]["end"], 163.2)
        self.assertEqual([s["name"] for s in result], [s[0] for s in studio.FORM])
        for prev, next_section in zip(result, result[1:]):
            self.assertEqual(prev["end"], next_section["start"])

    def test_wrong_section_order_rejected(self):
        draft = example_brief().model_dump()
        draft["sections"].reverse()
        with self.assertRaises(ValueError):
            studio.Brief.model_validate(draft)

    def test_own_catalog_duplicate_lyrics_rejected(self):
        brief = example_brief()
        studio.save_json(self.root / "2026-10-04" / "brief.json", brief.model_dump())
        draft = copy.deepcopy(brief)
        draft.title = "Another invented title"
        with self.assertRaises(ValueError):
            studio.check_originality(draft, self.root, self.root / "2026-10-05")

    def test_turkish_case_normalization(self):
        self.assertEqual(studio.normalized("\u0130Z I\u015eIK"), studio.normalized("iz \u0131\u015f\u0131k"))

    def test_technical_tone_passes(self):
        self.assertTrue(studio.wav_qc(self.make_wav(), 5)["passed"])

    def test_silent_and_clipped_rejected(self):
        self.assertFalse(studio.wav_qc(self.make_wav("silent"), 5)["passed"])
        self.assertFalse(studio.wav_qc(self.make_wav("clipped"), 5)["passed"])

    def test_truncated_audio_rejected(self):
        self.assertIn("incorrect_duration", studio.wav_qc(self.make_wav(), 10)["errors"])

    def test_quality_gate_requires_all_categories(self):
        review = studio.ListeningReview(groove=90, instrument_realism=50, hook=90, structure=90,
                                        mix=90, unwanted_vocals=False, observations=["one", "two"])
        self.assertFalse(studio.meets_quality({"passed": True}, review, self.cfg))
        review.instrument_realism = 90
        self.assertTrue(studio.meets_quality({"passed": True}, review, self.cfg))
        review.unwanted_vocals = True
        self.assertFalse(studio.meets_quality({"passed": True}, review, self.cfg))

    def test_missing_key_fails_without_leaking(self):
        folder = self.root / "2026-10-05"
        studio.claim(folder)
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(studio.execute(folder, self.cfg, self.root), 1)
        manifest = json.loads((folder / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["request_count"], 0)

    def test_mock_end_to_end_archive(self):
        folder = self.root / "2026-10-05"
        studio.claim(folder)
        source = self.make_wav()
        class FakeProducer:
            calls = 4
            client = type("Client", (), {"close": lambda self: None})()
            def __init__(self, *args):
                pass
            def compose(self):
                return example_brief()
            async def render(self, brief, candidate):
                dest = folder / "work" / f"candidate_{candidate}.wav"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest)
                return dest, 5
            def listen(self, *args):
                return studio.ListeningReview(groove=85, instrument_realism=85, hook=85,
                    structure=85, mix=85, unwanted_vocals=False, observations=["fixture", "mock review"])
        def fake_master(wav, mp3, duration):
            mp3.write_bytes(b"mock encoded audio, not playable music")
            return {"mock": True}
        with patch.object(studio, "Producer", FakeProducer), patch.object(studio, "mastering", fake_master), \
                patch.dict("os.environ", {"GEMINI_API_KEY": "test-only"}):
            self.assertEqual(studio.execute(folder, self.cfg, self.root), 0)
        self.assertTrue((folder / "instrumental.mp3").exists())
        manifest = json.loads((folder / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "ready_for_human_review")
        self.assertNotIn("test-only", json.dumps(manifest))
        self.assertEqual(studio.execute(folder, self.cfg, self.root), 0)

    def test_real_renderer_contract_and_control_evidence(self):
        from google.genai import types
        brief = example_brief()
        seconds = studio.timeline(brief)[-1]["end"]
        target = round(seconds * 48000) * 4
        session = SimpleNamespace(set_weighted_prompts=AsyncMock(),
                                  set_music_generation_config=AsyncMock(),
                                  play=AsyncMock(), stop=AsyncMock())
        async def receive():
            for _ in range(int(seconds) + 1):
                yield SimpleNamespace(filtered_prompt=None, server_content=SimpleNamespace(
                    audio_chunks=[SimpleNamespace(data=b"\x00" * 192000)]))
        session.receive = receive
        @asynccontextmanager
        async def connect(**kwargs):
            yield session
        producer = object.__new__(studio.Producer)
        producer.types, producer.folder, producer.cfg = types, self.root, self.cfg
        producer.client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(
            music=SimpleNamespace(connect=connect))))
        path, duration = asyncio.run(producer.render(brief, 1))
        with wave.open(str(path), "rb") as wav:
            self.assertEqual(wav.getnframes() * 4, target)
        evidence = json.loads((self.root / "steering_1.json").read_text())
        self.assertEqual(evidence["stage"], "complete")
        self.assertEqual({e["section"] for e in evidence["events"]}, {s[0] for s in studio.FORM})
        self.assertEqual(session.play.await_count, 1)
        self.assertEqual(session.stop.await_count, 1)

    def test_analysis_outage_preserves_verified_preview(self):
        folder = self.root / "2026-10-05"
        studio.claim(folder)
        (folder / "work").mkdir()
        wav = self.make_wav()
        producer = Mock()
        producer.calls = 4
        producer.compose.return_value = example_brief()
        producer.render = AsyncMock(return_value=(wav, 5))
        producer.listen.side_effect = RuntimeError("Listening service unavailable")
        def encode(source, destination, duration):
            destination.write_bytes(b"fixture master")
            return {"verified": True}
        cfg = {**self.cfg, "candidates": 1}
        with patch.object(studio, "Producer", return_value=producer), \
                patch.object(studio, "mastering", side_effect=encode), \
                patch.dict("os.environ", {"GEMINI_API_KEY": "fixture-only"}):
            self.assertEqual(studio.execute(folder, cfg, self.root), 2)
        manifest = json.loads((folder / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "awaiting_audio_review")
        self.assertTrue((folder / "preview.mp3").exists())
        self.assertFalse((folder / "instrumental.mp3").exists())

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg unavailable")
    def test_real_master_encoding(self):
        source = self.make_wav(seconds=8)
        master = self.root / "master.mp3"
        report = studio.mastering(source, master, 8)
        self.assertGreater(master.stat().st_size, 1000)
        self.assertLessEqual(float(report["encoded_master"]["input_tp"]), -0.5)


if __name__ == "__main__":
    unittest.main()
