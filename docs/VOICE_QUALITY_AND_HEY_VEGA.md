# Voice quality and “Hey Vega”

28 October 2026. Owner selected **Hey Vega**, pronounced **VAY-guh**. This is the immediate work priority before the desktop companion implementation.

> Update: owner recordings have now been received and locally measured. See [first validation assessment](VOICE_VALIDATION_2026-10-28.md) and [prepared Colab notebook](HEY_VEGA_COLAB.ipynb). Historical “not performed yet” statements below describe the initial correction run; no custom model has been trained yet.

## Current evidence and first fixes

VEGA already uses faster-whisper (`small.en`, CPU int8, English, beam size 1). Adding Whisper is not the missing step. Two independent things need evaluation: speech transcription and wake detection. A wake classifier is not a transcription model.

Source inspection found a concrete endpointing defect: each 80ms microphone chunk adds 1,280 samples, but the VAD loop stopped after its first speech-positive 512-sample frame. Old speech accumulated in the remainder and could postpone detecting silence until the 15-second recording cap. The corrected production helper consumes every full frame and keeps fewer than 512 samples. Synthetic regression covers eight seconds of speech followed by silence.

Also corrected: voice events are scheduled onto their owning asyncio loop; concurrent model initialization is locked; manual/wake decoder iteration is serialized; a validated `WHISPER_MODEL_PATH` is actually passed to the loader. CPU int8 remains the default, with explicit `WHISPER_DEVICE=cuda` and optional `WHISPER_COMPUTE_TYPE` for a measured GPU trial. No GPU speedup is claimed.

Installed package metadata observed: faster-whisper 1.2.1, CTranslate2 4.8.1, openWakeWord 0.6.0 and torch 2.13.0+cpu. PyTorch's CPU build does not by itself determine CTranslate2 GPU capability. A CTranslate2 runtime probe did not finish promptly and was stopped; usable CUDA speech inference remains unverified.

## What remains before calling capture quality fixed

- Measure end-of-speech delay, decoder time and total time separately on the same recordings. The new offline evaluator measures decoding, not live endpoint latency.
- Ordinary wake currently drops its detection chunk and has no pre-roll; very immediate commands can lose their onset. A bounded pre-roll needs testing and wake-prefix handling. Never reuse pre-acknowledgement/TTS audio during interruption.
- Recording is command-oriented: one-second silence timeout, 15-second cap. Longer dictation needs a separate mode, pause handling and visible finish/cancel controls.
- Capture/queue lifecycle needs a dedicated correction: bounded transcription admission, stale-result invalidation after disable/reconnect, reset on every stream exit, device-change reconnect and overrun reporting. A serialization lock prevents competing decoders but does not bound waiting request threads.
- Initial mic calibration currently discards two seconds while claiming ready. The energy estimate can be contaminated by speech. Readiness and robust calibration need native tests.
- `_load_silero_vad()` still uses torch.hub and can fetch code/assets if uncached. Replace it with verified local assets before claiming entirely offline startup. No voice startup was performed in this run.
- Browser manual capture uses its own default device, separate from the backend mic selection; device mapping, audio processing and request cleanup need review.

