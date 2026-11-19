"""Audio prompt packs, model graph bindings, and output identity."""
import hashlib
import json
import re
from pathlib import Path

AUDIO_EXTS = ('.opus', '.flac', '.mp3', '.wav', '.ogg', '.m4a', '.aac')
SAVE_NODES = {'SaveAudio', 'SaveAudioMP3', 'SaveAudioOpus', 'SaveAudioAdvanced'}
GRAVITY_CUES = ('aim', 'armed', 'launch', 'fly')
SLUG = re.compile(r'[a-z0-9]+(?:[-_][a-z0-9]+)*')


def read_pack(path):
	def invalid(message):
		raise SystemExit(f'{path}: {message}')

	try:
		pack = json.loads(Path(path).read_text(encoding='utf-8'))
	except (OSError, ValueError) as err:
		invalid(str(err))
	if isinstance(pack, dict) and set(pack) == set(GRAVITY_CUES):
		return read_cues(pack, invalid)
	rows = pack.get('samples') if isinstance(pack, dict) else None
	if not isinstance(rows, list) or not rows:
		invalid('expected a non-empty samples array')
	items = []
	seen = set()
	for i, row in enumerate(rows, 1):
		if not isinstance(row, dict):
			invalid(f'sample {i}: expected an object')
		genre, sample = row.get('genre_id'), row.get('sample_id')
		if not all(isinstance(v, str) and SLUG.fullmatch(v) for v in (genre, sample)):
			invalid(f'sample {i}: genre_id and sample_id must be lowercase slugs')
		if (genre, sample) in seen:
			invalid(f'duplicate sample {genre}/{sample}')
		seen.add((genre, sample))
		style, lyrics = row.get('style'), row.get('lyrics', '')
		if not isinstance(style, str) or not style.strip() or not isinstance(lyrics, str):
			invalid(f'sample {i}: style must be non-blank and lyrics must be text')
		items.append(dict(media='audio', adapter='yue2', type=genre, situation='music', genre_id=genre,
			sample_id=sample, target=f'{genre}/{sample}.opus', style=style,
			text=style, texts=[style], lyrics=lyrics))
	return items


def read_cues(pack, invalid):
	items = []
	for cue in GRAVITY_CUES:
		rows = pack[cue]
		if not isinstance(rows, list):
			invalid(f'{cue}: expected a list of prompts')
		seen = set()
		for row in rows:
			if not isinstance(row, dict):
				invalid(f'{cue}: expected a prompt object')
			sample, text = row.get('id'), row.get('text')
			seconds, steps = row.get('seconds'), row.get('steps')
			if not isinstance(sample, str) or not SLUG.fullmatch(sample) or sample in seen:
				invalid(f'{cue}: expected unique lowercase prompt IDs')
			if not isinstance(text, str) or not text.strip():
				invalid(f'{cue}/{sample}: expected non-blank text')
			if type(seconds) not in (float, int) or not 0 < seconds < float('inf'):
				invalid(f'{cue}/{sample}: expected positive finite seconds')
			if type(steps) is not int or steps < 1:
				invalid(f'{cue}/{sample}: expected positive integer steps')
			seen.add(sample)
			items.append(dict(media='audio', adapter='stable-audio', type=cue, situation='sound',
				cue=cue, sample_id=sample, target=f'{cue}/{sample}.flac', style='', text=text,
				texts=[text], seconds=seconds, steps=steps, pack_index=len(items)))
	if not items:
		invalid('expected at least one cue prompt')
	return items


def adapter(graph):
	if any(node.get('class_type') == 'YuE2GenerateMusic' for node in graph.values()):
		return 'yue2'
	if any(node.get('class_type') == 'CLIPLoader' and node['inputs'].get('type') == 'stable_audio'
			for node in graph.values()):
		return 'stable-audio'
	return None


def supports(graph, item):
	model = adapter(graph)
	# Gravity's text-only cues can double as YuE2 style prompts. The reverse
	# direction would discard lyrics and has no Stable Audio duration/steps.
	return model is not None and (model == item['adapter'] or
		(model == 'yue2' and item['adapter'] == 'stable-audio'))


