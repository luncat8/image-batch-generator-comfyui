#!/usr/bin/env python3
"""Offline generator regressions: python -m unittest discover -s . -p 'test_*.py'."""
import contextlib
import io
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True

import comfy_queue as core
import launch
from tools.audio_audition import build as build_audition

HERE = Path(__file__).resolve().parent
GRAVITY_MD = HERE / 'projects' / 'gravity' / 'prompts.md'
JOKE_MD = HERE / 'projects' / 'gravity' / 'helper-joke-prompts.md'
ARC_MD = HERE / 'projects' / 'arc-steam' / 'prompts.md'
ARC_PROJECT = HERE / 'projects' / 'arc-steam.json'
GRAVITY_PROJECT = HERE / 'projects' / 'gravity.json'
STABLE_WF = HERE / 'workflows' / 'audio_stable_audio_3_medium_api.json'
AUDIO_WF = HERE / 'workflows' / 'audio_yue2_text2music_api.json'
VIDEO_WF = HERE / 'workflows' / 'video_minimax_pika_h3_flex_t2v_api.json'


def settings(**overrides):
	values = dict(steps=None, cfg=None, no_refine=True, negative=None,
		aspect=None, megapixels=None, size=None, prefix='test', set=[],
		variants=1, seed=42, out='/tmp/test-generator')
	values.update(overrides)
	return SimpleNamespace(**values)


