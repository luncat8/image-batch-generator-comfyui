#!/usr/bin/env python3
"""Project-aware gallery adapter over the shared ComfyUI queue engine."""
import argparse
import json
import re
import random
import os
import sys
import urllib.parse
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True

import comfy_queue as core

HERE = Path(__file__).resolve().parent


def _default_project():
	"""gravity if present, else the first discovered project."""
	for candidate in (HERE / 'gravity.json', HERE / 'projects' / 'gravity.json'):
		if core.is_project_file(candidate):
			return candidate
	found = core.discover_projects()
	if found:
		return found[0]
	return HERE / 'gravity.json'   # load_project() reports the real problem


cli = argparse.ArgumentParser(add_help=False)
cli.add_argument('--project', default=str(_default_project()))
selection, _ = cli.parse_known_args()
PROJECT_PATH = Path(selection.project).resolve()
CONFIG, BASE = core.load_project(PROJECT_PATH)
OUTPUT_DIR = (BASE / CONFIG.get('gallery_output', CONFIG['output'])).resolve()
STATE_PATH = OUTPUT_DIR / 'state.toml'


def use_project(path):
	"""Point the module at another project JSON and return its config.

	Every function below reads these globals when it is called, which is what
	lets an open gallery switch projects without a second process. The globals
	are assigned only after the file loads, so a bad project leaves the current
	one in place.
	"""
	global PROJECT_PATH, CONFIG, BASE, OUTPUT_DIR, STATE_PATH
	path = Path(path).resolve()
	config, base = core.load_project(path)
	for key in ('name', 'prompts', 'workflows', 'output'):
		config[key]              # a graph JSON has node ids for keys, not a project
	PROJECT_PATH = path
	CONFIG, BASE = config, base
	OUTPUT_DIR = (BASE / CONFIG.get('gallery_output', CONFIG['output'])).resolve()
	STATE_PATH = OUTPUT_DIR / 'state.toml'
	return CONFIG, BASE


DEFAULT_SERVER = core.DEFAULT_SERVER
DEFAULT_NEGATIVE = ''
http = core.request
get_history = core.history
nodes_of = core.nodes_of

def _mini_toml(text):
	line_re = re.compile(
		r'^\s*(?:\[(?P<section>[^\]]+)\]'
		r'|(?P<key>"(?:[^"\\]|\\.)*")\s*=\s*'
		r'(?P<val>true|false|-?\d+(?:\.\d+)?|"(?:[^"\\]|\\.)*")'
		r')\s*(?:#.*)?$'
	)
	state, section = {}, None
	for line in text.splitlines():
		m = line_re.match(line)
		if not m:
			continue
		if m.group("section"):
			section = state.setdefault(m.group("section"), {})
			continue
		if section is None:
			continue
		raw = m.group("val")
		if raw == "true":
			val = True
		elif raw == "false":
			val = False
		elif raw.startswith('"'):
			val = json.loads(raw)
		else:
			val = float(raw) if "." in raw else int(raw)
		section[json.loads(m.group("key"))] = val
	return state


PROJECTS_FILE = HERE / 'projects.json'   # the projects added by hand


def is_project_file(path):
	"""A project JSON names a project and its prompt, workflow and output
	paths; a ComfyUI graph has node ids for keys."""
	try:
		text = Path(path).read_text(encoding='utf-8')
	except OSError:
		return False
	if '"prompts"' not in text:	  # cheap veto before the parse
		return False
	try:
		config = json.loads(text)
	except ValueError:
		return False
	return isinstance(config, dict) and all(
		key in config for key in ('name', 'prompts', 'workflows', 'output'))


def discover_projects(added=()):
	"""Every project JSON in projects/ (and next to launch.py, for an old
	flat checkout), then the ones added by hand."""
	found = []
	for folder in (HERE / 'projects', HERE):
		if folder.is_dir():
			for path in sorted(folder.glob('*.json')):
				if path not in found and is_project_file(path):
					found.append(path)
	for path in added:
		path = Path(path)
		if path not in found and is_project_file(path):
			found.append(path)
	return found


def load_added_projects():
	try:
		text = PROJECTS_FILE.read_text(encoding='utf-8')
	except OSError:
		return []
	try:
		return [Path(entry) for entry in json.loads(text)]
	except ValueError:
		return []


def save_added_projects(paths):
	PROJECTS_FILE.write_text(
		json.dumps([str(path) for path in paths], indent='	') + '\n',
		encoding='utf-8')


def load_state(path=None):
	"""Read state.toml -> dict of sections. {} if missing or unreadable."""
	path = Path(path or STATE_PATH)   # read now: a project switch moves it
	if not path.exists():
		return {}
	text = path.read_text(encoding="utf-8")
	try:
		import tomllib
	except ImportError:
		return _mini_toml(text)
	try:
		return tomllib.loads(text)
	except Exception:
		return {}


