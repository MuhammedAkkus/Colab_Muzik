"""Free-project-only instrumental production. Never invokes a paid music model."""

import argparse
import asyncio
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import wave
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
from pydantic import BaseModel, Field, model_validator


FORM = [("intro", 4), ("verse_1", 8), ("pre_1", 4), ("chorus_1", 8),
        ("turnaround", 4), ("verse_2", 8), ("pre_2", 4), ("chorus_2", 8),
        ("bridge", 8), ("final_chorus", 8), ("outro", 4)]
SCALES = {"A minor": "C_MAJOR_A_MINOR", "E minor": "G_MAJOR_E_MINOR",
          "D minor": "F_MAJOR_D_MINOR", "B minor": "D_MAJOR_B_MINOR"}
BASE_PROMPT = (
    "Turkish alternative pop, intimate live studio band, instrumental only. "
    "Warm acoustic guitar, expressive piano, melodic electric bass, real drum kit, "
    "restrained clean electric guitar. Cohesive memorable recurring two-bar lead motif, "
    "human microtiming and touch, breathing room for a future singer. "
    "No EDM drop, no genre switch, no dense synth layers, no vocals or speech."
)


class Section(BaseModel):
    name: str
    arrangement: str = Field(min_length=20, max_length=700)
    chords: str = Field(min_length=3, max_length=120)
    density: float = Field(ge=0.2, le=0.8)


class Lyrics(BaseModel):
    verse_1: list[str] = Field(min_length=4, max_length=4)
    pre: list[str] = Field(min_length=2, max_length=2)
    chorus: list[str] = Field(min_length=4, max_length=4)
    verse_2: list[str] = Field(min_length=4, max_length=4)
    bridge: list[str] = Field(min_length=4, max_length=4)


class Brief(BaseModel):
    title: str = Field(min_length=2, max_length=70)
    story: str = Field(min_length=40, max_length=1400)
    bpm: int = Field(ge=96, le=112)
    key: str
    hook: str = Field(min_length=20, max_length=600)
    vocal_direction: str = Field(min_length=20, max_length=600)
    lyrics: Lyrics
    sections: list[Section] = Field(min_length=11, max_length=11)
    editorial_notes: str = Field(min_length=20, max_length=1400)

    @model_validator(mode="after")
    def valid_form(self):
        if self.key not in SCALES:
            raise ValueError("Unsupported musical key")
        if [s.name for s in self.sections] != [s[0] for s in FORM]:
            raise ValueError("Section order must match the fixed musical form")
        for lines in self.lyrics.model_dump().values():
            if any(not 3 <= len(line.split()) <= 16 for line in lines):
                raise ValueError("Lyric lines must contain 3-16 words")
        return self


class ListeningReview(BaseModel):
    groove: int = Field(ge=0, le=100)
    instrument_realism: int = Field(ge=0, le=100)
    hook: int = Field(ge=0, le=100)
    structure: int = Field(ge=0, le=100)
    mix: int = Field(ge=0, le=100)
    unwanted_vocals: bool
    observations: list[str] = Field(min_length=2, max_length=8)

    def scores(self):
        return [self.groove, self.instrument_realism, self.hook, self.structure, self.mix]

    def average(self):
        return sum(self.scores()) / 5


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def load_config(path):
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg.get("monthly_budget_usd") != 0 or not cfg.get("free_project_confirmed"):
        raise ValueError("This version requires a billing-disabled free project and zero budget")
    if cfg.get("music_model") != "models/lyria-realtime-exp":
        raise ValueError("Paid music models are disabled")
    if cfg.get("text_model") != "gemini-3.8-flash":
        raise ValueError("Only the verified free-tier text model is allowed")
    if not 1 <= cfg.get("candidates", 0) <= 2:
        raise ValueError("At most two instrumental candidates are allowed per day")
    for name in ("minimum_listening_score", "minimum_category_score"):
        if not 0 <= cfg.get(name, -1) <= 100:
            raise ValueError("Invalid listening threshold")
    return cfg