class GeneratorTests(unittest.TestCase):
	def test_existing_prompts_and_variants(self):
		items = core.read_prompts(GRAVITY_MD)
		self.assertEqual(len(items), 18)
		self.assertEqual(len(items[-1]['texts']), 2)
		jobs = core.make_jobs(items, settings(), ['image_moss_image_t2i'])
		self.assertEqual(len(jobs), 19)
		self.assertTrue(jobs[-1]['target'].endswith('career/circus-2.jpg'))

	def test_arc_pack_preserved(self):
		config, _ = core.load_project(ARC_PROJECT)
		self.assertEqual(len(config['prompts']), 1)
		pack = ARC_PROJECT.parent / config['prompts'][0]
		self.assertTrue(pack.is_file())
		self.assertEqual(len(core.read_prompts(pack)), 18)

	def test_kerot_pack_preserved(self):
		config, _ = core.load_project(HERE / 'projects' / 'kerot.json')
		pack = config and (HERE / 'projects' / config['prompts'][0])
		self.assertTrue(pack.is_file())
		self.assertEqual(len(core.read_prompts(pack)), 17)

	def test_all_templates_patch_without_mutating(self):
		for name, path in core.WORKFLOWS.items():
			with self.subTest(workflow=name):
				template = json.loads(Path(path).read_text())
				if core.workflow_media(template) == 'audio':
					continue  # exercised by the music binding test
				before = json.dumps(template)
				graph = core.patch_graph(template, dict(text='A silver launch rail.',
					seed=123, target='photos/career/test.jpg', situation='career'), settings())
				self.assertEqual(json.dumps(template), before)
				self.assertNotIn('##prompt', json.dumps(graph))
				video = bool(core.nodes_of(graph, 'SaveVideo'))
				seed_node = core.one_node(graph, 'RandomNoise' if video else 'KSampler')
				self.assertEqual(seed_node['inputs']['noise_seed' if video else 'seed'], 123)

	def test_video_filtering_is_graph_based(self):
		items = core.read_prompts(JOKE_MD)
		jobs = core.make_jobs(items, settings(), list(core.WORKFLOWS))
		self.assertEqual(len(jobs), 8)  # 3 captions x 2 image graphs + 2 video captions
		for job in jobs:
			self.assertEqual(job['situation'] == 'video', 'video' in job['workflow'])

	def test_size_and_raw_overrides(self):
		graph = core.build(VIDEO_WF,
			dict(text='A flag flutters.', seed=4, target='video/test.mp4', situation='video'),
			settings(size=(960, 640), set=['140:133.value=6']))
		self.assertEqual(graph['140:131']['inputs']['width'], 960)
		self.assertEqual(graph['140:131']['inputs']['height'], 640)
		self.assertEqual(graph['140:133']['inputs']['value'], 6)

	def test_unsupported_video_sampler_override(self):
		with self.assertRaisesRegex(SystemExit, 'ManualSigmas'):
			core.build(VIDEO_WF,
				dict(text='A flag.', seed=1), settings(steps=30))

	def test_unknown_aspect_ratio_names_the_valid_ones(self):
		template = json.loads(VIDEO_WF.read_text())
		with self.assertRaisesRegex(SystemExit, 'valid ratios'):
			core.patch_graph(template, dict(text='A flag.', seed=1,
				target='video/test.mp4', situation='video',
				aspect_ratio='7:3'), settings())

	def test_gallery_parser_and_compatibility(self):
		jobs = launch.parse_prompts(GRAVITY_MD)
		self.assertEqual(len(jobs), 19)
		for job in jobs:
			self.assertTrue(all(key in job for key in ('aspect_ratio', 'megapixels', 'caption', 'filename', 'slug', 'category')))
		self.assertEqual(len({j['slug'] for j in jobs}), 19)
		video = json.loads(VIDEO_WF.read_text())
		self.assertFalse(launch.compatible(video, jobs[0]))


	def test_gallery_filenames_map_to_their_prompt(self):
		stems = {path.stem for path in launch.find_workflows()}
		self.assertEqual(launch.split_name('prompts__space__rock.jpg', stems),
			'prompts__space__rock')
		for stem in stems:
			name = f'prompts__space__rock__s123__{stem}_2.png'
			self.assertEqual(launch.split_name(name, stems), 'prompts__space__rock')
		# a unknown suffix is not a workflow: keep the whole name as the stem
		self.assertEqual(launch.split_name('space__trench.jpg', stems), 'space__trench')
		self.assertEqual(launch.seed_of('prompts__space__rock__s123__image_moss_image_t2i.png'), 123)
		self.assertIsNone(launch.seed_of('prompts__space__rock.jpg'))

	def test_nested_target_paths_and_pack_sizes(self):
		job = dict(target='assets/scenes/moon.jpg', variant=0)
		self.assertEqual(core.target_path(job, '/renders'), Path('/renders/assets/scenes/moon.jpg'))
		with self.assertRaises(SystemExit):
			core.target_path(dict(target='../escape.jpg', variant=0), '/renders')
		config, _ = core.load_project(ARC_PROJECT)
		items = core.project_items(core.read_prompts(ARC_MD), config)
		self.assertEqual(items[6]['aspect_ratio'], '16:9')
		self.assertEqual(items[6]['megapixels'], 1)

	def test_state_roundtrip(self):
		with tempfile.TemporaryDirectory() as directory:
			path = Path(directory) / 'nested/state.toml'
			state = {'app': {'server': 'http://host:8188', 'slg': False},
				'jobs': {'file::caption': False}}
			launch.save_state(state, path)
			self.assertEqual(launch.load_state(path), state)
			self.assertEqual(launch._mini_toml(path.read_text()), state)

	def test_project_discovery_skips_workflow_graphs(self):
		self.assertTrue(launch.is_project_file(GRAVITY_PROJECT))
		self.assertTrue(launch.is_project_file(ARC_PROJECT))
		found = launch.discover_projects()
		self.assertIn(GRAVITY_PROJECT, found)
		self.assertIn(ARC_PROJECT, found)
		self.assertNotIn(HERE / 'workflows' / 'image_moss_image_t2i.json', found)
		self.assertTrue(any(p.name == 'kerot.json' for p in found))

	def test_use_project_moves_output_state_and_workflows(self):
		before = (launch.PROJECT_PATH, launch.CONFIG, launch.BASE,
			launch.OUTPUT_DIR, launch.STATE_PATH)
		try:
			config, base = launch.use_project(ARC_PROJECT)
			self.assertEqual(config['name'], 'arc-steam')
			self.assertEqual(launch.PROJECT_PATH, ARC_PROJECT.resolve())
			self.assertEqual(launch.OUTPUT_DIR,
				(base / config['gallery_output']).resolve())
			self.assertEqual(launch.STATE_PATH, launch.OUTPUT_DIR / 'state.toml')
			self.assertEqual([p.name for p in launch.find_prompt_mds()],
				['prompts.md'])
			self.assertEqual(len(launch.parse_prompts(launch.find_prompt_mds()[0])), 18)
			# a broken project must leave the current one in place
			with self.assertRaises(SystemExit):
				launch.use_project(HERE / 'workflows' / 'image_moss_image_t2i.json')
			self.assertEqual(launch.CONFIG['name'], 'arc-steam')
		finally:
			launch.use_project(before[0])
		self.assertEqual(launch.CONFIG, before[1])
		self.assertEqual(launch.BASE, before[2])
		self.assertEqual(launch.OUTPUT_DIR, before[3])
		self.assertEqual(launch.STATE_PATH, before[4])

	def test_default_project_prefers_gravity_else_first_discovered(self):
		found = core.discover_projects()
		self.assertTrue(found, 'projects/ must contain at least one project')
		# gravity wins when present, otherwise the first discovered project
		self.assertEqual(core.default_project(), GRAVITY_PROJECT)
		with tempfile.TemporaryDirectory() as directory:
			sub = Path(directory) / 'projects'
			sub.mkdir()
			project = sub / 'only.json'
			project.write_text(json.dumps(dict(name='only', prompts=['x.md'],
				workflows=str(HERE / 'workflows'), output='out')))
			core.PROJECTS_DIR = sub
			try:
				self.assertEqual(core.default_project(), project)
			finally:
				core.PROJECTS_DIR = HERE / 'projects'

	def test_external_project_and_workflow_directory(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			(root / 'pack.md').write_text('### 1 · art · hello → `photos/art/hello.jpg`\n> A moon.\n')
			(root / 'project.json').write_text(json.dumps(dict(name='external',
				prompts=['pack.md'], output='renders', workflows=str(HERE / 'workflows'))))
			with contextlib.redirect_stdout(io.StringIO()) as output:
				core.main(['--project', str(root / 'project.json'), '--list'])
			self.assertIn('hello.jpg', output.getvalue())

	def test_workflow_blocks_choose_text_and_preserve_item_identity(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			pack = root / 'original.md'
			moss, krea = 'image_moss_image_t2i', 'image_krea2_turbo_t2i_int8'
			pack.write_text('### 1 · art · Moon → `art/moon.jpg`\n*ID: moon-card*\n'
				f'> Fallback caption\n*For: {moss}*\n> {{\n>   "scene": "A moon"\n> }}\n'
				f'*For: {krea}*\n> moon, craters\n', encoding='utf-8')
			items = core.read_prompts(pack)
			self.assertEqual(core.texts_for(items[0], 'not-listed'), ['Fallback caption'])
			jobs = core.make_jobs(items, settings(out=directory), [moss, krea], auto_default='not-listed')
			self.assertEqual([j['text'] for j in jobs], ['{\n  "scene": "A moon"\n}', 'moon, craters'])
			self.assertNotEqual(jobs[0]['target'], jobs[1]['target'])
			card, = launch.parse_prompts(pack)
			self.assertTrue(launch.enabled_for(card, Path(moss + '.json'), {}))
			self.assertFalse(launch.enabled_for(card, Path(moss + '.json'), {'workflows': {moss + '.json': False}}))
			self.assertFalse(launch.enabled_for(card, Path('not-listed.json'), {}))
			self.assertEqual(launch.caption_for(card, krea), 'moon, craters')
			moved = pack.rename(root / 'moved.md')
			new, = launch.parse_prompts(moved)
			self.assertEqual(launch.job_state_key(pack, card), launch.job_state_key(moved, new))
			self.assertEqual(card['slug'], new['slug'])
			moved.write_text(f'### 1 · art · Moon → `art/moon.jpg`\n*For: {moss}*\n> Only Moss\n', encoding='utf-8')
			self.assertEqual(core.texts_for(core.read_prompts(moved)[0], krea), [])

	def test_attach_files_updates_project_and_rejects_duplicate_item_id(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			project = root / 'project.json'
			project.write_text(json.dumps(dict(name='test', prompts=[], workflows='workflows', output='out')))
			pack, duplicate = root / 'one.md', root / 'two.md'
			for path in (pack, duplicate):
				path.write_text('### 1 · art · Moon → `art/moon.jpg`\n*ID: moon-card*\n> A moon\n', encoding='utf-8')
			previous = launch.PROJECT_PATH
			try:
				launch.use_project(project)
				self.assertEqual(launch.attach_prompt_files([pack]), 1)
				self.assertEqual(launch.attach_prompt_files([pack]), 0)
				with self.assertRaisesRegex(SystemExit, 'duplicate prompt item ID'):
					launch.attach_prompt_files([duplicate])
				self.assertEqual(json.loads(project.read_text())['prompts'], ['one.md'])
			finally:
				launch.use_project(previous)

	def test_music_bindings_and_model_isolation(self):
		with tempfile.TemporaryDirectory() as directory:
			pack = Path(directory) / 'samples.json'
			lyrics = '[Verse]\nTest one\n\n[Chorus]\nTest two'
			pack.write_text(json.dumps({'samples': [dict(genre_id='blues', sample_id='fixture',
				style='Test style', lyrics=lyrics)]}))
			items = core.read_prompts(pack)
			base = settings(out=directory, variants=2)
			changed = settings(out=directory, variants=2, set=['33:15.ckpt_name="other/model.safetensors"'])
			jobs = core.make_jobs(items, base, list(core.WORKFLOWS))
			other = core.make_jobs(items, changed, [AUDIO_WF.stem])
			self.assertEqual([job['seed'] for job in jobs], [42, 43])
			self.assertEqual([job['seed'] for job in other], [42, 43])
			self.assertTrue(all(a['target'] != b['target'] for a, b in zip(jobs, other)))
			template = json.loads(AUDIO_WF.read_text())
			before = json.dumps(template)
			graph = core.patch_graph(template, jobs[0], base)
			self.assertEqual(json.dumps(template), before)
			self.assertEqual(graph['33:36']['inputs']['value'], 'Test style')
			self.assertEqual(graph['33:37']['inputs']['value'], lyrics)
			self.assertEqual(graph['33:34']['inputs']['seed'], 42)
			for nid in ('33:8', '33:24', '33:25'):
				self.assertEqual(graph[nid]['inputs']['seed'], ['33:34', 0])
			self.assertEqual(core.music.provenance(graph, jobs[0])['checkpoint'],
				'yue2_3b_int8_convrot.safetensors')
			pack.write_text(json.dumps({'samples': [dict(genre_id='../bad', sample_id='x', style='x')]}))
			with self.assertRaisesRegex(SystemExit, 'lowercase slugs'):
				core.read_prompts(pack)

	def test_gravity_cue_settings_and_seed_match_filtered_runs(self):
		with tempfile.TemporaryDirectory() as directory:
			pack = Path(directory) / 'cues.json'
			pack.write_text(json.dumps(dict(aim=[dict(id='loop', text='Loop', seconds=7, steps=8)],
				armed=[], launch=[dict(id='impact', text='Impact', seconds=4.5, steps=10)], fly=[])))
			items = core.read_prompts(pack)
			args = settings(out=directory, variants=2, seed=None)
			jobs = core.make_jobs(items, args, list(core.WORKFLOWS))
			self.assertEqual([job['workflow'] for job in jobs], [STABLE_WF.stem] * 4)
			self.assertEqual([job['seed'] for job in jobs], [4100, 4101, 4200, 4201])
			selected = core.select(items, 'launch', 'both')
			self.assertEqual(core.make_jobs(selected, args, [STABLE_WF.stem]), jobs[2:])
			job = jobs[2]
			graph = core.build(STABLE_WF, job, args)
			self.assertEqual(graph['6']['inputs']['text'], 'Impact')
			self.assertEqual(graph['7']['inputs']['text'], '')
			self.assertEqual(graph['11']['inputs']['seconds'], 4.5)
			self.assertEqual(graph['3']['inputs']['steps'], 10)
			self.assertEqual(graph['3']['inputs']['seed'], 4200)
			changed = settings(out=directory, steps=9, seed=None)
			new, = core.make_jobs(selected, changed, [STABLE_WF.stem])
			self.assertNotEqual(job['target'], new['target'])
			self.assertEqual(core.music.provenance(graph, job)['cue'], 'launch')

	def test_queue_collect_image_video_and_audio(self):
		requests = []
		class Handler(BaseHTTPRequestHandler):
			def log_message(self, *args):
				pass

			def do_POST(self):
				data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
				requests.append(data)
				self.send_response(200)
				self.end_headers()
				self.wfile.write(json.dumps({'prompt_id': str(len(requests))}).encode())

			def do_GET(self):
				self.send_response(200)
				self.end_headers()
				if self.path.startswith('/view?'):
					self.wfile.write(b'fixture-media')
					return
				pid = self.path.rsplit('/', 1)[-1]
				graph = requests[int(pid) - 1]['prompt']
				key, filename = {'audio': ('audio', 'render.flac'), 'video': ('videos', 'render.mp4'),
					'image': ('images', 'render.png')}[core.workflow_media(graph)]
				entry = {'status': {'status_str': 'success'}, 'outputs': {'save': {
					key: [{'filename': filename, 'type': 'output'}]}}}
				self.wfile.write(json.dumps({pid: entry}).encode())

		server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
		thread = threading.Thread(target=server.serve_forever, daemon=True)
		thread.start()
		try:
			with tempfile.TemporaryDirectory() as directory:
				root = Path(directory)
				for workflow, situation, count in [('moss', 'career', 8), ('video', 'video', 2)]:
					with contextlib.redirect_stdout(io.StringIO()):
						result = core.main(['--server', f'http://127.0.0.1:{server.server_port}',
							'--workflow', workflow, '--situation', situation, '--wait',
							'--out', str(root), '--manifest', str(root / 'manifest.json')])
					self.assertEqual(result, 0)
					self.assertEqual(len(list((root / situation).iterdir())), count)
				self.assertTrue(all('extra_pnginfo' in r['extra_data'] for r in requests))
				pack = root / 'samples.json'
				pack.write_text(json.dumps({'samples': [dict(genre_id='blues',
					sample_id='fixture', style='Test style', lyrics='[Verse]\nTest line')]}))
				options = ['--project', str(HERE / 'projects/music-tree.json'), '--out', str(root / 'music'),
					'--server', f'http://127.0.0.1:{server.server_port}']
				with contextlib.redirect_stdout(io.StringIO()):
					self.assertEqual(core.main(options + ['--prompts', str(pack), '--seed', '42']), 0)
					self.assertEqual(core.main(options + ['--collect']), 0)
					count = len(requests)
					self.assertEqual(core.main(options + ['--prompts', str(pack)]), 0)
					self.assertEqual(len(requests), count)
				audio, = (root / 'music').rglob('*.flac')
				self.assertEqual(audio.read_bytes(), b'fixture-media')
				metadata = json.loads(audio.with_suffix('.flac.json').read_text())
				self.assertEqual(metadata['genre_id'], 'blues')
				self.assertEqual(metadata['workflow'], AUDIO_WF.stem)
				self.assertEqual(metadata['checkpoint'], 'yue2_3b_int8_convrot.safetensors')
				self.assertEqual(metadata['seed'], 42)

				cues = root / 'cues.json'
				cues.write_text(json.dumps(dict(aim=[], armed=[], fly=[], launch=[dict(
					id='fixture', text='Impact <test>', seconds=4.5, steps=10)])))
				out = root / 'gravity sounds'
				with contextlib.redirect_stdout(io.StringIO()):
					self.assertEqual(core.main(['--project', str(HERE / 'projects/gravity-sounds.json'),
						'--out', str(out), '--server', f'http://127.0.0.1:{server.server_port}',
						'--prompts', str(cues), '--wait']), 0)
				page, count = build_audition(out)
				self.assertEqual(count, 1)
				audition = page.read_text()
				self.assertIn('Impact &lt;test&gt;', audition)
				self.assertIn('steps: 10', audition)
				take, = out.rglob('take-1.flac')
				self.assertIn(take.relative_to(out).as_posix(), audition)

		finally:
			server.shutdown()
			server.server_close()
			thread.join()


if __name__ == '__main__':
	unittest.main()
