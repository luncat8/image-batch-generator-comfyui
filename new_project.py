#!/usr/bin/env python3
"""Create a project JSON + prompt pack skeleton in projects/.

    python3 new_project.py <name>            # wizard-free, sensible defaults
    python3 new_project.py kerot --aspect 16:9 --megapixels 1.5

Writes projects/<name>.json (discovered automatically by comfy_queue.py,
launch.py and gallery.py) and projects/<name>/prompts.md with the caption
format filled in. Zero dependencies, stdlib only.
"""

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
PROJECTS_DIR = HERE / 'projects'
WORKFLOWS_DIR = HERE / 'workflows'

TEMPLATE = """\
# {name} prompts
#
# One caption per "### " heading; the line under it is the prompt text.
# Target path after "→" is where the render is saved under the project's
# output/ directory (a leading "photos/" is stripped, so
# `photos/scenes/x.jpg` lands in output/<game>/scenes/x.jpg).
#
# A *Style:* and *Aspect:* line per caption are optional; aspect ratios:
# 1:1, 2:3, 3:2, 3:4, 4:3, 9:16, 16:9, 21:9.

### 1 · {category} · a caption → `photos/{category}/example.jpg`
> The prompt text goes here, one or more quoted paragraphs.
> A second quoted paragraph becomes example-2.jpg.

*Style: clean 2D digital illustration, soft gradients, quiet palette.*
*Aspect: {aspect} · {megapixels} MP.*
"""


def main(argv=None):
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument('name', help='project name (also the slug in output/)')
	parser.add_argument('--category', default='scenes',
		help='default caption category (default scenes)')
	parser.add_argument('--aspect', default='16:9',
		help='default aspect ratio for the sample caption (default 16:9)')
	parser.add_argument('--megapixels', type=float, default=1.5,
		help='default megapixels for the sample caption (default 1.5)')
	parser.add_argument('--workflow', default=None,
		help='workflow file stem to use by default (default: moss)')
	args = parser.parse_args(argv)

	name = args.name
	if not name.replace('-', '').replace('_', '').isalnum():
		raise SystemExit(f'bad project name {name!r}: use letters, digits, - and _')

	projects_dir = PROJECTS_DIR
	projects_dir.mkdir(parents=True, exist_ok=True)
	project_dir = projects_dir / name
	project_dir.mkdir(parents=True, exist_ok=True)
	prompts_md = project_dir / 'prompts.md'
	project_json = projects_dir / f'{name}.json'

	if prompts_md.exists():
		raise SystemExit(f'prompts already exist: {prompts_md}')
	if project_json.exists():
		raise SystemExit(f'project already exists: {project_json}')

	prompts_md.write_text(TEMPLATE.format(
		name=name, category=args.category, aspect=args.aspect,
		megapixels=args.megapixels), encoding='utf-8')
	project_json.write_text(json.dumps({
		'name': name,
		'prompts': [f'{name}/prompts.md'],
		'workflows': '../workflows',
		'output': f'../output/{name}',
		'gallery_output': f'../output/{name}',
		'prefix': name,
		'default_workflow': args.workflow or 'image_moss_image_t2i',
	}, indent='\t') + '\n', encoding='utf-8')

	print(f'created {project_json}')
	print(f'created {prompts_md}')
	print(f'edit {prompts_md}, then: python3 comfy_queue.py --project projects/{name}.json --list')
	return 0


if __name__ == '__main__':
	sys.exit(main())
