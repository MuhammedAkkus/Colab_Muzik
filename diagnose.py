"""Explicit, bounded music-engine test; independent from daily production claims."""

import asyncio
import contextlib
import json
import os
from pathlib import Path
import sys
import wave

from studio import Brief, Producer, load_config, mastering, safe_error, save_json, wav_qc


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
        producer = Producer(os.environ["GEMINI_API_KEY"], load_config(Path("config.json")), folder, Path("catalog"))
        if len(sys.argv) > 2 and sys.argv[2] == "arranged":
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
        report = {"status": "failed", "error_type": type(exc).__name__, "error_message": safe_error(exc)}
        save_json(folder / "manifest.json", report)
        print(json.dumps(report))
        return 1
    finally:
        if producer:
            with contextlib.suppress(Exception):
                producer.client.close()


if __name__ == "__main__":
    sys.exit(main())
