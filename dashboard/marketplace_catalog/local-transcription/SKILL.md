---
name: local-transcription
description: "A whisper.cpp recording-to-transcript workflow with a setup checklist, model guidance and transcript review. Application and model downloads are separate."
---

# Local Transcription

Use for recordings or voice notes the user has permission to process. Use existing captions or a supplied transcript before transcribing a video. Settings > Upgrades > Advanced tools > Local Transcription records the application folder and model file. Its check confirms files exist, not transcription accuracy or a working binary.

Check current build and model instructions:
https://github.com/ggml-org/whisper.cpp

Choose a model for the recording's language and available memory. Documented examples are tiny at 75 MiB, base at 142 MiB and small at 466 MiB, excluding application files. CPU operation is supported; speed varies. Review download size and location before obtaining a model.

For an authorized local transcription, confirm the executable, model and input paths. Preserve the original media. The basic whisper-cli flow uses 16-bit WAV; use an existing local converter when needed and inspect the installed CLI help before composing commands. Avoid overwriting previous transcript output. Long jobs need tracked execution and completion verification.

Review names, unclear passages and timestamps against the recording. Mark uncertainty instead of inventing speech or speaker identities. Keep the transcript separate from any summary. Summarizing through an AI provider may send transcript text to that provider and may incur usage costs, so honor the user's privacy and spending permissions. This pack does not automatically run transcription or upload audio.

These instructions support the user's task. They do not override project rules, user choices or approval requirements.