def save_state(state, path=None):
	"""Write checkbox state as TOML (kept to the subset _mini_toml reads)."""
	path = Path(path or STATE_PATH)   # read now: a project switch moves it
	path.parent.mkdir(parents=True, exist_ok=True)
	lines = []
	for section in ("app", "prompts", "workflows", "jobs"):
		sec = state.get(section) or {}
		if not sec:
			continue
		lines.append(f"[{section}]")
		for key, val in sorted((str(k), v) for k, v in sec.items()):
			if isinstance(val, bool):
				shown = "true" if val else "false"
			elif isinstance(val, str):
				shown = json.dumps(val, ensure_ascii=False)
			elif isinstance(val, int):
				shown = str(val)
			elif isinstance(val, float):
				shown = repr(val)
				if "e" in shown or "E" in shown:
					shown = f"{val:.12f}".rstrip("0").rstrip(".")
			else:
				continue
			lines.append(f"{json.dumps(key, ensure_ascii=False)} = {shown}")
		lines.append("")
	path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def slg_slot(workflow):
	"""The ComfySwitchNode whose on_true feeds SkipLayerGuidanceDiTSimple.

	Returns (node_id, input_name) or (None, None).
	"""
	for nid, node in nodes_of(workflow, "ComfySwitchNode"):
		source = node["inputs"].get("on_true") or []
		target = workflow.get(str(source[0]) if source else "")
		if target and target.get("class_type") in (
			"SkipLayerGuidanceDiTSimple",
			"SkipLayerGuidance",
		):
			return nid, "switch"
	return None, None


def download_image(server, image_info, out_path):
	query = urllib.parse.urlencode(
		{
			"filename": image_info["filename"],
			"subfolder": image_info.get("subfolder", ""),
			"type": image_info.get("type", "output"),
		}
	)
	blob = http(f"{server}/view?{query}", timeout=120)
	out_path.parent.mkdir(parents=True, exist_ok=True)
	out_path.write_bytes(blob)



def find_workflows():
	return [Path(p) for p in core.discover_workflows(BASE / CONFIG['workflows']).values()]


def find_prompt_mds():
	return [BASE / p for p in CONFIG['prompts']]


def attach_prompt_files(paths):
	"""Validate the combined project before writing links to the chosen packs."""
	current = find_prompt_mds()
	known = set(p.resolve() for p in current)
	added = []
	for path in map(Path, paths):
		path = path.resolve()
		if path in known:
			continue
		if path.suffix.lower() not in ('.md', '.json'):
			raise SystemExit(f'not a Markdown or JSON prompt pack: {path}')
		if any(other.stem.casefold() == path.stem.casefold() for other in current + added):
			raise SystemExit(f'an attached prompt file already uses the name {path.stem!r}')
		known.add(path)
		added.append(path)
	if not added:
		return 0
	core.validate_item_ids([item for path in current + added for item in core.read_prompts(path)])
	config = dict(CONFIG, prompts=CONFIG['prompts'] + [os.path.relpath(path, BASE)
		if path.drive == BASE.drive else str(path) for path in added])
	temporary = PROJECT_PATH.with_suffix('.json.tmp')
	temporary.write_text(json.dumps(config, ensure_ascii=False, indent='\t') + '\n', encoding='utf-8')
	temporary.replace(PROJECT_PATH)
	use_project(PROJECT_PATH)
	return len(added)


def enabled_for(job, workflow, state):
	preferred = core.preferred_workflows(job)
	default = workflow.stem in preferred if preferred else workflow.stem == CONFIG.get('default_workflow')
	return state.get('workflows', {}).get(workflow.name, default)


def preferred_for(job):
	return core.preferred_workflows(job)


def caption_for(job, workflow):
	if workflow in job.get('workflow_prompts', {}):
		return job['workflow_prompts'][workflow]
	return job['caption'] if job.get('fallback_texts', job['texts']) else None


def parse_prompts(path):
	jobs = []
	for index, item in enumerate(core.project_items(core.read_prompts(path), CONFIG)):
		item.setdefault('aspect_ratio', '3:2')
		item.setdefault('megapixels', 1.5)
		for variant, text in enumerate(item['texts'][:1] if item.get('item_id') or item.get('workflow_prompts') else item['texts']):
			target = Path(item['target'])
			suffix = '' if variant == 0 else f'-{variant + 1}'
			slug = f'{Path(path).stem}__{target.parent.name}__{target.stem}{suffix}'
			if item.get('item_id'):
				slug = f"item__{item['item_id']}"
			jobs.append(dict(item, index=index + 1, category=item['situation'],
				slug=slug, filename=target.name, caption=text))
	return jobs


def job_state_key(path, job):
	if job.get('item_id'):
		return f"item::{job['item_id']}"
	return f"{Path(path).stem}::{job['slug']}"