Do not lower the wake threshold or replace `small.en` with a smaller model to hide these defects. Benchmark accuracy and latency together. GPU execution needs compatible CUDA/cuDNN libraries; installing CPU PyTorch does not provide those automatically. [faster-whisper setup](https://github.com/SYSTRAN/faster-whisper)

## Professional wake-word procedure

Do not rename `hey_jarvis.onnx` and call it trained. Follow openWakeWord's detailed training workflow: diverse synthetic positives, appropriate negative speech/noise, acoustic augmentation, then a separate realistic evaluation set. Its quick notebook is a prototype route; it explicitly does not guarantee deployment quality. Inspect licenses for datasets, speech voices, shared feature models and produced artifacts. [Upstream training and evaluation](https://github.com/dscripka/openWakeWord#training-new-models)

For this owner-specific model:

1. Freeze the phrase and pronunciation: Hey Vega / VAY-guh. Include natural pace, quieter speech, different distances and microphone positions. Do not synthesize a clone of the owner's voice without a separate request.
2. Prepare a versioned training environment separate from the running app. Pin upstream commit and dependency versions; Python 3.14 compatibility must be checked before choosing the training interpreter. Verify asset sizes, disk space and licenses before downloads. No paid compute is required by default.
3. Generate several thousand varied synthetic positives using an inspected upstream pipeline; include the intended pronunciation and room/noise augmentation. Owner recordings personalize/evaluate the result; a small personal dataset alone does not establish general robustness.
4. Split real recordings by **recording session before augmentation**. Keep training, validation and final test distinct; never tune the threshold on final test clips. Include held-out owner speech and near phrases such as “hey Vijay,” “vegan,” “Las Vegas,” ordinary conversation and VEGA's own spoken replies.
5. Select a threshold using validation recall versus false-wake events/hour. An optional speaker verifier can reduce false activations but may increase missed wakes; measure both effects.
6. Evaluate the frozen artifact on held-out recordings and extended negative audio. Proposed initial owner gate: >=95% recall across at least 60 diverse held-out wake clips, and <0.5 false wakes/hour over substantial representative negative audio. These are targets, not achieved results or statistically guaranteed population rates. Zero false wakes in a short recording is weak evidence; around six hours with zero events only places an approximate 95% Poisson upper bound near 0.5/hour under stationary assumptions.
7. Test on the actual laptop at 0.5/1/2m, with fan/room noise, speakers playing and interruption during TTS. Record detection delay, misses and false wakes. Speaker echo remains an acoustic problem; queue purging is not acoustic echo cancellation.
8. Version the ONNX model with SHA256, source/license manifest, split identifiers, selected threshold and evaluation report. Only promote it after these checks. Keep the previous artifact available for an explicit rollback.

## Local integration

The runtime now accepts a finite `VEGA_WAKE_THRESHOLD` in (0, 1]. Freeze the evaluated value with the artifact and set that exact value at deployment. Offline rising-edge/cooldown metrics differ from production wake/reset/capture transitions; they support comparisons, while native tests remain the promotion gate.

PowerShell, **after a real trained artifact exists**:

```powershell
$env:VEGA_WAKE_PHRASE = 'Hey Vega'
$env:VEGA_WAKE_MODEL_PATH = 'D:\your-local-models\hey_vega.onnx'
$env:VEGA_WAKE_THRESHOLD = '0.5' # example only: use the EXACT frozen validation threshold
npm run dev:beta:voice
```

These process variables do not edit `.env`. An explicitly missing/empty custom model now fails closed instead of silently loading Jarvis. The old `JARVIS_WAKE_MODEL_PATH` remains compatible. The phrase label is configuration, not proof the model recognizes it. Ordinary beta remains microphone-off. The active default is still the existing Jarvis model until a real Vega artifact is configured; UI naming should follow actual model configuration in the integration milestone.

## Offline accuracy and speed evaluation

For a guided local recording session, first close the app's active voice mode, then run from the project root:

```powershell
python scripts/collect_voice_samples.py --session validation-01 --split validation
```

The microphone opens only after the explicit Enter prompts and stops after each clip. This collects 30 wake clips, 10 commands and 5 near phrases into ignored `voice-lab-local/validation-01`, with a manifest. Use a different session name and `--split test` for held-out recordings on another occasion. Default input is the OS device; `--device` selects its sounddevice index. Listen to and label-check the files before evaluation. Short negatives are an initial check, not the required extended false-wake test. Do not mix final test clips into training or threshold tuning. This collection command has not been run by the agent.

Prepare owner-approved WAV clips: mono, PCM16, 16kHz. Store them outside the repository, together with a JSON manifest. Audio contains personal information; no upload is required. Example manifest:

```json
[
  {"file": "command-01.wav", "text": "Set a timer for twenty five seconds"},
  {"file": "command-02.wav", "text": "Add a new task to review sorting algorithms"},
  {"file": "wake-positive-01.wav", "wake": true},
  {"file": "room-negative-01.wav", "wake": false}
]
```

Use separate ASR-only and wake-only manifests if the trained wake model is not yet available:

```powershell
python scripts/evaluate_voice.py 'D:\voice-evaluation\asr.json'
python scripts/evaluate_voice.py 'D:\voice-evaluation\wake-test.json' --wake-model 'D:\your-local-models\hey_vega.onnx' --threshold 0.5
```

The evaluator uses installed local assets; it never records, starts the app, dispatches transcripts as commands or explicitly downloads models. openWakeWord still requires its local shared feature assets. It reports normalized word error rate, cold load, median decoding time, wake recall, false events/hour, negative-audio duration and model hash. Wake events use a documented two-second cooldown. Clip detection is not a latency/localization measurement. Threshold 0.5 is an example, not a tuned recommendation.

Build a representative ASR set of 30–50 owner-spoken commands/dictations: timers, task names, coding terms, numbers, natural pauses and room noise. Compare the same set before/after and across approved compute settings. Preserve raw hypotheses; never silently “correct” critical numbers or execute uncertain transcripts. Large models or paid ASR can be considered only if pipeline corrections and measured local configurations are insufficient.

## Current milestone boundary

Code corrections and synthetic regressions do not establish ChatGPT-level recognition. No microphone recordings, owner-audio benchmark, synthetic training-data generation, model training, model download or accuracy claim has occurred yet. The next required input is a consented recording session and the training-asset setup; the next engineering work is capture lifecycle/pre-roll correction. A new trained artifact must not be claimed until it exists and passes held-out evaluation.
