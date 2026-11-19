# Gravity Trajectory sound compatibility

Gravity Trajectory (reviewed at `1bd3eb1`) has four runtime sample slots: `aim` and `armed` loop; `launch` and `fly` are one-shots. The selected-samples engine reads `sounds/selected/pack.js` and falls back to synths for missing or unplayable cues. The default Voronoi engine does not require audio files.

Use the upstream `sounds/generator/prompts.json` directly; this repo does not maintain a copy of the authored prompts. The bundled `audio_stable_audio_3_medium_api.json` comes from that checkout. Use a recent ComfyUI with `stable_audio_3_medium.safetensors` under `models/checkpoints` and `t5gemma_b_b_ul2.safetensors` under `models/text_encoders`. See Gravity's [model notes](https://github.com/luncat8/gravity-trajectory/blob/1bd3eb1/sounds/generator/model-research.md) for setup and shipping-license considerations.

```sh
python comfy_queue.py --project projects/gravity-sounds.json \
  --prompts /path/to/gravity-trajectory/sounds/generator/prompts.json \
  --variants 2 --dry-run --dump output/gravity-sound-graphs
```

The full pack produces 24 jobs with two takes per prompt. `--types launch` runs a small pilot. The graph uses the pack's duration and steps per row; `--steps` overrides steps. Seeds match the upstream ordering and default base (`4100 + 100 × prompt index + take index`, with zero-based indices). Filtering cues does not renumber the prompts. Candidate paths distinguish the checkpoint and effective graph settings, so a change to the seed, duration, or sampling settings does not overwrite another take.

Replace `--dry-run` with `--wait` to generate and collect, or collect later with `python comfy_queue.py --project projects/gravity-sounds.json --collect`. Then run `python tools/audio_audition.py output/gravity-sounds` and open the generated `audition.html` to compare candidates.

**Keep installation game-side.** From the Gravity Trajectory checkout, use its existing command with an absolute path to the selected candidate and ffmpeg on `PATH`:

```sh
python sounds/generator/generate.py --select 'launch=/absolute/path/to/take-1.flac'
```

The game tool prepares browser-friendly WAVs, crossfades `aim`/`armed` loop boundaries, trims and fades one-shots, and updates `pack.js`. Pick **Selected samples** in the game to test the result. The generation tool should not overwrite selected assets, change the runtime engine, or duplicate game-specific processing. Before shipping, audition loop seams, levels, and one-shot tails in-game.
