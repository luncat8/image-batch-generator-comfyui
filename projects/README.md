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
