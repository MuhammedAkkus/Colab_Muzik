"""Explicit, bounded music-engine test; independent from daily production claims."""

import argparse
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
from datetime import datetime
from zoneinfo import ZoneInfo

from studio import Brief, Producer, ffmpeg, load_config, mastering, meets_quality, safe_error, save_json, wav_qc


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


def archive_preview(source, day, folder, day_name):
    source_state = json.loads((source / "manifest.json").read_text())
    qc = json.loads((source / "technical.json").read_text())
    master = json.loads((source / "mastering.json").read_text())
    if source_state.get("status") != "audio_verified" or not qc.get("passed"):
        raise ValueError("Only a verified full arrangement may be archived")
    if source_state.get("seconds", 0) < 120:
        raise ValueError("A short connection probe is not a complete musical product")
    encoded = master["encoded_master"]
    if abs(float(encoded["input_i"]) + 14) > 2 or float(encoded["input_tp"]) > -0.5:
        raise ValueError("Encoded preview must pass mastering checks")
    if (day / "instrumental.mp3").exists() or (day / "preview.mp3").exists():
        raise ValueError("A saved daily recording must not be replaced")
    brief = Brief.model_validate_json((source / "brief.json").read_text(encoding="utf-8"))
    current_brief = Brief.model_validate_json((day / "brief.json").read_text(encoding="utf-8"))
    if brief.model_dump() != current_brief.model_dump():
        raise ValueError("Recovery must match the same preserved original brief")
    ffmpeg("-i", source / "instrumental.mp3", "-f", "null", "-")
    state = json.loads((day / "manifest.json").read_text())
    save_json(day / "pre_preview_recovery_manifest.json", state)
    shutil.copyfile(source / "instrumental.mp3", day / "preview.mp3")
    shutil.copyfile(source / "mastering.json", day / "mastering.json")
    shutil.copyfile(source / "steering_1.json", day / "recovered_steering.json")
    record = {"candidate": "recovered_arrangement", "source": str(source), "technical": qc,
              "mastering": master, "accepted": False, "stage": "awaiting_audio_review",
              "review_status": "Free analysis services returned 503; no listening score assigned",
              "file": "preview.mp3"}
    quality = json.loads((day / "quality.json").read_text())
    save_json(day / "quality.json", quality + [record])
    requests = json.loads((day / "api_requests.json").read_text())
    indexed = {r["request"]: r for r in requests}
    for path in sorted(Path("diagnostics").glob("*/api_requests.json")):
        for request in json.loads(path.read_text()):
            request_day = datetime.fromisoformat(request["timestamp"]).astimezone(ZoneInfo("Europe/Istanbul")).date().isoformat()
            if request_day == day_name and request["request"] <= state.get("request_count", 0):
                indexed.setdefault(request["request"], request)
    save_json(day / "api_requests.json", [indexed[n] for n in sorted(indexed)])
    state.update(status="awaiting_audio_review", pipeline_status="completed_with_review_pending",
                 title=brief.title, recovery_source=str(source), output_file="preview.mp3",
                 audio_sha256=hashlib.sha256((day / "preview.mp3").read_bytes()).hexdigest(),
                 action="Listen to the verified original preview; automatic criticism is unavailable.")
    save_json(day / "manifest.json", state)
    save_json(folder / "manifest.json", {"status": "preview_archived", "technical_passed": True,
                                        "automatic_quality_approved": False, "production_day": day_name})
    print("Complete verified original audio archived; human listening review pending")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("test", choices=["probe", "arranged", "review", "archive_preview"], default="probe", nargs="?")
    parser.add_argument("source", default="", nargs="?")
    parser.add_argument("--day")
    parser.add_argument("--retry-review-from")
    args = parser.parse_args()
    folder = args.folder
    folder.mkdir(parents=True, exist_ok=True)
    save_json(folder / "manifest.json", {"status": "testing", "request_count": 0})
    producer = None
    try:
        cfg = load_config(Path("config.json"))
        mode = args.test
        day_name = args.day or datetime.now(ZoneInfo(cfg["timezone"])).date().isoformat()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_name):
            raise ValueError("Production day must be YYYY-MM-DD")
        datetime.strptime(day_name, "%Y-%m-%d")
        day = Path("catalog") / day_name
        if mode == "archive_preview":
            if not re.fullmatch(r"\d+", args.source):
                raise ValueError("Source must be a numeric diagnostic run ID")
            archive_preview(Path("diagnostics") / args.source, day, folder, day_name)
            return 0
        if mode == "review":
            if not re.fullmatch(r"\d+", args.source):
                raise ValueError("Review source must be a numeric diagnostic run ID")
            source = Path("diagnostics") / args.source
            source_state = json.loads((source / "manifest.json").read_text())
            if source_state["status"] != "audio_verified":
                raise ValueError("Review requires a previously verified complete recording")
            brief = Brief.model_validate_json((source / "brief.json").read_text(encoding="utf-8"))
            if source_state.get("production_day") and source_state["production_day"] != day_name:
                raise ValueError("The supplied production day must match the diagnostic recording")
            state = json.loads((day / "manifest.json").read_text())
            if (day / "instrumental.mp3").exists():
                raise ValueError("Existing daily master must not be replaced by a diagnostic")
            digest = hashlib.sha256((source / "instrumental.mp3").read_bytes()).hexdigest()
            retry_review = bool(args.retry_review_from)
            if retry_review:
                if not re.fullmatch(r"\d+", args.retry_review_from):
                    raise ValueError("Recovery must reference a numeric failed review run")
                failure = json.loads((Path("diagnostics") / args.retry_review_from / "manifest.json").read_text())
                if (failure.get("error_type") != "ServerError" or failure.get("request_count") != 5
                        or state.get("request_count") != 5 or state.get("audio_review_recovery_count", 0)
                        or digest not in state.get("reviewed_diagnostic_audio", [])):
                    raise ValueError("Only one explicit recovery of the documented server outage is allowed")
                state["audio_review_recovery_count"] = 1
            elif digest in state.get("reviewed_diagnostic_audio", []):
                raise ValueError("This diagnostic recording already had its single review attempt")
            previous_calls = state.get("request_count", 0)
            if previous_calls >= 6:
                raise ValueError("Daily analysis budget already consumed")
            state.setdefault("reviewed_diagnostic_audio", []).append(digest)
            state["request_count"] = previous_calls + 1
            save_json(day / "manifest.json", state)
            save_json(folder / "manifest.json", {"status": "reviewing", "request_count": previous_calls})
            producer = Producer(os.environ["GEMINI_API_KEY"], cfg, folder, Path("catalog"))
            if retry_review:
                producer.text_model = "gemini-3.8-flash"
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
            brief = Brief.model_validate_json((day / "brief.json").read_text(encoding="utf-8"))
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
        save_json(folder / "manifest.json", {"status": "audio_verified", "seconds": duration,
                                            "production_day": day_name, "release": "diagnostic_only"})
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
