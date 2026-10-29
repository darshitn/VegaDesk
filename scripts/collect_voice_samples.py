"""Owner-operated local recording session. No microphone opens before Enter.

Uses the selected OS/default input. Close VEGA voice mode first. Saves prompted
clips only; this is not background monitoring and nothing is uploaded.
"""
import argparse
import json
from pathlib import Path
import re
import wave

COMMANDS = [
    "Set a timer for twenty five seconds",
    "Add a new task to review sorting algorithms",
    "Open my coding workspace",
    "Remind me to revise binary search tomorrow",
    "I have forty five minutes for practice today",
    "Show my incomplete tasks",
    "Start a focus session for thirty minutes",
    "Explain the difference between a vector and an array",
    "Create a note about Python virtual environments",
    "Stop speaking and listen to my next question",
]
NEAR_PHRASES = ["Hey Vijay", "I prefer vegan food", "Las Vegas is a city", "Hey Google", "Please open the browser"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("voice-lab-local"))
    parser.add_argument("--session", required=True, help="Unique name, e.g. heldout-01")
    parser.add_argument("--split", choices=("train", "validation", "test"), default="validation")
    parser.add_argument("--device", type=int, help="Optional sounddevice input index; default is OS input")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.session):
        parser.error("session must contain only letters, numbers, underscores or hyphens")
    target = args.output.resolve() / args.session
    if target.exists():
        parser.error("session already exists; choose a new name (no overwrites)")
    print(f"Local destination: {target}\nSplit: {args.split}. No audio is uploaded.")
    print("30 wake clips, 10 command clips and 5 near-phrase clips.")
    print("Use your usual microphone; vary natural pace/distance between groups. Ctrl+C stops.")
    input("Press Enter to begin the prompted session, or Ctrl+C to cancel: ")
    import sounddevice as sd
    import numpy as np
    prompts = [("Hey Vega", {"wake": True}) for _ in range(30)]
    prompts += [(text, {"text": text}) for text in COMMANDS]
    prompts += [(text, {"wake": False}) for text in NEAR_PHRASES]
    target.mkdir(parents=True, exist_ok=False)
    rows = []
    try:
        for i, (prompt, labels) in enumerate(prompts, 1):
            duration = 3 if "wake" in labels else 6
            input(f"\n{i}/{len(prompts)} — say: {prompt}\nPress Enter; recording then lasts {duration}s: ")
            print("RECORDING")
            audio = sd.rec(duration * 16000, samplerate=16000, channels=1, dtype="int16", device=args.device)
            sd.wait()
            sd.stop()  # close the stream while the owner reviews/prepares
            if not np.any(audio):
                print("Warning: digital silence. Check the selected input before continuing.")
            filename = f"clip-{i:03}.wav"
            with (target / filename).open("xb") as raw:
                with wave.open(raw, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(16000)
                    wav.writeframes(audio.astype("<i2").tobytes())
            rows.append({"file": filename, **labels, "split": args.split, "session": args.session})
            (target / "manifest.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
            print("Saved locally. Recording stopped.")
    finally:
        sd.stop()
        print(f"\n{len(rows)} completed clips remain at {target}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled. Previously completed clips remain local.")
