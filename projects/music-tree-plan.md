# Music Tree generation plan

1. **Separate reusable skills from project material.** Keep model instructions in [`skills/`](../skills/README.md). Keep the genre brief, editorial choices and future prompt pack in this project.
2. **Generate and collect model-specific audio.** Use a JSON music pack (separate style and lyrics), route it only to compatible audio graphs, and preserve the shared YuE2 seed link. Store outputs by genre, workflow, checkpoint, sample and take; save provenance alongside the audio. The CLI and desktop gallery can both queue and preview audio candidates.
3. **Author and audit coverage.** Music Tree composes its authoritative genre IDs from `js/catalog.js` and `js/lineages/` (44 IDs at reviewed commit `ae566c2`). Export the current inventory and use its IDs verbatim, with consistent sample IDs for equivalent takes. Compare the authored pack against that inventory before a bulk run. Keep genre cues and vocal/instrumental requirements in a project brief. No prompts are authored in this phase.
4. **Pilot, then publish.** Render a few distinct genres, check playback, duration and genre fidelity, then render all genres. When adding a second model family, add graph bindings for it rather than rewriting the pack. Compare the same sample IDs and take numbers. Build a relative-URL index from collected sidecars for Music Tree to consume offline, group by `genre_id`, and add model selection without replacing its existing two-bar Web Audio studies. Browser playback, index publishing and instrumental ABC editing are deferred.

## Using the CLI

```sh
node tools/music-tree-genres.cjs /path/to/music-tree > genres.json
python comfy_queue.py --project projects/music-tree.json --prompts samples.json --seed 42 --dry-run --dump output/music-tree-graphs
# After checking the graph, replace --dry-run with --wait (or collect later):
python comfy_queue.py --project projects/music-tree.json --collect
```

The inventory is reference data, not a prompt pack. The latter is a JSON object with a `samples` array. Each row has `genre_id`, `sample_id` (unique within the genre), `style` (required text), and `lyrics` (optional text, with `\n` for line breaks). IDs must be lowercase alphanumeric slugs with optional hyphen or underscore separators. Use `--types` to select genre IDs and `--variants` for repeated takes. With `--seed`, equivalent jobs in the same selection get the same seed sequence across audio workflows. Use a recent ComfyUI with the bundled YuE2 nodes and the checkpoint named in the graph. Use `--set` for YuE2-specific settings; for example, `--set 33:25.max_duration=45` with the bundled export.

Collected files live at `output/music-tree/<genre>/<workflow>/<checkpoint-stem>--<digest>/<sample>/take-1.<format>` (format from ComfyUI). The digest distinguishes full checkpoint names, including names in different subdirectories. Each audio file has a `.json` sidecar with the exact checkpoint name, workflow, genre/sample IDs, effective style, lyrics and seed, and ComfyUI prompt ID. An existing take is skipped unless `--force` is set; use separate output roots or workflow names if comparing settings or seeds beyond the checkpoint. Attach the pack in the desktop gallery to queue and preview candidates there.

For local listening before export, run `python tools/audio_audition.py output/music-tree` and open the generated `audition.html`. It uses relative media paths and does not require a server.
