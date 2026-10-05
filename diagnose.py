"""Explicit, bounded music-engine test; independent from daily production claims."""

import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import wave

from studio import Brief, Producer, load_config, mastering, meets_quality, safe_error, save_json, wav_qc


async def probe(producer, folder):
    events = []
    pcm = bytearray()
    prompt = "Acoustic guitar, expressive piano, melodic bass, live drum kit, relaxed alternative pop, instrumental"
    save_json(folder / "request.json", {"prompt": prompt, "model": producer.cfg["music_model"], "seconds": 15})
    async with producer.client.aio.live.music.connect(model=producer.cfg["music_model"]) as session:
        await session.set_weighted_prompts(prompts=[producer.types.WeightedPrompt(text=prompt, weight=1)])
        await session.set_music_generation_config(config=producer.types.LiveMusicGenerationConfig(bpm=104))
        await session.play()
        receiver = session.receive().__aiter__()
        while len(pcm) < 15 * 192000:
            message = await asyncio.wait_for(anext(receiver), timeout=30)
            filtered = getattr(message, "filtered_prompt", None)
            if filtered:
                events.append({"filtered_reason": filtered.filtered_reason, "text": filtered.text})
                save_json(folder / "events.json", events)
                raise RuntimeError("Prompt filtered: " + str(filtered.filtered_reason))
            content = getattr(message, "server_content", None)
            chunks = content.audio_chunks if content else []
            for chunk in chunks or []:
                pcm.extend(chunk.data)
            events.append({"received_bytes": len(pcm), "message_type": "audio" if chunks else "metadata"})
            save_json(folder / "events.json", events)
        await session.stop()
    wav = folder / "probe.wav"
    with wave.open(str(wav), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(pcm[:15 * 192000])
    qc = wav_qc(wav, 15)
    save_json(folder / "technical.json", qc)
    if not qc["passed"]:
        raise ValueError("Probe audio failed technical checks")
    save_json(folder / "mastering.json", mastering(wav, folder / "probe.mp3", 15))


def main():
    folder = Path(sys.argv[1])
    folder.mkdir(parents=True, exist_ok=True)
    save_json(folder / "manifest.json", {"status": "testing", "request_count": 0})
    producer = None
    try:
        cfg = load_config(Path("config.json"))
        mode = sys.argv[2] if len(sys.argv) > 2 else "probe"
        if mode == "review":
            if len(sys.argv) != 4 or not re.fullmatch(r"\d+", sys.argv[3]):
                raise ValueError("Review source must be a numeric diagnostic run ID")
            source = Path("diagnostics") / sys.argv[3]
            source_state = json.loads((source / "manifest.json").read_text())
            if source_state["status"] != "audio_verified":
                raise ValueError("Review requires a previously verified complete recording")
            brief = Brief.model_validate_json((source / "brief.json").read_text(encoding="utf-8"))
            day = Path("catalog/2026-10-05")
            state = json.loads((day / "manifest.json").read_text())
            if (day / "instrumental.mp3").exists():
                raise ValueError("Existing daily master must not be replaced by a diagnostic")
            digest = hashlib.sha256((source / "instrumental.mp3").read_bytes()).hexdigest()
            if digest in state.get("reviewed_diagnostic_audio", []):
                raise ValueError("This diagnostic recording already had its single review attempt")
            previous_calls = state.get("request_count", 0)
            if previous_calls >= 6:
                raise ValueError("Daily analysis budget already consumed")
            state.setdefault("reviewed_diagnostic_audio", []).append(digest)
            state["request_count"] = previous_calls + 1
            save_json(day / "manifest.json", state)
            save_json(folder / "manifest.json", {"status": "reviewing", "request_count": previous_calls})
            producer = Producer(os.environ["GEMINI_API_KEY"], cfg, folder, Path("catalog"))
            review = producer.listen(source / "instrumental.mp3", brief)
            qc = json.loads((source / "technical.json").read_text())
            approved = meets_quality(qc, review, cfg)
            record = {"candidate": "recovered_arrangement", "source": str(source), "technical": qc,
                      "listening": review.model_dump(), "score": review.average(), "accepted": approved}
            save_json(folder / "quality.json", record)
            if not approved:
                save_json(folder / "manifest.json", {"status": "quality_rejected", "request_count": producer.calls})
                print("Real audio evaluated but not accepted: " + str(review.average()))
                return 2
            save_json(day / "pre_audio_recovery_manifest.json", state)
            old_quality = json.loads((day / "quality.json").read_text())
            save_json(day / "quality.json", old_quality + [record])
            shutil.copyfile(source / "instrumental.mp3", day / "instrumental.mp3")
            shutil.copyfile(source / "mastering.json", day / "mastering.json")
            shutil.copyfile(source / "steering_1.json", day / "recovered_steering.json")
            state.update(status="ready_for_human_review", title=brief.title,
                         audio_sha256=hashlib.sha256((day / "instrumental.mp3").read_bytes()).hexdigest(),
                         selected_candidate="recovered_arrangement", recovery_source=str(source),
                         request_count=producer.calls, models={"text": producer.text_model, "music": cfg["music_model"]})
            state.pop("action", None)
            save_json(day / "manifest.json", state)
            requests = json.loads((day / "api_requests.json").read_text())
            requests.extend(json.loads((folder / "api_requests.json").read_text()))
            save_json(day / "api_requests.json", requests)
            save_json(folder / "manifest.json", {"status": "review_complete", "accepted": True, "score": review.average()})
            print("Complete original instrumental accepted and archived: " + str(review.average()))
            return 0
        producer = Producer(os.environ["GEMINI_API_KEY"], cfg, folder, Path("catalog"))
        if mode == "arranged":
            brief = Brief.model_validate_json(Path("catalog/2026-10-05/brief.json").read_text(encoding="utf-8"))
            save_json(folder / "brief.json", brief.model_dump())
            wav, duration = asyncio.run(asyncio.wait_for(producer.render(brief, 1), timeout=360))
            qc = wav_qc(wav, duration)
            save_json(folder / "technical.json", qc)
            if not qc["passed"]:
                raise ValueError("Arranged audio failed technical checks")
            save_json(folder / "mastering.json", mastering(wav, folder / "instrumental.mp3", duration))
        else:
            duration = 15
            asyncio.run(asyncio.wait_for(probe(producer, folder), timeout=100))
        save_json(folder / "manifest.json", {"status": "audio_verified", "seconds": duration, "release": "diagnostic_only"})
        print("Real music stream and encoded MP3 verified")
        return 0
    except Exception as exc:
        report = {"status": "failed", "error_type": type(exc).__name__, "error_message": safe_error(exc),
                  "request_count": producer.calls if producer else 0}
        save_json(folder / "manifest.json", report)
        print(json.dumps(report))
        return 1
    finally:
        if producer:
            with contextlib.suppress(Exception):
                producer.client.close()


if __name__ == "__main__":
    sys.exit(main())
