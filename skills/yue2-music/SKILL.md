---
name: yue2-music
description: Write style and lyric inputs for the YuE2 text-to-music workflow.
---

# YuE2 prompt writing

- **Style:** Describe the genre or subgenre, characteristic rhythm or groove, tempo, core instruments, and arrangement or production traits. For songs, add language and vocal character. Use cues that distinguish the genre from related styles; avoid contradictory requirements.
- **Lyrics:** Put only sung words and structure tags such as `[Verse]`, `[Chorus]`, `[Bridge]`, and `[Outro]` here. Preserve line breaks. Put production directions in Style, not Lyrics.
- **Planning:** The bundled ComfyUI graph uses full ABC planning (melody and chords). Melody-only planning leaves accompaniment freer; disabling ABC skips the symbolic plan. Max duration is a ceiling, not a promised length.
- **Instrumentals:** An empty lyric string does not guarantee no vocals. For dependable instrumentals, see the upstream instrumental workflow, which generates a plan, moves vocal notes to the instrumental track, and renders the edited plan. The bundled graph does not implement that edit.
- **Comparison:** Reuse comparable inputs, seeds, and planning settings when comparing models. Keep the exact checkpoint, workflow, and inputs with each result. Listen for genre cues, unwanted vocals, and truncation rather than treating a successful render as a quality check.

Sources: `temp-YuE2.txt` (ComfyUI workflow notes), the upstream [YuE2 Music Skill](https://github.com/multimodal-art-projection/YuE/blob/main/skills/yue2-music/SKILL.md), [instrumental skill](https://github.com/multimodal-art-projection/YuE/blob/main/skills/yue2-music/instrumental/SKILL.md), and [generation guide](https://github.com/multimodal-art-projection/YuE/blob/main/docs/generation.md). The upstream skill also covers the standalone runtime, covers, transcription, and score editing; those are not features of this generator.
