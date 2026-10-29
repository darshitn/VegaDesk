# Voice recordings: first local assessment

28 October 2026. This is an initial measurement, not acceptance of the voice system or a trained wake model.

## Received and preserved

`voice-lab-local/validation-01` contains 45 mono PCM16/16kHz clips: 30 wake phrases, 10 commands and 5 near-phrase negatives. All files were readable, had nonzero audio, and showed no digitally clipped samples. These checks do not prove correct spoken content. The fifth wake clip had substantially higher amplitude than its peers and should be reviewed before scoring.

Original clips and manifest remain unchanged. SHA256 values and split metadata are saved locally in `validation-freeze.json`. The owner confirmed that clip-031 began with Hey Vega / a pause or cutoff, rather than the complete expected timer sentence. Preserve it but exclude it from ASR accuracy comparisons. Do not use this validation set as training data.

## Measured transcription

Same existing `small.en`, English, CPU int8; local files only. No transcripts were sent to the action dispatcher.

| Setting | Eligible clips | Word edits / reference words | Raw word error rate | Median decode time |
|---|---:|---:|---:|---:|
| Current beam size 1 | 9 | 9 / 62 | 14.5% | 1.727s |
| Trial beam size 5 | 9 | 8 / 62 | 12.9% | 1.873s |

The first full ten-clip run measured 1.731s median decoding and 13.25s model cold loading. Removing the owner-confirmed mismatched reference gives the nine-clip comparison above. Raw WER normalizes case/punctuation but counts equivalent number formatting such as “forty five” versus “45” as edits. It is not a semantic task-success score. Four of nine eligible commands matched exactly after case/punctuation normalization in each setting. Small sample size and one recording session limit generalization.

Higher beam search recovered one additional word edit overall with greater median decoding time. It is not enough evidence to change the production default. These are supplied-file decoding timings, not live speech-end-to-action latency. VAD onset, endpointing, queuing and UI delivery need separate native measurements.

Detailed hypotheses/timings are kept with the recordings in ignored local reports:
- `asr-baseline-20261028-154109.log`
- `asr-beam5-20261028-154311.json`
- `asr-cuda-20261028-154353.log`

## GPU trial

CTranslate2 reported one CUDA device and supported compute types. A real `small.en` CUDA/int8_float16 inference attempt then failed with **Library cublas64_12.dll is not found or cannot be loaded**. This proves GPU enumeration is not sufficient. CPU settings were not changed, no model or CUDA libraries were installed, and no GPU performance claim is made. Any follow-up GPU setup must verify compatible cuBLAS/cuDNN and repeat the same clips before changing defaults.

## Chosen training route and prepared notebook

Owner selected Google Colab training on synthetic/public audio followed by local evaluation. Prepared [HEY_VEGA_COLAB.ipynb](HEY_VEGA_COLAB.ipynb). Open Colab → File → Upload notebook, select this file, and choose a free GPU runtime if offered. Run cells in order; explicit prompts precede environment setup and large downloads. No owner recordings or Drive mount are required. Download the resulting experimental ONNX, metadata and environment freeze only after actual training completes.

The wrapper pins openWakeWord commit `368c03716d1e92591906a84949bc477f3a834455` and Piper generator commit `2971426a55072f7d22fec416ca7800df8bd23207`. It creates an isolated Python3.10 environment, adapts the inspected upstream notebook to Hey Vega, includes near phrases, and targets 20,000 synthetic training positives and 2,000 synthetic validation positives. Selected wrapper dependency constraints were checked against public package metadata, including NumPy/protobuf compatibility. Wrapper Python syntax and absence of upload/Drive-mount cells were checked locally. **Remote installation, dependency resolution, notebook execution and model training remain untested.** Stop on setup errors; do not assume a green artifact from a partially failed run.

One upstream feature file is **17,280,000,128 bytes**, plus a 184,836,608-byte validation file and other training assets. The wrapper requires at least60GiB free before setup; total space/runtime depends on downloaded datasets and generated clips. Free Colab resources are not guaranteed and sessions can terminate. No paid runtime was enabled. [Colab limits](https://research.google.com/colaboratory/faq.html)

Upstream code is Apache-2.0; dataset, TTS voice and model terms require separate review before redistributing an artifact. “Experimental” must remain in the artifact identity until untouched local validation, threshold selection, a separate final test session and native interruption/noise tests pass. The training targets are not achieved model metrics. [Upstream training notebook](https://github.com/dscripka/openWakeWord/blob/368c03716d1e92591906a84949bc477f3a834455/notebooks/automatic_model_training.ipynb)

## Next steps

Execute the prepared notebook in the owner's Colab session, diagnose any setup incompatibility, then evaluate its actual ONNX locally against the frozen wake clips. Select threshold on validation only. Collect a separate final test session and extended negatives after model/threshold selection. Existing 15 seconds of near-phrase negatives cannot establish a meaningful production false-wake rate. Capture lifecycle/pre-roll correction and optional local GPU dependency setup remain separate engineering work.
