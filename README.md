# image-batch-generator-comfyui

Standalone ComfyUI batch generator for game art: queue caption packs from a
project's `prompts.md`, render them through shared ComfyUI API-format
workflows, collect the results, and browse them in a thumbnail gallery.

Extracted from `gravity-trajectory/photos/generator` so the tool is
decoupled from any single game repo. Each game keeps its own prompts; this
repo holds the tool, the shared workflows, and project pointers.

**Zero dependencies** — Python 3.9+ standard library only. Pillow is optional
and only makes the gallery thumbnails nicer.

## Quickstart

1. **Point it at ComfyUI.** Start ComfyUI (default `http://127.0.0.1:8188`,
   override with `--server` or the `COMFYUI` env var).
2. **List the captions** of the default project (gravity-trajectory):
   ```sh
   python3 comfy_queue.py --list
   ```
3. **Queue the missing ones** and wait for the renders:
   ```sh
   python3 comfy_queue.py --wait
   ```
4. **Browse / pick** in the gallery:
   ```sh
   ./gallery.sh            # or gallery.bat on Windows
   ```

Pick a different project any time:

```sh
python3 comfy_queue.py --list-projects          # see what's installed
python3 comfy_queue.py --project projects/arc-steam.json --list
python3 launch.py --project projects/kerot.json --list
./gallery.sh --project projects/kerot.json
```

## Layout

```text
comfy_queue.py     queue captions into ComfyUI, collect the renders (CLI)
launch.py          gallery batch runner over the same queue engine
gallery.py/.sh/.bat   Tk thumbnail gallery: view, pick, batch, delete
test_generator.py  offline regression suite (python -m unittest test_generator)
new_project.py     create a new project skeleton
workflows/         shared ComfyUI API-format graphs (add once, use everywhere)
projects/          one <game>.json + <game>/prompts.md per game
output/            git-ignored renders, state.toml, per-game manifests
```

## Projects

A project JSON describes one game's caption pack. Projects auto-discover:
drop a JSON into `projects/` and it appears in `--list-projects` and the
gallery with zero setup. Fields:

| key | meaning |
| --- | --- |
| `name` | display name (also the ComfyUI output prefix) |
| `prompts` | prompt Markdown files, relative to the JSON |
| `workflows` | workflow directory (usually `../workflows`) |
| `output` | render output directory, relative to the JSON |
| `gallery_output` | where the gallery looks for renders (default: `output`) |
| `prefix` | ComfyUI filename prefix |
| `default_workflow` | workflow stem used without `--workflow` |
| `category_sizes` | optional `{category: [aspect_ratio, megapixels]}` map |

Create a new game with `python3 new_project.py <name>` (see
[`projects/README.md`](projects/README.md) for the caption Markdown format).

## The three installed projects

| project | prompts | renders |
| --- | --- | --- |
| `gravity-trajectory` | `projects/gravity/` (18 + 5 caption packs) | `output/gravity/` |
| `arc-steam` | `projects/arc-steam/prompts.md` (18 captions) | `output/arc-steam/` |
| `kerot` | `projects/kerot/prompts.md` (17 captions) | `output/kerot/` |

The game repos keep their own prompts: `gravity-trajectory/photos/prompts.md`,
`arc-steam`'s prompt pack, and `KeRot-minigame/img/prompts.md`. The copies in
`projects/` here are the generator's working copies; keep them in sync when a
game's captions change (or register the game's own file via `+ add project…`
in the gallery).

## Workflows

`workflows/*.json` are ComfyUI "Save (API format)" exports, used as
templates: the prompt, seed, size and output prefix are patched per caption;
everything else stays as exported. Every JSON in the directory is picked up
automatically — export a new graph from ComfyUI, drop it in, done. Nodes are
located by `class_type`, never by exported node ids, so re-exporting does not
break the scripts.

## CLI reference (comfy_queue.py)

```text
--list / --list-projects   print the caption pack or the discovered projects
--project PATH             project JSON (default: gravity, else first found)
--workflow NAME            template stem or unique part (default: project's)
--types / --situation      filter captions (default: all / both)
--variants N               pictures per caption (default 1)
--seed N                   base seed (default: the template's own seed)
--size WxH / --aspect / --megapixels   resolution overrides
--set NODE.INPUT=VALUE     raw template patch, repeatable
--out DIR                  output directory (default: the project's output)
--wait                     download renders as they finish
--collect                  only download jobs already in the manifest
--force                    redo captions that already have art
--dry-run                  print the jobs, queue nothing
```

## Gallery

`./gallery.sh` opens a Tk window with one card per caption: thumbnail, prompt
text, target path, checkbox state (remembered in `output/<game>/state.toml`),
variation count, and a batch button that queues the checked cards through the
selected workflow. Cards can be deleted (with undo, files go to
`output/<game>/.trash/`). The project selector at the top switches between
installed projects in-place; `+ add project…` registers an external project
JSON for the session (persisted in `projects.json`, git-ignored).

## Tests

```sh
python3 -m unittest test_generator
```

The suite is fully offline: a fake ComfyUI server covers prompt parsing,
template patching, video filtering, size/`--set` overrides, gallery slug and
filename mapping, state roundtrip, project discovery, and queue/collect for
image and video.
