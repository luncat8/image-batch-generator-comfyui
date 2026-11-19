#!/usr/bin/env python3
"""Build an offline audition page from audio files and their adjacent sidecars."""
import argparse
import html
import json
from pathlib import Path
from urllib.parse import quote


def build(root):
	entries = []
	for sidecar in sorted(root.rglob('*.json')):
		media = sidecar.with_suffix('')
		if not media.is_file() or not media.resolve().is_relative_to(root):
			continue
		try:
			meta = json.loads(sidecar.read_text(encoding='utf-8'))
		except (OSError, ValueError):
			continue
		if not isinstance(meta, dict) or meta.get('file') != media.name:
			continue
		group = meta.get('genre_id') or meta.get('cue')
		if not group or not meta.get('checkpoint'):
			continue
		entries.append((str(group), str(meta.get('sample_id')), str(meta['checkpoint']), media, meta))
	rows = []
	for group, sample, model, media, meta in sorted(entries, key=lambda row: row[:4]):
		path = media.relative_to(root).as_posix()
		title = f'{group} / {sample} · {model} · take {meta.get("take")} · seed {meta.get("seed")}'
		prompt = meta.get('style', meta.get('text', ''))
		if meta.get('lyrics'):
			prompt = f'{prompt}\n\n{meta["lyrics"]}'
		settings = ' · '.join(f'{key}: {meta[key]}' for key in
			('workflow', 'encoder', 'seconds', 'steps', 'cfg', 'sampler_name', 'scheduler') if key in meta)
		rows.append(f'<article><h2>{html.escape(title)}</h2><p>{html.escape(settings)}</p>'
			f'<audio controls preload="none" src="{quote(path)}"></audio>'
			f'<p><code>{html.escape(path)}</code></p><pre>{html.escape(str(prompt))}</pre></article>')
	page = root / 'audition.html'
	page.write_text('<!doctype html><html lang="en"><meta charset="utf-8">'
		'<meta name="viewport" content="width=device-width,initial-scale=1">'
		'<title>Audio audition</title><style>body{font:1rem system-ui;max-width:70rem;margin:2rem auto;'
		'padding:0 1rem}article{border-top:1px solid #aaa;padding-bottom:1rem}'
		'pre,code{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
		f'<h1>Audio audition</h1><p>{len(rows)} collected take(s). Playback depends on browser codec support.</p>'
		+ '\n'.join(rows), encoding='utf-8')
	return page, len(rows)


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument('output', type=Path, help='generation output directory')
	args = parser.parse_args()
	root = args.output.resolve()
	if not root.is_dir():
		parser.error(f'not an output directory: {root}')
	page, count = build(root)
	print(f'{page} ({count} takes)')


if __name__ == '__main__':
	main()