def normalized(text):
    text = text.replace("I", "\u0131").replace("\u0130", "i").lower()
    return re.findall(r"\w+", unicodedata.normalize("NFKC", text))


def ngrams(text, size=5):
    tokens = normalized(text)
    return {tuple(tokens[i:i + size]) for i in range(len(tokens) - size + 1)}


def lyric_text(brief):
    return "\n".join(line for lines in brief.lyrics.model_dump().values() for line in lines)


def check_originality(brief, catalog, exclude):
    fresh = ngrams(lyric_text(brief))
    for path in sorted(catalog.glob("*/brief.json")):
        if path.parent == exclude:
            continue
        old = Brief.model_validate_json(path.read_text(encoding="utf-8"))
        if normalized(old.title) == normalized(brief.title):
            raise ValueError("Title already exists in this catalog")
        if fresh & ngrams(lyric_text(old)):
            raise ValueError("Repeated five-word lyric phrase in this catalog")


def timeline(brief):
    position = 0.0
    result = []
    for section, (_, bars) in zip(brief.sections, FORM):
        duration = bars * 4 * 60 / brief.bpm
        result.append({**section.model_dump(), "bars": bars,
                       "start": round(position, 4), "end": round(position + duration, 4)})
        position += duration
    return result


def ffmpeg(*args):
    result = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-y", *map(str, args)],
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError("Audio processing failed; inspect the local audio/FFmpeg installation")
    return result.stderr


def wav_qc(path, expected):
    with wave.open(str(path), "rb") as audio:
        channels, rate, width = audio.getnchannels(), audio.getframerate(), audio.getsampwidth()
        if channels != 2 or rate != 48000 or width != 2:
            raise ValueError("Expected stereo 48kHz PCM16")
        data = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(float) / 32768
    if not len(data):
        return {"passed": False, "errors": ["empty_audio"]}
    stereo = data.reshape(-1, 2)
    duration = len(stereo) / rate
    peak = float(np.max(np.abs(data)))
    rms = float(np.sqrt(np.mean(data ** 2)))
    clip = float(np.mean(np.abs(data) >= 32760 / 32768))
    windows = [stereo[i:i + rate] for i in range(0, len(stereo), rate)]
    quiet = [float(np.sqrt(np.mean(w ** 2))) < 0.001 for w in windows]
    longest = current = 0
    for is_quiet in quiet:
        current = current + 1 if is_quiet else 0
        longest = max(longest, current)
    errors = []
    if abs(duration - expected) > 1:
        errors.append("incorrect_duration")
    if rms < 0.005:
        errors.append("near_silent")
    if clip > 0.0001:
        errors.append("clipping")
    if longest > 4:
        errors.append("long_silence")
    return {"passed": not errors, "errors": errors, "duration_seconds": duration,
            "peak_dbfs": 20 * math.log10(max(peak, 1e-12)), "rms_dbfs": 20 * math.log10(max(rms, 1e-12)),
            "clipped_sample_fraction": clip, "longest_quiet_seconds": longest}


def mastering(source, output, duration):
    fade = f"afade=t=in:st=0:d=0.15,afade=t=out:st={duration - 2}:d=2"
    log = ffmpeg("-i", source, "-af", fade + ",loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
                 "-f", "null", "-")
    measured = json.loads(log[log.rfind("{"):log.rfind("}") + 1])
    names = ["input_i", "input_tp", "input_lra", "input_thresh", "target_offset"]
    if not all(math.isfinite(float(measured[x])) for x in names):
        raise ValueError("Cannot master silent or invalid audio")
    filt = (f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={measured['input_i']}:"
            f"measured_TP={measured['input_tp']}:measured_LRA={measured['input_lra']}:"
            f"measured_thresh={measured['input_thresh']}:offset={measured['target_offset']}:"
            "linear=true:print_format=json")
    ffmpeg("-i", source, "-af", fade + "," + filt, "-ar", "48000", "-ac", "2",
           "-codec:a", "libmp3lame", "-b:a", "256k", output)
    # Measure the encoded deliverable, not just the pre-encoding waveform.
    post = ffmpeg("-i", output, "-af", "loudnorm=I=-14:TP=-1:LRA=11:print_format=json", "-f", "null", "-")
    delivered = json.loads(post[post.rfind("{"):post.rfind("}") + 1])
    if float(delivered["input_tp"]) > -0.5 or abs(float(delivered["input_i"]) + 14) > 2:
        raise ValueError("Encoded master failed loudness/true-peak checks")
    return {"before": measured, "encoded_master": delivered}


