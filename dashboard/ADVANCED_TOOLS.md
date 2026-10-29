# Advanced tools and the starter collection

Marketplace is the discovery and workflow library. Settings > Upgrades > Advanced
tools configures optional applications on the computer running DATA.

The free starter collection adds these original instruction packs:

| Pack | Purpose |
| --- | --- |
| Web Design Studio | Guided website builds using the existing core design skills |
| Brand Kit | Palette, type, voice and practical brand guidance |
| Content Repurposer | Social, carousel and email drafts from supplied material |
| Website Audit | Usability, accessibility and copy findings with evidence |
| Client Proposal Kit | Scope, deliverables, milestones and onboarding drafts |
| Research Desk | Source-linked research and comparisons |
| Marketplace Creator Kit | Valid declarative packages and accurate listing copy |
| FramePack Video Studio | Image-to-video guidance and a setup checklist |
| Image Studio | ComfyUI workflow guidance and a setup checklist |
| Local Transcription | whisper.cpp guidance and a setup checklist |

All ten are version 1.0.0 and use MIT for their original package content. Third-party
applications, weights, scripts and full skill resources are not included and retain
their own licenses. The design bundle already ships separately with DATA.
The three advanced packs use the existing skill and checklist-page components.
Preparing a skill request leaves an unsent chat draft. External AI usage may consume
the selected provider's allowance or incur charges.

Packages start unverified. Parser validation does not grant an independent review
badge. Existing reviews remain attached to the exact previously reviewed bytes.

## Setup controls

Each application shows requirements, download guidance, costs, a basic hardware
assessment and official instructions. Users can save an application folder, plus
a loopback URL for FramePack/ComfyUI or a model file for whisper.cpp.
Save and check stores the displayed fields before checking them.

- Opening Settings does not probe application servers or write settings.
- File checks inspect expected file locations without running executables.
- Web checks make one bounded GET to a fixed endpoint: FramePack `/config` or
  ComfyUI `/system_stats`. Redirects and proxies are disabled. Only literal
  loopback HTTP addresses are used; `localhost` is normalized without DNS.
- A matching response reports connection status and enables Open local app.
  It does not establish that models are loaded or generation works.
- Forget saved locations removes the record and keeps application/model files.
- No installer, process launcher, model downloader, render queue, transcription
  runner, billing call or generation API is implemented by this feature.

The external app handles generation, outputs and its own gallery. Image Studio
provides workflow guidance, not tested ComfyUI graph files.

## Storage and access

The bridge saves settings in the active local profile's
`users/<profile>/advanced-tools/advanced_tools.json`. This is distinct from a
marketplace login and is excluded from release archives. The preview uses
`<preview-data-dir>/advanced-tools/advanced_tools.json` instead.

```text
GET  /advanced-tools/catalog
POST /advanced-tools/<tool-id>/save
POST /advanced-tools/<tool-id>/check
POST /advanced-tools/<tool-id>/forget
```

Tool IDs are `framepack`, `comfyui` and `whispercpp`. Setup requires a direct
loopback request, existing bridge authentication, a matching browser origin when
present, and `X-Data-Request: 1`. Reverse-proxy and remote requests are refused.
Bodies are bounded JSON objects. Settings are validated before atomic replacement;
unreadable settings are reported rather than silently overwritten. No paths or
connection records are sent to the shared marketplace service.

## Official sources

Requirements and download guidance were checked on 2026-10-29. Consult these
sources again before installing because releases can change.

https://github.com/lllyasviel/FramePack

https://github.com/Comfy-Org/ComfyUI

https://github.com/ggml-org/whisper.cpp

## Development checks

```text
python -m unittest discover -s dashboard/tests -p test_advanced_tools.py -v
python -m unittest discover -s dashboard/tests -p test_marketplace.py -q
python dashboard/tests/advanced_tools_browser.py
```

The browser check uses an isolated database, temporary file markers and a simulated
local app. It does not install software, load models or contact AI providers.
Real generation and physical phone use are outside these checks.
