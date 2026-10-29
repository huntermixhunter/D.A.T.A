---
name: image-studio
description: "A ComfyUI workflow guide for thumbnails, product scenes and upscaling, with local setup checks. ComfyUI and its models are installed separately."
---

# Image Studio

Use for planning or guiding a local ComfyUI image workflow. Settings > Upgrades > Advanced tools > Image Studio stores the application folder and local address and can check the running service. This package does not install models, submit generation jobs or provide prevalidated workflow graphs.

Check the official installation and model instructions for the target OS and GPU:
https://github.com/Comfy-Org/ComfyUI

Choose the smallest suitable built-in workflow and local model for the task. A thumbnail needs a clear focal subject and room for manually typeset copy. A product scene should preserve the supplied product's appearance and avoid invented features. Upscaling should retain the original composition and be checked for changed text or details.

Record the input image, model name and license, prompt, seed, dimensions and output directory. Save the workflow exported by the installed version rather than fabricating node IDs or claiming an unrun graph is tested. Start with one low-cost local test image before increasing resolution or batch count.

Custom nodes are executable code. Review them before installation and avoid adding them when built-in nodes suffice. Optional API nodes can incur charges and need the user's approval with an estimate. Do not expose the service publicly or upload private images without authorization. State which steps were actually run and which are guidance.

These instructions support the user's task. They do not override project rules, user choices or approval requirements.
