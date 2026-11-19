# Projects

Each project is a JSON file plus its prompt pack(s):

```text
projects/
  <game>.json        # discovered automatically; see fields below
  <game>/prompts.md  # the caption pack(s)
```

## Project JSON fields

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

Required: `name`, `prompts`, `workflows`, `output`. Any other JSON in the
directory (ComfyUI graphs) is ignored for project discovery.

Create a skeleton with `python3 new_project.py <name>`.

## Caption Markdown format

One caption per `### ` heading:

```markdown
### 1 · space · rocky moon → `photos/space/rock.jpg`
> The prompt text, one or more quoted paragraphs.

*Style: clean 2D digital illustration, soft gradients.*
*Aspect: 16:9 · 1.5 MP.*
```

- The heading is `### N · <category> · <title> → \`<target>\``. The `N` is
  just an index; `<category>` groups renders under `output/<game>/<category>/`.
- The `→` target is where the render is saved. A leading `photos/` is
  stripped, so `photos/space/rock.jpg` lands in
  `output/<game>/space/rock.jpg`. Other relative paths are kept as-is.
- Prompt text is the quoted lines (`> ...`). **Multiple quoted paragraphs**
  produce `rock.jpg`, `rock-2.jpg`, … (one file per paragraph) — used by
  career/video captions.
- `*Style:*` and `*Aspect:*` lines are optional metadata. Valid aspect
  ratios: `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `9:16`, `16:9`, `21:9`.
  A caption without them gets the project's `category_sizes` for its
  category, else `3:2` at 1.5 MP.

## Shipping the renders back to a game repo

The renders land in `output/<game>/` (git-ignored here). Copy the chosen
files into the game repo's image folder — e.g.
`gravity-trajectory/photos/space/` — or point the project's `output`
directly at the game repo if you prefer a live link.

## One item, different prompts for different models

Use `*ID:*` to make an item independent of its heading, filename, and prompt
text. Use lowercase IDs with hyphens or underscores, unique across the project.
Keep the ID when moving or editing an item; give genuinely different assets
new IDs. Legacy packs without IDs work as before.

```markdown
### 1 · art · Moon outpost → `art/moon-outpost.jpg`
*ID: moon-outpost*
> A moon outpost at dawn.

*For: image_sd15_booru*
> moon, lunar outpost, dawn, wide shot, rim lighting

*For: image_moss_image_t2i*
> A low camera looks across the crater as the first light catches the outpost windows.

*For: image_custom_json*
> {
>   "subject": "lunar outpost",
>   "lighting": "dawn"
> }
```

- The workflow names are **installed API JSON filename stems**, not checkpoint
  filenames. The SD1.5 and JSON stems above are illustrative; install suitable
  workflows or replace the stems with names in your project. Image workflows
  still need the `##prompt` input marker.
- Each `*For:*` starts one prompt block and marks its workflow as preferred for
  that item. List several stems separated by commas to share one block. Line
  breaks and indentation inside the block are preserved; JSON is passed as
  prompt text, not interpreted as workflow settings. The batch CLI skips a
  graph's optional prompt refiner for these model-specific blocks.
- An optional untagged quote must come **before** the first `*For:*`. It is a
  fallback for another workflow you explicitly enable or request. With no
  fallback, other workflows skip the item. Without any `*For:*`, the project
  default applies. A gallery card with an ID or `*For:*` stays one card; use
  separate IDs for separate assets rather than using blank quotes as assets.
- The gallery and its **Run batch** button use these preferences automatically.
  Workflow checkboxes override the automatic selection; **Reset to prompt
  defaults** restores it. The standalone CLI uses preferences unless you pass
  `--workflow` explicitly. Its ID-based renders are saved under
  `items/<id>/<workflow>/take-N.<ext>`, so model outputs do not overwrite one
  another. Untagged legacy filename rules are unchanged.

## Adding files and previewing audio in the gallery

Use **Prompt files → Add prompt files…** to attach Markdown or an audio JSON
pack to the current project. The gallery saves a link in the project JSON; it
neither copies nor rewrites the prompt pack. After editing a pack in your
editor, click **Refresh** to reload it. If the project JSON cannot be written,
attach the file to a writable project instead.

Audio prompt packs for YuE2 and Stable Audio appear as cards and can be queued
like visual prompts. Gravity cue packs default to Stable Audio; to try the
same prompts in YuE2, uncheck Stable Audio and check YuE2. The cue text goes
into **Style**, **Lyrics** is left blank, and YuE2 uses its own duration and
sampler defaults instead of the cue's `seconds` and `steps`.

Click **Play** on a take to preview it in place, and click the same button
(**Stop**) to end playback. Playing a different take stops the previous one.
Install FFmpeg with `ffplay` on `PATH` to enable preview; the gallery does not
open an external player. Collected takes show their checkpoint and seed when
sidecar metadata is present. Audio collected by the CLI appears when the
gallery uses the same output folder and contains the matching prompt pack.