# a gallery file: <slug>__s<seed>__<workflow>[_<n>].<ext>, plus the bare batch
# target <slug>.<ext>. A slug itself contains '__' (prompts__space__rock), so
# only the seed marker or a known workflow stem gives the cut.
SEEDED_NAME_RE = re.compile(r"^(?P<stem>.+?)__s(?P<seed>\d+)__")
SEED_RE = re.compile(r"__s(\d+)")


def split_name(name, stems):
	"""The prompt slug a rendered filename belongs to; `stems` are the workflow
	stems of the project, the only thing that separates a workflow suffix from
	the slug's own last segment."""
	base = Path(name).name.rsplit(".", 1)[0]
	seeded = SEEDED_NAME_RE.match(base)
	if seeded:
		return seeded.group("stem")
	for stem in sorted(stems, key=len, reverse=True):
		if base.endswith('__' + stem):
			return base[:-(len(stem) + 2)]
	return base


def seed_of(name):
	m = SEED_RE.search(Path(name).name)
	return int(m.group(1)) if m else None


def compatible(template, job):
	return core.compatible(template, job)


def patch_workflow(template, job, seed, args, workflow=None):
	settings = SimpleNamespace(steps=args.steps, cfg=args.cfg,
		negative=None if job.get('media') == 'audio' else args.negative or None, no_refine=True, size=None, aspect=None,
		megapixels=None, prefix=CONFIG.get('prefix', 'renders'), set=[])
	caption = caption_for(job, workflow)
	if caption is None:
		raise SystemExit(f'no caption for workflow {workflow}')
	graph = core.patch_graph(template, dict(job, text=caption,
		target=job['filename'], seed=seed, workflow=workflow, variant=0), settings)
	nid, name = slg_slot(graph)
	if nid is not None:
		graph[nid]['inputs'][name] = not args.no_slg
	return graph


def queue_job(server, graph, slug):
	settings = SimpleNamespace(no_extra_data=False, prefix=CONFIG.get('prefix', 'renders'))
	job = dict(target=slug, workflow=CONFIG['name'], situation='gallery')
	return core.queue_job(server, graph, str(uuid.uuid4()), core.job_extra_data(job, settings))


def main():
	parser = argparse.ArgumentParser(description='Batch the enabled gallery cards using the shared queue engine.')
	parser.add_argument('--project', default=str(PROJECT_PATH))
	parser.add_argument('--force', action='store_true')
	parser.add_argument('--list', action='store_true')
	parser.add_argument('--dry-run', action='store_true')
	parser.add_argument('--no-wait', action='store_true')
	options = parser.parse_args()
	state = load_state()
	app = state.get('app', {})
	server = app.get('server', DEFAULT_SERVER)
	core.manifest_path = OUTPUT_DIR / core.MANIFEST_NAME
	core.WORKFLOWS = core.discover_workflows(BASE / CONFIG['workflows'])
	parsed = {path: parse_prompts(path) for path in find_prompt_mds()}
	core.validate_item_ids(job for jobs in parsed.values() for job in jobs)
	queued = matched = 0
	for workflow in find_workflows():
		template = json.loads(workflow.read_text(encoding='utf-8'))
		nid, name = slg_slot(template)
		overrides = [] if nid is None else [f"{nid}.{name}={str(app.get('slg', True)).lower()}"]
		args = SimpleNamespace(steps=int(app['steps']) if app.get('steps') else None,
			cfg=float(app['cfg']) if app.get('cfg') else None,
			negative=app.get('negative') or None, no_refine=True,
			size=None, aspect=None, megapixels=None, prefix=CONFIG.get('prefix', 'renders'),
			set=overrides, force=options.force, dry_run=options.dry_run,
			dump=None, no_extra_data=False)
		jobs = []
		for path, cards in parsed.items():
			if not state.get('prompts', {}).get(path.name, True):
				continue
			for job in cards:
				caption = caption_for(job, workflow.stem)
				if caption is None or not enabled_for(job, workflow, state) or not compatible(template, job) or not state.get('jobs', {}).get(job_state_key(path, job), True):
					continue
				# normalize the slug like gallery.py's stem_owner does, so
				# the batch filename maps back to the job when re-scanned
				target = OUTPUT_DIR / f"{job['slug'].replace(' ', '_')}__{workflow.stem}"
				if job.get('media') == 'audio':
					args.negative = None
					target = target.with_name(target.name + Path(job['filename']).suffix)
				jobs.append(dict(job, text=caption, target=str(target),
					seed=random.randrange(2**63), variant=0, workflow=workflow.stem))
		if options.list:
			core.print_table(jobs)
			continue
		if not jobs:
			continue
		matched += len(jobs)
		_, count = core.queue(server, jobs, args, str(uuid.uuid4()))
		queued += count
	if not matched and not options.list:
		print("Nothing queued: no enabled prompts match the selected workflows. Check the gallery checkboxes.")
	if queued and not options.no_wait and not options.dry_run:
		if not core.collect(server, core.load_manifest(), 1800):
			return 1
	return 0


if __name__ == '__main__':
	sys.exit(main())