def stable_bindings(graph):
	samplers = [n['inputs'] for n in graph.values() if n.get('class_type') == 'KSampler']
	if len(samplers) != 1:
		raise SystemExit('Stable Audio workflow needs exactly one KSampler')
	sampler = samplers[0]

	def linked(inputs, name, class_type, key):
		link = inputs.get(name)
		node = graph.get(str(link[0]), {}) if isinstance(link, list) and len(link) == 2 else {}
		if node.get('class_type') != class_type or key not in node.get('inputs', {}):
			raise SystemExit(f'Stable Audio {name} must link to {class_type}.{key}')
		return node['inputs']

	positive = linked(sampler, 'positive', 'CLIPTextEncode', 'text')
	encoder = linked(positive, 'clip', 'CLIPLoader', 'clip_name')
	latent = linked(sampler, 'latent_image', 'EmptyLatentAudio', 'seconds')
	loader = linked(sampler, 'model', 'CheckpointLoaderSimple', 'ckpt_name')
	return positive, latent, sampler, loader, encoder


def patch_stable(graph, job, args):
	positive, latent, sampler, _, _ = stable_bindings(graph)
	positive['text'] = job['text']
	latent['seconds'] = job['seconds']
	sampler['steps'] = job['steps'] if args.steps is None else args.steps
	sampler['seed'] = job['seed']
	if args.cfg is not None:
		sampler['cfg'] = args.cfg


def save_nodes(graph):
	return [node for node in graph.values() if node.get('class_type') in SAVE_NODES]


def bindings(graph):
	"""Follow the generator's inputs, keeping shared ABC/sampler links intact."""
	generators = [n for n in graph.values() if n.get('class_type') == 'YuE2GenerateMusic']
	if len(generators) != 1:
		raise SystemExit('audio workflow needs exactly one YuE2GenerateMusic node')
	inputs = generators[0]['inputs']

	def source(name, key):
		link = inputs.get(name)
		if not isinstance(link, list) or len(link) != 2:
			raise SystemExit(f'YuE2 {name} must link to a node with a {key} input')
		node = graph.get(str(link[0]), {}).get('inputs', {})
		if key not in node:
			raise SystemExit(f'YuE2 {name} source has no {key} input')
		return node

	return source('style', 'value'), source('lyrics', 'value'), source('seed', 'seed'), source('clip', 'ckpt_name')


def provenance(graph, job):
	if adapter(graph) == 'stable-audio':
		positive, latent, sampler, loader, encoder = stable_bindings(graph)
		return dict(cue=job['cue'], sample_id=job['sample_id'], take=job['variant'] + 1,
			checkpoint=loader['ckpt_name'], encoder=encoder['clip_name'], text=positive['text'],
			seconds=latent['seconds'], **{key: sampler[key] for key in
				('seed', 'steps', 'cfg', 'sampler_name', 'scheduler')})
	style, lyrics, seed, loader = bindings(graph)
	# Keep the source pack identity so either model's take maps to the same card.
	group = {'cue': job['cue']} if 'cue' in job else {'genre_id': job['genre_id']}
	return dict(group, sample_id=job['sample_id'], take=job['variant'] + 1,
		checkpoint=loader['ckpt_name'], seed=seed['seed'],
		style=style['value'], lyrics=lyrics['value'])


def cue_run_dir(graph, metadata):
	# Output naming is not a generation parameter.
	effective = {nid: (node['class_type'], {key: value for key, value in node['inputs'].items()
		if key != 'filename_prefix'}) for nid, node in graph.items()}
	digest = hashlib.sha256(json.dumps(effective, sort_keys=True).encode()).hexdigest()[:12]
	return f's{metadata["seed"]}-t{metadata["steps"]}--{digest}'


def model_dir(checkpoint):
	stem = Path(checkpoint.replace('\\', '/')).stem
	slug = re.sub(r'[^a-zA-Z0-9_-]+', '-', stem).strip('-') or 'checkpoint'
	digest = hashlib.sha256(checkpoint.encode()).hexdigest()[:10]
	return f'{slug[:80]}--{digest}'
