#!/usr/bin/env python3
"""Queue a project's prompts.md captions into ComfyUI, then collect the renders.

The workflow JSONs in workflows/ are saved from ComfyUI's "Save (API
format)" dialog; every such json is picked up automatically, so a new graph
needs no edit here. They are used as templates: the prompt, the seed, the size
and the Save Image prefix are patched per caption, everything else (models,
sampler, prompt-refiner chain) stays exactly as exported, so a better graph can
be dropped in without touching this script.

    python3 comfy_queue.py --list             # caption pack (default project)
    python3 comfy_queue.py                    # queue missing ones
    python3 comfy_queue.py --collect          # download them
    python3 comfy_queue.py --workflow moss --wait
    python3 comfy_queue.py --situation career --wait
    python3 comfy_queue.py --project projects/arc-steam.json --list

Zero dependencies, stdlib only. Nodes are found by class_type, never by the
numeric id from the export, so re-exporting a graph does not break the script.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

sys.dont_write_bytecode = True

import music

HERE = Path(__file__).resolve().parent
# Manifest targets are stored relative to the generator root (HERE) so a
# moved checkout still resolves; targets outside the root stay absolute.
# (This used to be HERE.parents[1], which assumed a photos/generator/ depth
# and raised IndexError on a shallow standalone checkout.)
ROOT = HERE
REPO = ROOT  # backwards-compatible alias
MANIFEST = HERE / 'queue-manifest.json'
manifest_path = MANIFEST
MANIFEST_NAME = 'queue-manifest.json'
WORKFLOWS_DIR = HERE / 'workflows'
PROJECTS_DIR = HERE / 'projects'


def default_workflows_dir():
	"""The bundled workflows/ folder, or HERE for an old flat checkout."""
	return WORKFLOWS_DIR if WORKFLOWS_DIR.is_dir() else HERE


def discover_workflows(root=None):
	"""Every API-format workflow json in workflows/ is a template.

	The queue manifest and any json that is not a ComfyUI "Save (API format)"
	export (a dict of nodes keyed by id, each with a class_type) are skipped.
	"""
	found = {}
	for path in sorted((Path(root) if root else default_workflows_dir()).glob('*.json')):
		if path.name == MANIFEST_NAME:
			continue
		try:
			graph = json.loads(path.read_text(encoding='utf-8'))
		except (OSError, ValueError):
			continue
		if not (isinstance(graph, dict) and graph and
				all(isinstance(node, dict) and 'class_type' in node
					for node in graph.values())):
			continue
		found[path.stem] = str(path.resolve())
	return found


WORKFLOWS = discover_workflows()


def resolve_workflow(name):
	"""A template by its file stem, by a unique part of one, or all of them."""
	if name in ('all', 'both'):
		return list(WORKFLOWS)
	if name in WORKFLOWS:
		return [name]
	matches = [stem for stem in WORKFLOWS if name in stem]
	if len(matches) == 1:
		return matches
	known = ', '.join(WORKFLOWS) or 'none found'
	if not matches:
		raise SystemExit(f'no workflow template {name!r}; available: {known}')
	raise SystemExit(f'--workflow {name!r} matches several templates: '
		f'{", ".join(matches)}')


def default_workflow():
	"""Without --workflow: the only template, else the historical krea pick."""
	if not WORKFLOWS:
		raise SystemExit(f'no workflow templates in {default_workflows_dir()}; '
			'export a graph from ComfyUI with "Save (API format)" and drop '
			'the json into workflows/')
	if len(WORKFLOWS) == 1:
		return next(iter(WORKFLOWS))
	krea = [stem for stem in WORKFLOWS if 'krea' in stem]
	if len(krea) == 1:
		return krea[0]
	print('several templates (' + ', '.join(WORKFLOWS) + '); picking the first, '
		'pass --workflow to choose')
	return next(iter(WORKFLOWS))

# ComfyUI ResolutionSelector labels (comfy_extras/nodes_resolution.py)
ASPECT_3_2 = '3:2 (Photo)'
# 1.5 MP at 3:2 is exactly 1536 x 1024, the size photos/README.md asks for.
MEGAPIXELS = 1.5
PROMPT_SLOT = '##prompt'
EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.mp4', '.webm') + music.AUDIO_EXTS
DEFAULT_SERVER = os.environ.get('COMFYUI', 'http://127.0.0.1:8188')

class ComfyError(RuntimeError):
	"""A ComfyUI call failed; the message is the whole diagnosis."""


ASPECT_LABELS = {ratio: f'{ratio} ({label})' for ratio, label in (
	('1:1', 'Square'), ('2:3', 'Portrait Photo'), ('3:2', 'Photo'),
	('3:4', 'Portrait Standard'), ('4:3', 'Standard'),
	('9:16', 'Portrait Widescreen'), ('16:9', 'Widescreen'), ('21:9', 'Ultrawide'))}

# --------------------------------------------------------------- projects

def is_project_file(path):
	"""A project JSON names a project and its prompt, workflow and output
	paths; a ComfyUI graph has node ids for keys."""
	try:
		text = Path(path).read_text(encoding='utf-8')
	except OSError:
		return False
	if '"prompts"' not in text:  # cheap veto before the parse
		return False
	try:
		config = json.loads(text)
	except ValueError:
		return False
	return isinstance(config, dict) and all(
		key in config for key in ('name', 'prompts', 'workflows', 'output'))


def discover_projects(added=()):
	"""Every project JSON in projects/ (and next to this script, for an old
	flat checkout), then the ones added by hand via the gallery."""
	found = []
	for folder in (PROJECTS_DIR, HERE):
		if folder.is_dir():
			for path in sorted(folder.glob('*.json')):
				if path not in found and is_project_file(path):
					found.append(path)
	for path in added:
		path = Path(path)
		if path not in found and is_project_file(path):
			found.append(path)
	return found


def default_project():
	"""Without --project: gravity if present, else the first discovered."""
	for candidate in (HERE / 'gravity.json', PROJECTS_DIR / 'gravity.json'):
		if is_project_file(candidate):
			return candidate
	found = discover_projects()
	if found:
		return found[0]
	raise SystemExit('no project found: add one with '
		'`python3 new_project.py <name>`, drop a project JSON into projects/, '
		'or pass --project path/to/project.json')

# ---------------------------------------------------------------- prompts.md

def paragraphs(parts):
	"""Quoted lines -> paragraphs; a blank quoted line starts a new variant."""
	texts = []
	for part in parts:
		if not part:
			if texts and texts[-1]:
				texts.append('')
			continue
		if texts and texts[-1]:
			texts[-1] += ' ' + part
		else:
			texts.append(part)
	return [text for text in texts if text]


def read_prompts(path):
	"""Parse the caption pack: type, situation, target, style, text variants.

	A caption is one or more `>` paragraphs. A blank quoted line (`>`) starts
	the next variant, so one row can carry several finished-image prompts; the
	generator writes each as its own file (`<stem>.jpg`, `<stem>-2.jpg`, ...).
	"""
	if Path(path).suffix.lower() == '.json':
		return music.read_pack(path)
	items = []
	pattern = re.compile(r'^###\s+\d+\s*·\s*([^·]+)\s*·\s*(.+?)\s*→\s*`([^`]+)`\s*$')
	current = None
	for raw in Path(path).read_text(encoding='utf-8').splitlines():
		line = raw.strip()
		match = pattern.match(line)
		if match:
			category, label, target = (v.strip() for v in match.groups())
			where = 'space' if label == 'from space' else label
			game = where in ('space', 'landed', 'career', 'video')
			current = {'type': category if game else label,
				'situation': where if game else category,
				'target': target, 'style': '', 'parts': [], 'blocks': []}
			items.append(current)
			continue
		if current is None:
			continue
		if line.startswith('#'):
			current = None
		elif line.startswith('*ID:'):
			value = line[4:].rstrip('*').strip()
			if 'item_id' in current or not music.SLUG.fullmatch(value):
				raise SystemExit(f'{path}: expected one lowercase ID for {current["target"]}')
			current['item_id'] = value
		elif line.startswith('*For:'):
			names = [name.strip() for name in line[5:].rstrip('*').split(',')]
			if not all(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', name) for name in names):
				raise SystemExit(f'{path}: invalid workflow stems for {current["target"]}')
			current['blocks'].append((names, []))
		elif line.startswith('*Style:'):
			current['style'] = line[7:].strip().rstrip('*').strip().rstrip('.')
		elif line.startswith('*Aspect:'):
			aspect = re.search(r'(\d+:\d+)\s*·\s*([\d.]+)\s*MP', line)
			if aspect:
				current['aspect_ratio'] = aspect.group(1)
				current['megapixels'] = float(aspect.group(2))
		elif line.startswith('>'):
			if current['blocks']:
				current['blocks'][-1][1].append(raw.lstrip()[1:].removeprefix(' '))
			else:
				current['parts'].append(line[1:].strip())
	for item in items:
		item['texts'] = paragraphs(item.pop('parts'))
		blocks = item.pop('blocks')
		if blocks:
			preferred = {}
			for names, lines in blocks:
				text = '\n'.join(lines).strip()
				for name in names:
					if not text or name in preferred:
						raise SystemExit(f'{path}: empty or repeated For block for {name} on {item["target"]}')
					preferred[name] = text
			item['workflow_prompts'] = preferred
			item['fallback_texts'] = item['texts']
			if not item['texts']:
				item['texts'] = [next(iter(preferred.values()))]
		if not item['texts']:
			raise SystemExit(f"empty caption for {item['target']}")
		item['text'] = item['texts'][0]
	if not items:
		raise SystemExit(f'no captions found in {path}')
	return items


def validate_item_ids(items):
	seen = set()
	audio = set()
	for item in items:
		if item.get('media') == 'audio':
			key = (item['adapter'], item['type'], item['sample_id'])
			if key in audio:
				raise SystemExit(f'duplicate audio sample: {key[1]}/{key[2]}')
			audio.add(key)
		identity = item.get('item_id')
		if identity:
			if identity in seen:
				raise SystemExit(f'duplicate prompt item ID: {identity}')
			seen.add(identity)


def texts_for(item, workflow):
	preferred = item.get('workflow_prompts', {})
	if workflow in preferred:
		return [preferred[workflow]]
	return item.get('fallback_texts', item['texts'])


def preferred_workflows(item):
	if item.get('workflow_prompts'):
		return tuple(item['workflow_prompts'])
	return {'yue2': ('audio_yue2_text2music_api',),
		'stable-audio': ('audio_stable_audio_3_medium_api',)}.get(item.get('adapter'), ())


def select(items, types, situation):
	want = None if types in (None, '', 'all') else {t.strip() for t in types.split(',')}
	picked = [i for i in items
		if (want is None or i['type'] in want)
		and (situation == 'both' or i['situation'] == situation)]
	if not picked:
		raise SystemExit(f'no caption matches types={types!r} situation={situation!r}')
	return picked


# ------------------------------------------------------------- workflow patch

def nodes_of(workflow, class_type):
	return [(nid, node) for nid, node in workflow.items()
		if node.get('class_type') == class_type]


def one_node(workflow, class_type):
	found = nodes_of(workflow, class_type)
	if not found:
		raise SystemExit(f'workflow has no {class_type} node')
	return found[0][1]


def prompt_slot(workflow):
	"""The multiline text node holding the '##prompt' placeholder."""
	for nid, node in workflow.items():
		if any(value == PROMPT_SLOT for value in node.get('inputs', {}).values()):
			return nid
	raise SystemExit(f'workflow has no text node containing {PROMPT_SLOT!r}')


def refine_slot(workflow):
	"""The bool that decides whether the graph rewrites the caption before use.

	MOSS exports the decision as a literal `switch` on an If/Else node whose
	true branch is the TextGenerate refiner; Krea exports it as a PrimitiveBoolean
	titled "Refine Prompt?". Return (node_id, input_name) for whichever exists.
	"""
	for nid, node in nodes_of(workflow, 'ComfySwitchNode'):
		flag = node['inputs'].get('switch')
		if not isinstance(flag, bool):
			continue
		source = node['inputs'].get('on_true') or []
		target = workflow.get(str(source[0]) if source else '')
		if target and target.get('class_type') == 'TextGenerate':
			return nid, 'switch'
	for nid, node in nodes_of(workflow, 'PrimitiveBoolean'):
		title = node.get('_meta', {}).get('title', '').lower()
		if 'refine' in title:
			return nid, 'value'
	return None, None


def sampler_node(workflow):
	return one_node(workflow, 'KSampler')


def negative_slot(workflow):
	"""The CLIPTextEncode wired into KSampler.negative, if it takes raw text."""
	link = sampler_node(workflow)['inputs'].get('negative') or []
	node = workflow.get(str(link[0]) if link else '')
	if node and node.get('class_type') == 'CLIPTextEncode':
		if isinstance(node['inputs'].get('text'), str):
			return node
	return None


def apply_override(workflow, spec):
	"""--set NODE.INPUT=VALUE, e.g. --set 30:3.steps=8 or 30:24.value=false."""
	target, sep, raw = spec.partition('=')
	node_id, _, name = target.partition('.')
	if not sep or not name:
		raise SystemExit(f'bad --set {spec!r}, expected NODE.INPUT=VALUE')
	if node_id not in workflow:
		raise SystemExit(f'--set {spec!r}: workflow has no node {node_id!r}')
	if name not in workflow[node_id]['inputs']:
		raise SystemExit(f'--set {spec!r}: node {node_id} has no input {name!r}')
	try:
		value = json.loads(raw)
	except json.JSONDecodeError:
		value = raw
	workflow[node_id]['inputs'][name] = value


def build(workflow_path, job, args):
	"""Load the template and patch one job into it."""
	return patch_graph(json.loads(Path(workflow_path).read_text(encoding='utf-8')), job, args)


def workflow_media(graph):
	if music.save_nodes(graph):
		return 'audio'
	return 'video' if nodes_of(graph, 'SaveVideo') else 'image'


def compatible(graph, item):
	media = item.get('media', 'video' if item['situation'] == 'video' else 'image')
	return workflow_media(graph) == media and (media != 'audio' or music.adapter(graph) == item['adapter'])


def patch_audio(workflow, job, args):
	if any(getattr(args, key) is not None for key in ('size', 'negative')):
		raise SystemExit('audio does not support --size or --negative')
	if job['adapter'] == 'stable-audio':
		music.patch_stable(workflow, job, args)
	else:
		style, lyrics, seed, _ = music.bindings(workflow)
		style['value'], lyrics['value'] = job['text'], job['lyrics']
		if job['seed'] is not None:
			seed['seed'] = job['seed']
		for key in ('steps', 'cfg'):
			if getattr(args, key) is not None:
				one_node(workflow, 'KSampler')['inputs'][key] = getattr(args, key)
	for saver in music.save_nodes(workflow):
		saver['inputs']['filename_prefix'] = (
			f'{args.prefix}/{job["type"]}/{job["workflow"]}/'
			f'{job["sample_id"]}/take-{job["variant"] + 1}')
	for spec in args.set or ():
		apply_override(workflow, spec)
	return workflow


def patch_graph(template, job, args):
	workflow = json.loads(json.dumps(template))
	if workflow_media(workflow) == 'audio':
		return patch_audio(workflow, job, args)
	slot = workflow[prompt_slot(workflow)]['inputs']
	key = next(key for key, value in slot.items() if value == PROMPT_SLOT)
	slot[key] = job['text']

	video = bool(nodes_of(workflow, 'SaveVideo'))
	sampler = one_node(workflow, 'RandomNoise' if video else 'KSampler')['inputs']
	seed_key = 'noise_seed' if video else 'seed'
	if job['seed'] is not None:
		sampler[seed_key] = job['seed']
	if video and (args.steps is not None or args.cfg is not None):
		raise SystemExit('video uses ManualSigmas/BasicGuider; use --set for sampler overrides')
	if args.steps is not None:
		sampler['steps'] = args.steps
	if args.cfg is not None:
		sampler['cfg'] = args.cfg

	if args.no_refine or job.get('workflow') in job.get('workflow_prompts', {}):
		node_id, name = refine_slot(workflow)
		if node_id is not None:
			workflow[node_id]['inputs'][name] = False

	negative = None if video else negative_slot(workflow)
	if negative is not None and args.negative is not None:
		negative['inputs']['text'] = args.negative

	resolution = nodes_of(workflow, 'ResolutionSelector')
	if resolution and args.size is None:
		inputs = resolution[0][1]['inputs']
		ratio = job.get('aspect_ratio')
		if ratio and ratio not in ASPECT_LABELS:
			raise SystemExit(f'unknown aspect ratio {ratio!r} for '
				f'{job.get("target", "caption")!r}; valid ratios: '
				f'{", ".join(sorted(ASPECT_LABELS))}')
		inputs['aspect_ratio'] = args.aspect or (ASPECT_LABELS[ratio] if ratio else ASPECT_3_2)
		inputs['megapixels'] = args.megapixels or job.get('megapixels', MEGAPIXELS)

	if args.size is not None:
		width, height = args.size
		for node in workflow.values():
			inputs = node.get('inputs', {})
			if 'width' in inputs and 'height' in inputs:
				inputs['width'], inputs['height'] = width, height

	stem = job.get('item_id') or Path(job['target']).stem
	one_node(workflow, 'SaveVideo' if video else 'SaveImage')['inputs']['filename_prefix'] = \
		f'{args.prefix}/{job["situation"]}/{stem}'

	for spec in args.set or ():
		apply_override(workflow, spec)
	return workflow


# ------------------------------------------------------------------ comfy api

def request(url, payload=None, timeout=60):
	data = None if payload is None else json.dumps(payload).encode('utf-8')
	req = urllib.request.Request(url, data=data,
		headers={'Content-Type': 'application/json'} if data else {})
	try:
		with urllib.request.urlopen(req, timeout=timeout) as reply:
			return reply.read()
	except urllib.error.HTTPError as err:
		body = err.read().decode('utf-8', 'replace')
		try:
			detail = json.loads(body)
		except json.JSONDecodeError:
			detail = body
		shown = json.dumps(detail, indent=2) if not isinstance(detail, str) else detail
		raise ComfyError(f'{url} failed: HTTP {err.code}\n{shown}')
	except urllib.error.URLError as err:
		raise ComfyError(f'cannot reach ComfyUI at {url}: {err.reason}\n'
			'start ComfyUI or pass --server http://host:port')


def job_extra_data(job, args):
	"""The metadata the web UI attaches to a prompt, which queue nodes read.

	Queue Manager (before 2026-10) dereferences
	extra_data.extra_pnginfo.workflow.workflow_name for every dequeued item, so
	an API job without it raises KeyError inside the prompt worker and aborts
	ComfyUI. `workflow_name` is what its queue list shows per job, `id` groups
	the runs of one template (recent ComfyUI also reports it as workflow_id).
	"""
	if args.no_extra_data:
		return None
	stem = Path(job['target']).stem
	return {'extra_pnginfo': {'workflow': {
		'workflow_name': f'{getattr(args, "prefix", "renders")} · {job["workflow"]} · {job["situation"]}/{stem}',
		'id': f'{getattr(args, "prefix", "renders")}-{job["workflow"]}',
	}}}


def queue_job(server, workflow, client_id, extra_data):
	payload = {'prompt': workflow, 'client_id': client_id}
	if extra_data is not None:
		payload['extra_data'] = extra_data
	reply = json.loads(request(f'{server}/prompt', payload))
	return reply['prompt_id']


def history(server, prompt_id):
	"""Finished entries only; empty while queued or running."""
	raw = request(f'{server}/history/{urllib.parse.quote(prompt_id)}', timeout=30)
	return json.loads(raw).get(prompt_id) or {}


def output_files(node_output):
	return [item for key in ('images', 'videos', 'gifs', 'audio')
		for item in node_output.get(key, ()) if 'filename' in item]


def save_outputs(server, entry, target, audio_only=False):
	written = []
	for node_output in (entry.get('outputs') or {}).values():
		files = output_files({'audio': node_output.get('audio', ())} if audio_only else node_output)
		for image in files:
			if audio_only and image.get('type', 'output') != 'output':
				continue
			query = urllib.parse.urlencode({
				'filename': image['filename'],
				'subfolder': image.get('subfolder', ''),
				'type': image.get('type', 'output'),
			})
			blob = request(f'{server}/view?{query}', timeout=120)
			path = resolve(target).with_suffix(Path(image['filename']).suffix or '.png')
			if written:
				path = path.with_name(f'{path.stem}-{len(written) + 1}{path.suffix}')
			path.parent.mkdir(parents=True, exist_ok=True)
			path.write_bytes(blob)
			written.append(path)
	return written


# --------------------------------------------------------------------- jobs

def target_path(job, out_dir):
	"""photos/space/rock.jpg -> <out>/space/rock[-N].jpg (a leading photos/
	is stripped; other relative subdirectories are kept)."""
	named = Path(job['target'])
	if named.is_absolute() or '..' in named.parts:
		raise SystemExit('prompt targets must be relative paths without ..')
	if named.parts and named.parts[0] == 'photos':
		named = Path(*named.parts[1:])
	stem = named.stem
	if job['variant']:
		stem = f'{stem}-{job["variant"] + 1}'
	return Path(out_dir) / named.parent / f'{stem}{named.suffix}'


def resolve(target):
	"""Targets are stored relative to the generator root so a manifest
	survives a moved checkout; absolute targets pass through."""
	path = Path(target)
	return path if path.is_absolute() else ROOT / path


def display(path):
	path = Path(path)
	return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def existing(target):
	stem = resolve(target).with_suffix('')
	return [p for p in (stem.parent / (stem.name + ext) for ext in EXTS) if p.exists()]


def make_jobs(items, args, workflows, auto_default=None):
	jobs = []
	audio_targets = set()
	for workflow in workflows:
		graph = json.loads(Path(WORKFLOWS[workflow]).read_text(encoding='utf-8'))
		audio = workflow_media(graph) == 'audio'
		first_job = len(jobs)
		for item in items:
			if not compatible(graph, item):
				continue
			if auto_default is not None and workflow not in (preferred_workflows(item) or (auto_default,)):
				continue
			texts = texts_for(item, workflow)
			if not texts:
				continue
			# A career caption writes one card per quoted paragraph; the deck
			# reads no career variants, so extra texts are separate files, not
			# seed repeats. A planet caption is one text repeated by --variants.
			pictures = (len(texts) if item['situation'] in ('career', 'video')
				else args.variants)
			for variant in range(pictures):
				job = dict(item)
				job['text'] = texts[variant % len(texts)]
				job['workflow'] = workflow
				job['variant'] = variant
				# one deterministic seed per job so a rerun reproduces the same
				# picture; without --seed the template's own seed is reused
				offset = len(jobs) - first_job if audio else len(jobs)
				job['seed'] = None if args.seed is None else args.seed + offset
				if audio:
					if job['adapter'] == 'stable-audio':
						if args.variants > 100:
							raise SystemExit('Stable Audio cue packs support at most 100 takes per prompt')
						base = music.stable_bindings(graph)[2]['seed'] if args.seed is None else args.seed
						job['seed'] = base + job['pack_index'] * 100 + variant
					patched = patch_graph(graph, job, args)
					metadata = music.provenance(patched, job)
					model = music.model_dir(metadata['checkpoint'])
					folder = Path(args.out) / job['type'] / workflow / model / job['sample_id']
					if job['adapter'] == 'stable-audio':
						folder /= music.cue_run_dir(patched, metadata)
					job['target'] = display(folder / f'take-{variant + 1}{Path(job["target"]).suffix}')
					if job['target'] in audio_targets:
						raise SystemExit(f'duplicate audio target: {job["target"]}')
					audio_targets.add(job['target'])
				else:
					target = target_path(job, args.out)
					if job.get('item_id'):
						target = Path(args.out) / 'items' / job['item_id'] / workflow / f'take-{variant + 1}{target.suffix}'
					elif job.get('workflow_prompts'):
						target = target.with_stem(f'{target.stem}__{workflow}')
					job['target'] = display(target)
				jobs.append(job)
	return jobs


def load_manifest():
	if manifest_path.exists():
		try:
			return json.loads(manifest_path.read_text(encoding='utf-8'))
		except json.JSONDecodeError:
			pass
	return {'jobs': []}


def write_manifest(manifest):
	manifest_path.parent.mkdir(parents=True, exist_ok=True)
	manifest_path.write_text(json.dumps(manifest, indent='\t') + '\n', encoding='utf-8')


def print_table(items):
	print(f'{"type":8} {"situation":10} {"texts":5} {"style":34} target')
	for item in items:
		print(f'{item["type"]:8} {item["situation"]:10} {len(item["texts"]):<5} '
			f'{item["style"][:34]:34} {item["target"]}')
	print(f'{len(items)} captions')


# --------------------------------------------------------------------- main

def collect(server, manifest, timeout, only_targets=None):
	"""Poll finished jobs in the manifest and download their outputs."""
	pending = [j for j in manifest['jobs']
		if not j.get('done') and (only_targets is None or j['target'] in only_targets)]
	if not pending:
		print('nothing to collect')
		return True
	print(f'collecting {len(pending)} job(s) ...')
	deadline = time.time() + timeout
	missing = set(j['prompt_id'] for j in pending)
	failed = False
	while missing and time.time() < deadline:
		for prompt_id in list(missing):
			try:
				entry = history(server, prompt_id)
			except ComfyError as err:
				raise ComfyError(f'{err}\n\nComfyUI stopped answering while jobs were '
					'pending. If its log ends with KeyError: \'extra_pnginfo\' inside '
					'a prompt_worker thread, an old Queue Manager build crashed on a job '
					'without extra_data. This script sends it by default, so check that '
					'--no-extra-data is not set, restart ComfyUI and run --collect again.')
			if not entry:
				continue
			job = next(j for j in pending if j['prompt_id'] == prompt_id)
			status = (entry.get('status') or {}).get('status_str', '')
			if status == 'error':
				print(f'  ! {job["target"]}: {job["workflow"]} run failed server-side')
				failed = True
			else:
				provenance = job.get('audio', job.get('music'))
				is_audio = provenance is not None
				paths = save_outputs(server, entry, job['target'], audio_only=is_audio)
				if is_audio:
					if not paths:
						print(f'  ! {job["target"]}: history contains no saved audio')
						failed = True
					job['files'] = [display(path) for path in paths]
					for path in paths:
						metadata = dict(provenance, workflow=job['workflow'], prompt_id=prompt_id, file=path.name)
						path.with_suffix(path.suffix + '.json').write_text(
							json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
				for path in paths:
					print(f'  + {display(path)}')
				job['done'] = bool(paths) or not is_audio
			missing.discard(prompt_id)
			write_manifest(manifest)
		if missing:
			time.sleep(1)
	if missing:
		print(f'{len(missing)} job(s) still queued after {timeout:.0f}s; '
			f'run --collect again once ComfyUI finishes them')
		return False
	return not failed


def queue(server, jobs, args, client_id):
	manifest = load_manifest()
	queued = 0
	claimed = {}
	for job in jobs:
		claimed.setdefault(job['target'], set()).add(job['workflow'])
	clashes = {t: w for t, w in claimed.items() if len(w) > 1}
	for target, owners in sorted(clashes.items()):
		print(f'  ! {target} is rendered by {" + ".join(sorted(owners))}; '
			f'the last finished render wins (use --out to keep both sets)')
	for job in jobs:
		have = existing(job['target'])
		if have and not args.force:
			print(f'  = {display(have[0])} exists (use --force to redo)')
			continue
		template = WORKFLOWS.get(job['workflow'])
		if template is None:
			raise SystemExit(f'workflow template {job["workflow"]!r} is gone; '
				f'available: {", ".join(WORKFLOWS) or "(none)"}')
		workflow = build(template, job, args)
		if args.dump:
			dump = Path(args.dump) / f'{job["workflow"]}_{Path(job["target"]).stem}.json'
			if job.get('media') == 'audio' or job.get('item_id') or job.get('workflow_prompts'):
				relative = resolve(job['target']).relative_to(Path(args.out).resolve())
				dump = Path(args.dump) / relative.with_suffix('.json')
			dump.parent.mkdir(parents=True, exist_ok=True)
			dump.write_text(json.dumps(workflow, indent='\t') + '\n', encoding='utf-8')
		if args.dry_run:
			print(f'  · {job["workflow"]} {display(resolve(job["target"]))} '
				f'seed={job["seed"] if job["seed"] is not None else "template"}')
			continue
		prompt_id = queue_job(server, workflow, client_id, job_extra_data(job, args))
		key = f'{job["workflow"]}:{job["target"]}'
		manifest['jobs'] = [j for j in manifest['jobs'] if j.get('key') != key]
		metadata = {'audio': music.provenance(workflow, job)} if job.get('media') == 'audio' else {}
		manifest['jobs'].append({
			**metadata,
			'key': key,
			'prompt_id': prompt_id, 'target': job['target'], 'type': job['type'],
			'situation': job['situation'], 'variant': job['variant'],
			'workflow': job['workflow'], 'seed': job['seed'],
			'queued': time.strftime('%Y-%m-%d %H:%M:%S'), 'done': False,
		})
		write_manifest(manifest)
		queued += 1
		print(f'  + queue {display(resolve(job["target"]))} ({job["workflow"]}, {prompt_id[:8]})')
	return manifest, queued


def parse_size(text):
	match = re.fullmatch(r'\s*(\d+)\s*[x×]\s*(\d+)\s*', text)
	if not match:
		raise SystemExit(f'bad --size {text!r}, expected WIDTHxHEIGHT')
	return int(match.group(1)), int(match.group(2))


def project_items(items, config):
	for item in items:
		size = config.get('category_sizes', {}).get(item['situation'])
		if size:
			item.setdefault('aspect_ratio', size[0])
			item.setdefault('megapixels', size[1])
	return items


def load_project(path):
	path = Path(path).resolve()
	if not path.is_file():
		raise SystemExit(f'project file not found: {path}\n'
			'create one with `python3 new_project.py <name>`, drop a project '
			'JSON into projects/, or pass --project path/to/project.json')
	try:
		config = json.loads(path.read_text(encoding='utf-8'))
	except (OSError, ValueError) as err:
		raise SystemExit(f'cannot read project {path}: {err}')
	if not isinstance(config, dict):
		raise SystemExit(f'project {path} must be a JSON object')
	missing = [key for key in ('name', 'prompts', 'workflows', 'output')
		if key not in config]
	if missing:
		raise SystemExit(f'project {path} is missing required key(s): '
			f'{", ".join(missing)} (see projects/README.md)')
	return config, path.parent


def main(argv=None):
	parser = argparse.ArgumentParser(
		description='Queue a project\'s prompts into ComfyUI and collect the renders.',
		formatter_class=argparse.RawDescriptionHelpFormatter,
		epilog='examples:\n'
			'  comfy_queue.py --list\n'
			'  comfy_queue.py --list-projects\n'
			'  comfy_queue.py --workflow all --variants 2 --wait\n'
			'  comfy_queue.py --types gas,ice --situation landed --force\n'
			'  comfy_queue.py --project projects/arc-steam.json --list\n'
			'  comfy_queue.py --set 30:3.steps=6 --set 30:24.value=false\n')
	parser.add_argument('--project', default=None,
		help='project configuration JSON (default: gravity, else the first '
			'project found in projects/)')
	parser.add_argument('--prompts', action='append', help='Markdown caption or JSON audio pack (repeatable)')
	parser.add_argument('--workflow-dir', help='directory of API workflow JSON templates')
	parser.add_argument('--prefix', default=None, help='ComfyUI output prefix')
	parser.add_argument('--server', default=DEFAULT_SERVER,
		help=f'ComfyUI base url (default {DEFAULT_SERVER}, env COMFYUI)')
	parser.add_argument('--workflow', default=None, metavar='NAME',
		help='template to use: its json file stem, a unique part of one, or all '
			'(default: the only template, else the krea one)')
	parser.add_argument('--types', default='all',
		help='comma list of caption types (default all)')
	parser.add_argument('--situation', default='both',
		help='caption situation/category, or both (default both)')
	parser.add_argument('--variants', type=int, default=1,
		help='pictures per caption; a caption\'s text repeats with a new '
			'seed, a caption with several quoted paragraphs cycles them; '
			'career/video captions write one file per paragraph')
	parser.add_argument('--seed', type=int, default=None,
		help='base seed; without it the seed stored in the template is reused')
	parser.add_argument('--steps', type=int, default=None, help='override KSampler steps')
	parser.add_argument('--cfg', type=float, default=None, help='override KSampler cfg')
	parser.add_argument('--aspect', default=None,
		help=f'ResolutionSelector aspect_ratio (default {ASPECT_3_2!r})')
	parser.add_argument('--megapixels', type=float, default=None)
	parser.add_argument('--size', type=parse_size, default=None,
		help='exact WxH, bypassing the ResolutionSelector (e.g. 1536x1024)')
	parser.add_argument('--negative', default=None, help='replace the negative prompt text')
	parser.add_argument('--no-refine', action='store_true',
		help='send the caption verbatim, skipping the graph\'s prompt refiner')
	parser.add_argument('--out', default=None,
		help='output directory (default: the project\'s output)')
	parser.add_argument('--force', action='store_true', help='redo captions that already have art')
	parser.add_argument('--no-extra-data', action='store_true',
		help='omit extra_data.extra_pnginfo; only for servers without a queue custom node')
	parser.add_argument('--dry-run', action='store_true', help='print the jobs, queue nothing')
	parser.add_argument('--dump', default=None, metavar='DIR',
		help='write each patched workflow to DIR for inspection')
	parser.add_argument('--wait', action='store_true', help='download outputs as they finish')
	parser.add_argument('--timeout', type=float, default=1800,
		help='seconds to wait for renders with --wait/--collect (default 1800)')
	parser.add_argument('--collect', action='store_true',
		help='only download jobs recorded in the manifest')
	parser.add_argument('--manifest', default=None)
	parser.add_argument('--list', action='store_true', help='print the caption pack and exit')
	parser.add_argument('--list-projects', action='store_true',
		help='print the discovered projects and exit')
	parser.add_argument('--set', action='append', default=[], metavar='NODE.INPUT=VALUE',
		help='raw patch of the template, repeatable')
	args = parser.parse_args(argv)
	if args.variants < 1:
		parser.error('--variants must be at least 1')

	if args.list_projects:
		found = discover_projects()
		for path in found:
			try:
				print(f'{path}\t{json.loads(path.read_text(encoding="utf-8"))["name"]}')
			except (OSError, ValueError):
				print(f'{path}\t(unreadable)')
		print(f'{len(found)} project(s)')
		return 0

	# Fall back to the first discovered project instead of a hardcoded
	# gravity.json path: a standalone checkout has no assumption about
	# which project is the default.
	args.project = args.project or str(default_project())

	config, base = load_project(args.project)
	args.out = str(Path(args.out or (base / config['output'])).resolve())
	args.prefix = args.prefix or config.get('prefix', 'renders')
	global WORKFLOWS
	WORKFLOWS = discover_workflows(args.workflow_dir or base / config['workflows'])
	global manifest_path
	manifest_path = Path(args.manifest) if args.manifest else Path(args.out) / MANIFEST_NAME

	if args.collect:
		try:
			return 0 if collect(args.server, load_manifest(), args.timeout) else 1
		except ComfyError as err:
			raise SystemExit(str(err))

	paths = [Path(p).resolve() for p in args.prompts] if args.prompts else [base / p for p in config['prompts']]
	if not paths:
		raise SystemExit('no prompt packs configured; pass --prompts PATH')
	items = project_items([item for path in paths for item in read_prompts(path)], config)
	validate_item_ids(items)
	if args.list:
		print_table(items)
		return 0

	picked = select(items, args.types, args.situation)
	if args.workflow is not None:
		jobs = make_jobs(picked, args, resolve_workflow(args.workflow))
	else:
		default = config.get('default_workflow') or default_workflow()
		wanted = dict.fromkeys(name for item in picked for name in (preferred_workflows(item) or (default,)))
		missing = wanted.keys() - WORKFLOWS.keys()
		if missing:
			raise SystemExit(f'preferred workflows not found: {", ".join(sorted(missing))}')
		jobs = make_jobs(picked, args, wanted, auto_default=default)

	for workflow in dict.fromkeys(job['workflow'] for job in jobs):
		if args.set:
			graph = json.loads(Path(WORKFLOWS[workflow]).read_text(encoding='utf-8'))
			for spec in args.set:
				apply_override(graph, spec)

	if not jobs:
		raise SystemExit('no compatible jobs: match the workflow media to the selected image, video, or music prompts')
	used = ', '.join(dict.fromkeys(job['workflow'] for job in jobs))
	dimensions = ''
	if any(job.get('media') != 'audio' for job in jobs):
		dimensions = f' · {args.aspect} · {args.megapixels} MP'
		if args.size is not None:
			dimensions += f' · {args.size[0]}x{args.size[1]}'
	print(f'{len(jobs)} job(s): {used}{dimensions}{" · dry run" if args.dry_run else ""}')
	try:
		manifest, queued = queue(args.server, jobs, args, uuid.uuid4().hex)
	except ComfyError as err:
		raise SystemExit(str(err))
	if args.dry_run:
		return 0
	print(f'queued {queued}')
	if args.wait and queued:
		try:
			return 0 if collect(args.server, manifest, args.timeout) else 1
		except ComfyError as err:
			raise SystemExit(str(err))
	if queued:
		print('watch them in the ComfyUI queue; run --collect when done to download')
	return 0


if __name__ == '__main__':
	sys.exit(main())
