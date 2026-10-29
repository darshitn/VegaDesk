"""Offline evaluation of owner-supplied audio. Never records or dispatches commands.

JSON manifest: [{"file":"clip.wav", "text":"reference transcript"}, ...].
Optional wake evaluation adds {"file":"clip.wav", "wake":true/false} rows.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def words(text):
    return re.findall(r"\w+", text.lower())


def word_errors(reference, hypothesis):
    """Levenshtein distance on normalized words; not a semantic quality score."""
    ref, hyp = words(reference), words(hypothesis)
    previous = list(range(len(hyp) + 1))
    for i, a in enumerate(ref, 1):
        current = [i]
        for j, b in enumerate(hyp, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1], len(ref)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--wake-model", type=Path)
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()
    if not 0 < args.threshold <= 1:
        parser.error("threshold must be in (0, 1]")
    rows = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        parser.error("manifest must be a non-empty list")
    import numpy as np
    import voice_service
    wake_model = None
    if args.wake_model:
        from openwakeword.model import Model
        wake_model = Model(wakeword_models=[str(args.wake_model.resolve())], inference_framework="onnx")
    report = {"asr": [], "wake": [], "boundary": "Offline supplied clips only; no microphone, UI, command execution or end-to-end latency test. False-wake events use evaluator-specific rising edges/cooldown, not the production capture/reset state machine."}
    if args.wake_model:
        report["wake_model_sha256"] = hashlib.sha256(args.wake_model.read_bytes()).hexdigest()
    total_errors = total_words = 0
    false_events = negatives_seconds = positives = hits = 0
    cold_load_seconds = None
    for row in rows:
        path = (args.manifest.parent / row["file"]).resolve()
        with wave.open(str(path), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
                parser.error(f"{path.name}: expected 16kHz mono PCM16 WAV")
            audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
        seconds = len(audio) / 16000
        if "text" in row:
            if not words(row["text"]):
                parser.error("ASR reference text must contain words; use wake=false for negative audio")
            if cold_load_seconds is None:
                start = time.perf_counter()
                voice_service._get_whisper()
                cold_load_seconds = time.perf_counter() - start
            start = time.perf_counter()
            transcript = voice_service.transcribe_source(str(path))
            elapsed = time.perf_counter() - start
            errors, count = word_errors(row["text"], transcript)
            total_errors += errors
            total_words += count
            report["asr"].append({"file": row["file"], "reference": row["text"], "transcript": transcript,
                                  "decode_seconds": round(elapsed, 3), "audio_seconds": seconds,
                                  "real_time_factor": round(elapsed / seconds, 3) if seconds else None})
        if "wake" in row:
            if type(row["wake"]) is not bool:
                parser.error("wake labels must be JSON true/false")
            if wake_model is None:
                parser.error("--wake-model is required for wake-labelled rows")
            wake_model.reset()
            peak = 0.0
            events = 0
            was_above = False
            last_event = -32000
            for offset in range(0, len(audio), 1280):
                chunk = audio[offset:offset + 1280]
                if len(chunk) < 1280:
                    chunk = np.pad(chunk, (0, 1280 - len(chunk)))
                scores = wake_model.predict(chunk)
                score = max((float(value) for value in scores.values()), default=0.0)
                peak = max(peak, score)
                above = score >= args.threshold
                if above and not was_above and offset - last_event >= 32000:
                    events += 1
                    last_event = offset
                was_above = above
            detected = peak >= args.threshold
            report["wake"].append({"file": row["file"], "expected": row["wake"], "detected": detected,
                                   "peak": round(peak, 4), "events": events})
            if row["wake"]:
                positives += 1
                hits += int(detected)
            else:
                negatives_seconds += seconds
                false_events += events
    report["summary"] = {"normalized_word_error_rate": total_errors / total_words if total_words else None,
                         "cold_load_seconds": cold_load_seconds,
                         "median_decode_seconds": statistics.median(r["decode_seconds"] for r in report["asr"]) if report["asr"] else None,
                         "positive_wake_clips": positives, "wake_recall": hits / positives if positives else None,
                         "negative_audio_hours": negatives_seconds / 3600,
                         "false_wake_events_per_hour": false_events / (negatives_seconds / 3600) if negatives_seconds else None,
                         "threshold": args.threshold, "event_cooldown_seconds": 2}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