def meets_quality(qc, review, cfg):
    return (qc.get("passed", False) and not review.unwanted_vocals
            and review.average() >= cfg["minimum_listening_score"]
            and min(review.scores()) >= cfg["minimum_category_score"])


class Producer:
    def __init__(self, key, cfg, folder, catalog):
        from google import genai
        from google.genai import types
        self.types = types
        self.client = genai.Client(api_key=key, http_options=types.HttpOptions(
            api_version="v1beta", timeout=120000,
            retry_options=types.HttpRetryOptions(attempts=1)))
        self.cfg, self.folder, self.catalog = cfg, folder, catalog
        self.calls = json.loads((folder / "manifest.json").read_text(encoding="utf-8")).get("request_count", 0)
        self.text_model = cfg["text_model"]

    def request(self, items, schema):
        self.calls += 1
        if self.calls > 6:
            raise RuntimeError("Daily text/audio-analysis request limit reached")
        log_path = self.folder / "api_requests.json"
        records = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
        records.append({"request": self.calls, "model": self.text_model,
                        "timestamp": datetime.now(ZoneInfo("UTC")).isoformat()})
        save_json(log_path, records)
        return self.client.interactions.create(
            model=self.text_model, input=items,
            generation_config={"thinking_level": "low"},
            response_format={"type": "text", "mime_type": "application/json",
                             "schema": schema.model_json_schema()})

    def structured(self, prompt, schema, audio=None):
        items = prompt
        uploaded = None
        try:
            if audio:
                uploaded = self.client.files.upload(file=str(audio))
                items = [{"type": "text", "text": prompt},
                         {"type": "audio", "uri": uploaded.uri, "mime_type": uploaded.mime_type}]
            try:
                response = self.request(items, schema)
            except Exception as exc:
                code = getattr(exc, "code", getattr(exc, "status_code", 0))
                server_error = (isinstance(code, int) and 500 <= code < 600) or type(exc).__name__ in (
                    "InternalServerError", "ServerError", "ServiceUnavailableError")
                if not server_error or self.text_model != "gemini-3.8-flash":
                    raise
                # Both models have free tiers. Never retry quota/auth errors or use paid music.
                self.text_model = "gemini-3.5-flash"
                time.sleep(10)
                response = self.request(items, schema)
            return schema.model_validate_json(response.output_text)
        finally:
            if uploaded:
                with contextlib.suppress(Exception):
                    self.client.files.delete(name=uploaded.name)

    def compose(self):
        recent = []
        for path in sorted(self.catalog.glob("*/brief.json"))[-30:]:
            old = json.loads(path.read_text(encoding="utf-8"))
            recent.append({"title": old["title"], "story": old["story"], "lyrics": old["lyrics"]})
        prompt = (
            "You are a meticulous Turkish alternative-pop songwriter and band arranger. "
            "Invent two distinct concepts internally, reject the weaker one, deliver only the better. "
            "Write original Turkish lyrics with proper Turkish characters, concrete lived-in images, "
            "emotional subtext and one memorable chorus hook. Natural spoken Turkish, 8-12 approximate "
            "syllables per line, breatheable vowel endings. No forced rhyme, generic heartbreak cliches, "
            "translationese, artist names, borrowed phrases, quotations or samples. Verse 2 must advance "
            "the story; bridge changes perspective. Describe a singable two-bar melodic hook using "
            "scale degrees/rhythm and vocal phrasing; no existing tune. Lyrics are a future vocalist's "
            "score, NOT sung by the instrumental engine. Keep 4/4, 96-112 BPM, one key throughout from "
            f"{list(SCALES)}. Use exactly these sections in order: {[x[0] for x in FORM]}. "
            "Arrangement and chords must be in English. Intro sparse piano/fingerpicked guitar; verse "
            "subtle kick/rim and bass; pre build; chorus open acoustic strum, live snare and hook guitar; "
            "bridge strips back with a harmonic color; final chorus expands without genre shift; outro "
            "resolves. Density 0.25-0.70. Bass locks to kick but moves melodically, sparse drum fills, "
            "realistic articulations, no excessive quantization. Editorial notes must honestly critique "
            "prosody, story, hook and harmonic movement. Different concept from recent catalog below. "
            f"Production day: {self.folder.name}. Previous originals (avoid): "
            + json.dumps(recent, ensure_ascii=False)
        )
        self.folder.joinpath("composition_prompt.txt").write_text(prompt, encoding="utf-8")
        brief = self.structured(prompt, Brief)
        edit_prompt = (
            "Independently edit this original Turkish alternative-pop draft. Improve weak lines and "
            "prosody, remove filler and forced rhyme, sharpen emotional specificity and chorus recall. "
            "Keep exactly the required section order, allowed key and BPM. Do not copy any existing "
            "work or name artists. Check harmonic and dynamic continuity. Return the complete revised "
            "brief, even if most of it is already strong. Explain remaining limitations in editorial_notes. "
            + brief.model_dump_json() + " Required order: " + str([x[0] for x in FORM])
        )
        self.folder.joinpath("editing_prompt.txt").write_text(edit_prompt, encoding="utf-8")
        edited = self.structured(edit_prompt, Brief)
        check_originality(edited, self.catalog, self.folder)
        return edited

    async def render(self, brief, candidate):
        schedule = timeline(brief)
        frame_limit = round(schedule[-1]["end"] * 48000)
        target_bytes = frame_limit * 4
        chunks = bytearray()
        section_index = 0
        log = []
        base = BASE_PROMPT + f" Key {brief.key}, {brief.bpm} BPM, 4/4. Lead motif: {brief.hook}."
        config_fields = dict(
            bpm=brief.bpm, scale=getattr(self.types.Scale, SCALES[brief.key]),
            brightness=0.45, guidance=3.5, temperature=0.9,
            seed=int(hashlib.sha256(f"{self.folder.name}:{candidate}".encode()).hexdigest()[:8], 16) % 2147483647,
            music_generation_mode=self.types.MusicGenerationMode.QUALITY)
        destination = self.folder / "work" / f"candidate_{candidate}.wav"
        destination.parent.mkdir(parents=True, exist_ok=True)

        async with self.client.aio.live.music.connect(model=self.cfg["music_model"]) as session:
            async def steer(index, blend=1.0):
                sec = schedule[index]
                prompts = [self.types.WeightedPrompt(text=base, weight=1.5)]
                if index and blend < 1:
                    prompts.append(self.types.WeightedPrompt(
                        text=schedule[index - 1]["arrangement"], weight=1 - blend))
                prompts.append(self.types.WeightedPrompt(
                    text=f"{sec['arrangement']} Harmony: {sec['chords']}", weight=blend))
                await session.set_weighted_prompts(prompts=prompts)
                previous_density = schedule[max(0, index - 1)]["density"]
                density = previous_density * (1 - blend) + sec["density"] * blend
                await session.set_music_generation_config(config=self.types.LiveMusicGenerationConfig(
                    **config_fields, density=density))
                log.append({"audio_time": len(chunks) / 192000, "section": sec["name"], "blend": blend})

            await steer(0)
            await session.play()
            receiver = session.receive().__aiter__()
            while len(chunks) < target_bytes:
                message = await asyncio.wait_for(anext(receiver), timeout=30)
                if getattr(message, "filtered_prompt", None):
                    raise RuntimeError("Generation prompt was filtered; candidate rejected")
                content = getattr(message, "server_content", None)
                if not content:
                    continue
                if getattr(content, "filtered_prompt", None):
                    raise RuntimeError("Generation prompt was filtered; candidate rejected")
                for chunk in content.audio_chunks or []:
                    chunks.extend(chunk.data)
                seconds = len(chunks) / 192000
                next_index = min(len(schedule) - 1, section_index + 1)
                if next_index != section_index and seconds >= schedule[next_index]["start"]:
                    section_index = next_index
                    await steer(section_index, 0.35)
                elif log[-1]["blend"] < 1 and seconds >= schedule[section_index]["start"] + 2:
                    await steer(section_index, 1.0)
            await session.stop()
        with wave.open(str(destination), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(48000)
            output.writeframes(bytes(chunks[:target_bytes]))
        save_json(self.folder / f"steering_{candidate}.json", {"base_prompt": base, "events": log,
                                                               "timeline": schedule})
        return destination, schedule[-1]["end"]

    def listen(self, mp3, brief):
        prompt = (
            "Listen to the ENTIRE attached instrumental and judge critically, not generously. "
            "Score 0-100: groove (stable human band pocket), instrument_realism (believable acoustic "
            "attack/decay/articulations), hook (recognizable recurring melodic idea), structure "
            "(audible intro/verse/chorus/bridge contrast and coherent ending), mix (balance, dynamics, "
            "no warbles, harshness or artifacts). 80 means convincing and musical, 65 means clearly "
            "demo quality, 50 means synthetic or meandering. Flag any unwanted sung or spoken voices. "
            "Do not score the lyrics as performed: there is no singer. Cite timestamped audible "
            "evidence in observations. If unsure lower the score. Desired production brief: "
            + brief.model_dump_json()
        )
        self.folder.joinpath("listening_prompt.txt").write_text(prompt, encoding="utf-8")
        return self.structured(prompt, ListeningReview, audio=mp3)


def claim(folder, repair_failed=False):
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / "manifest.json"
    if file.exists():
        previous = json.loads(file.read_text(encoding="utf-8"))
        if (repair_failed and previous.get("status") == "failed"
                and previous.get("error_type") in ("InternalServerError", "ServerError", "ServiceUnavailableError")
                and previous.get("request_count", 6) <= 2
                and previous.get("repair_count", 0) == 0 and not (folder / "brief.json").exists()):
            save_json(folder / "pre_repair_manifest.json", previous)
            previous.update(status="claimed", repair_count=1)
            save_json(file, previous)
            return True
        print("Today already has an attempt; no duplicate API calls.")
        return False
    save_json(file, {"date": folder.name, "status": "claimed", "cost_mode": "free_project_only",
                     "output_type": "instrumental_with_separate_lyrics", "commercial_release": "not_approved"})
    return True


def execute(folder, cfg, catalog):
    state_path = folder / "manifest.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state["status"] != "claimed":
        print("Attempt already consumed; refusing automatic retry.")
        return 0
    state.update(status="running", models={"text": cfg["text_model"], "music": cfg["music_model"]},
                 rights_policy=cfg["rights_policy"], quality_assessment="Model estimate, not a professional guarantee")
    save_json(state_path, state)
    producer = None
    try:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ValueError("GitHub/Colab secret GEMINI_API_KEY is missing")
        producer = Producer(key, cfg, folder, catalog)
        brief = producer.compose()
        save_json(folder / "brief.json", brief.model_dump())
        save_json(folder / "arrangement.json", timeline(brief))
        lyrics = "# " + brief.title + "\n\nSeparate lyric sheet: not sung in the instrumental.\n"
        for name, lines in brief.lyrics.model_dump().items():
            lyrics += "\n## " + name + "\n\n" + "\n".join(lines) + "\n"
        folder.joinpath("lyrics.md").write_text(lyrics, encoding="utf-8")
        candidates = []
        for number in range(1, cfg["candidates"] + 1):
            record = {"candidate": number, "accepted": False}
            try:
                # Total timeout also bounds continuous metadata/no-audio streams.
                async def bounded_render():
                    return await asyncio.wait_for(producer.render(brief, number), timeout=360)
                wav, duration = asyncio.run(bounded_render())
                qc = wav_qc(wav, duration)
                record["technical"] = qc
                if qc["passed"]:
                    mp3 = folder / "work" / f"candidate_{number}.mp3"
                    record["mastering"] = mastering(wav, mp3, duration)
                    review = producer.listen(mp3, brief)
                    record["listening"] = review.model_dump()
                    record["score"] = review.average()
                    record["accepted"] = meets_quality(qc, review, cfg)
                    record["file"] = mp3.name
            except Exception as exc:
                # Do not serialize raw API errors/URLs: they can contain credentials or input data.
                record["error_type"] = type(exc).__name__
                record["action"] = "Check quota/model availability and local logs; no paid fallback or retry"
            candidates.append(record)
            save_json(folder / "quality.json", candidates)
        approved = [c for c in candidates if c["accepted"]]
        if approved:
            best = max(approved, key=lambda c: c["score"])
            master = folder / "instrumental.mp3"
            shutil.copyfile(folder / "work" / best["file"], master)
            digest = hashlib.sha256(master.read_bytes()).hexdigest()
            for other in catalog.glob("*/manifest.json"):
                if other.parent != folder and json.loads(other.read_text(encoding="utf-8")).get("audio_sha256") == digest:
                    master.unlink()
                    raise ValueError("Exact audio duplicate found in this catalog")
            state.update(status="ready_for_human_review", title=brief.title,
                         selected_candidate=best["candidate"], audio_sha256=digest)
        else:
            state.update(status="quality_rejected", title=brief.title,
                         action="Listen to candidates in the workflow artifact. No master was accepted.")
        state["request_count"] = producer.calls
        save_json(state_path, state)
        print(f"Production status: {state['status']}")
        return 0 if approved else 2
    except Exception as exc:
        state.update(status="failed", error_type=type(exc).__name__,
                     action="Check free quota/model access. Paid fallback is disabled.",
                     request_count=producer.calls if producer else 0)
        save_json(state_path, state)
        print("Production failed safely; see manifest. No paid fallback was used.")
        return 1
    finally:
        if producer:
            with contextlib.suppress(Exception):
                producer.client.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["claim", "generate", "run", "validate"])
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--catalog", type=Path, default=Path("catalog"))
    parser.add_argument("--date")
    parser.add_argument("--repair-failed", action="store_true",
                        help="One explicit recovery of a server failure before composition completed")
    args = parser.parse_args()
    cfg = load_config(args.config)
    day = args.date or datetime.now(ZoneInfo(cfg["timezone"])).date().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("Date must be YYYY-MM-DD, never a filesystem path")
    datetime.strptime(day, "%Y-%m-%d")
    folder = args.catalog / day
    if args.mode == "validate":
        print("Configuration valid: free-project-only, paid music disabled.")
        return 0
    if args.mode in ("claim", "run"):
        fresh = claim(folder, repair_failed=args.repair_failed)
        if args.mode == "claim":
            if os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                    output.write(f"fresh={str(fresh).lower()}\n")
                    output.write(f"day={day}\n")
            return 0
        if not fresh:
            return 0
    return execute(folder, cfg, args.catalog)


if __name__ == "__main__":
    sys.exit(main())
