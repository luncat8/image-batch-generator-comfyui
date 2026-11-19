---
name: stable-audio-3
description: Write short SFX, isolated instrument, and instrumental music prompts for Stable Audio 3.
---

# Stable Audio 3 prompt writing

- Match `TrackType` to the job: `SFX` for a sound event, `Instrument` for an isolated instrument, or `Music` for a composed cue. For instrumental music, specify `VocalType: Instrumental`.
- Describe what should be audible: source, environment, timbre, attack, sustain, decay, and perspective. Distinguish a steady loop candidate from a one-shot with a tail. For music, add instrumentation, groove, tempo, and how the cue should end.
- Keep `Length: … seconds` consistent with the latent duration. Duration is also a graph parameter; prompt text alone does not set it. Listen for loop seams and unintended events before selecting a take.
- Start from the distilled Medium workflow's defaults (8 steps, CFG 1, LCM/simple). Record changes and audition the results rather than assuming a higher step count is better. Other checkpoints may need different graphs and settings.

Sources: [Stability AI prompting guide](https://github.com/Stability-AI/stable-audio-3/blob/main/docs/guides/prompting.md), [ComfyUI workflow guide](https://docs.comfy.org/tutorials/audio/stable-audio/stable-audio-3), and [Gravity Trajectory model notes](https://github.com/luncat8/gravity-trajectory/blob/1bd3eb1/sounds/generator/model-research.md).
