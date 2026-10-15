"""Agent analysis is free-form; scene binding and job isolation are mandatory."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import agent_scene
import agent_scene_probe
import duel_plan


class AgentSceneTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.jobs = Path(self.temporary.name)
        self.patcher = patch.object(duel_plan, 'JOBS', self.jobs)
        self.patcher.start()
        self.folder = self.jobs / 'arbitrary_scene'
        self.folder.mkdir()
        duel_plan.atomic_json(self.folder / 'request.json', {'prompt': '七个人和非素材物体，自由动作与镜头'})

    def tearDown(self):
        self.patcher.stop()
        self.temporary.cleanup()

    def manifest(self):
        agent_scene.analyze_job('arbitrary_scene', {'actor_count': 7, 'anything': {'new_motion': ['free', 2]}})
        (self.folder / 'scene.blend').write_bytes(b'Blender scene fixture bytes')
        manifest = {'version': 1, 'kind': 'agent_scene', 'job_id': 'arbitrary_scene',
                    'scene_path': str(self.folder / 'scene.blend'),
                    'analysis_path': str(self.folder / 'agent_analysis.json'),
                    'scene_sha256': agent_scene.sha256_file(self.folder / 'scene.blend'),
                    'analysis_sha256': agent_scene.sha256_file(self.folder / 'agent_analysis.json'),
                    'request_sha256': agent_scene.sha256_file(self.folder / 'request.json')}
        duel_plan.atomic_json(self.folder / 'agent_scene.json', manifest)
        return self.folder / 'agent_scene.json'

    def test_arbitrary_analysis_is_preserved_with_original_prompt(self):
        analysis = {'actor_count': 71, 'unknown_action': {'free': ['orbit', 3.5]}, 'new_category': True}
        result = agent_scene.analyze_job('arbitrary_scene', analysis)
        self.assertTrue(result['ok'])
        saved = json.loads((self.folder / 'agent_analysis.json').read_text())
        self.assertEqual(saved['analysis'], analysis)
        self.assertEqual(saved['original_prompt'], '七个人和非素材物体，自由动作与镜头')
        self.assertEqual(saved['request_sha256'], agent_scene.sha256_file(self.folder / 'request.json'))

    def test_empty_analysis_is_allowed_but_non_json_is_rejected(self):
        self.assertTrue(agent_scene.analyze_job('arbitrary_scene', {})['ok'])
        for value in ([], {'x': float('nan')}, {'x': object()}):
            with self.subTest(value=str(value)), self.assertRaises(ValueError):
                agent_scene.analyze_job('arbitrary_scene', value)

    def test_unknown_job_and_path_escape_do_not_create_folders(self):
        for job in ('missing_job', '../arbitrary_scene', '/tmp/elsewhere'):
            with self.subTest(job=job), self.assertRaises(ValueError):
                agent_scene.analyze_job(job, {})
        self.assertFalse((self.jobs / 'missing_job').exists())
        (self.jobs / 'alias').symlink_to(self.folder, target_is_directory=True)
        with self.assertRaises(ValueError):
            agent_scene.analyze_job('alias', {})

    def test_each_binding_change_is_rejected(self):
        manifest_path = self.manifest()
        agent_scene.validate_manifest_files(manifest_path)
        for name in ('scene.blend', 'agent_analysis.json', 'request.json'):
            with self.subTest(name=name):
                path = self.folder / name
                previous = path.read_bytes()
                path.write_bytes(previous + b' ')
                with self.assertRaises(ValueError):
                    agent_scene.validate_manifest_files(manifest_path)
                path.write_bytes(previous)

    def test_request_change_requires_new_analysis(self):
        agent_scene.analyze_job('arbitrary_scene', {'custom': 'value'})
        duel_plan.atomic_json(self.folder / 'request.json', {'prompt': '新的请求'})
        request, request_hash = agent_scene._request(self.folder)
        with self.assertRaises(ValueError):
            agent_scene.load_analysis(self.folder, request, request_hash)

    def test_resubmission_archives_previous_scene_and_analysis(self):
        self.manifest()
        old_scene = (self.folder / 'scene.blend').read_bytes()
        old_analysis = (self.folder / 'agent_analysis.json').read_bytes()
        result = agent_scene.analyze_job('arbitrary_scene', {'new_motion': 'new explanation'})
        archive = self.folder / result['archive']
        self.assertEqual((archive / 'scene.blend').read_bytes(), old_scene)
        self.assertEqual((archive / 'agent_analysis.json').read_bytes(), old_analysis)
        self.assertTrue((archive / 'agent_scene.json').is_file())

    def test_reproducibility_compare_allows_rounding_but_detects_missing_animation(self):
        first = {'frame': 20, 'objects': {'cube': {'mesh_samples': [[0, .4, .5, .6]]}}}
        near = {'frame': 20, 'objects': {'cube': {'mesh_samples': [[0, .400001, .5, .6]]}}}
        static = {'frame': 20, 'objects': {'cube': {'mesh_samples': [[0, 0., .5, .6]]}}}
        self.assertFalse(agent_scene.compare_evaluation_sample(first, near))
        self.assertTrue(agent_scene.compare_evaluation_sample(first, static))

    def test_odd_positive_source_resolution_warns_instead_of_rejecting(self):
        for resolution in ([127, 71], [127, 72], [128, 71]):
            with self.subTest(resolution=resolution):
                errors, warnings = agent_scene_probe.resolution_messages(resolution)
                self.assertFalse(errors)
                self.assertEqual(len(warnings), 1)
        self.assertEqual(agent_scene_probe.resolution_messages([128, 72]), ([], []))
        for resolution in ([0, 72], [-1, 72], [128, 0], [True, 72]):
            with self.subTest(resolution=resolution):
                errors, warnings = agent_scene_probe.resolution_messages(resolution)
                self.assertEqual(errors[0]['code'], 'resolution')
                self.assertFalse(warnings)

    def test_rig_geometry_metadata_recognizes_parent_chains_and_armature_modifiers(self):
        class Object:
            def __init__(self, name, kind, parent=None, hidden=False, vertices=1):
                self.name, self.type, self.parent, self.hide_render = name, kind, parent, hidden
                self.modifiers = []
                self.data = SimpleNamespace(vertices=list(range(vertices)))

        rig = Object('ActorRig', 'ARMATURE')
        empty = Object('IntermediateParent', 'EMPTY', rig)
        parented = Object('ParentedSkin', 'MESH', empty)
        modified = Object('ModifierSkin', 'MESH')
        modified.modifiers = [SimpleNamespace(type='ARMATURE', object=rig, show_render=True)]
        hidden = Object('HiddenSkin', 'MESH', rig, hidden=True)
        no_vertices = Object('EmptySkin', 'MESH', rig, vertices=0)
        unused = Object('UnusedRig', 'ARMATURE')
        unrelated = Object('Table', 'MESH')
        scene = SimpleNamespace(objects=[rig, empty, parented, modified, hidden, no_vertices, unused, unrelated])
        actors = agent_scene.actor_geometry_facts(scene)
        self.assertEqual(actors[0]['related_visible_meshes'], ['ModifierSkin', 'ParentedSkin'])
        self.assertEqual(actors[0]['related_hidden_or_empty_meshes'], ['EmptySkin', 'HiddenSkin'])
        self.assertEqual(actors[1]['related_visible_meshes'], [])
        warnings = agent_scene.appearance_warnings({'actor_geometry': actors, 'render_engine': 'BLENDER_EEVEE',
                                                    'light_count': 0, 'render_enabled_light_count': 0})
        self.assertEqual(len(warnings), 2)
        self.assertIn('UnusedRig', warnings[0])
        self.assertIn('World', warnings[1])

    def test_lighting_diagnostic_is_advisory_and_workbench_needs_no_light(self):
        self.assertFalse(agent_scene.appearance_warnings({'render_engine': 'BLENDER_WORKBENCH', 'light_count': 0}))
        self.assertFalse(agent_scene.appearance_warnings({'render_engine': 'CYCLES', 'light_count': 1}))
        warnings = agent_scene.appearance_warnings({'render_engine': 'CYCLES', 'light_count': 0})
        self.assertEqual(len(warnings), 1)
        self.assertIn('环境/发光照明可以合法出片', warnings[0])
        # A diagnostic does not turn declared actor count into a completion gate.
        self.assertEqual(warnings, agent_scene.appearance_warnings({'render_engine': 'CYCLES', 'light_count': 0,
                                                                    'actor_count': 5000}))

    def test_robot_policy_cannot_be_removed_from_new_submission(self):
        path = self.manifest()
        request_path = self.folder / 'request.json'
        request = json.loads(request_path.read_text())
        request['character_policy'] = 'library_beta'
        duel_plan.atomic_json(request_path, request)
        agent_scene.analyze_job('arbitrary_scene', {'new_motion': 'not an enum'})
        manifest = json.loads(path.read_text())
        manifest['request_sha256'] = agent_scene.sha256_file(request_path)
        manifest['analysis_sha256'] = agent_scene.sha256_file(self.folder / 'agent_analysis.json')
        duel_plan.atomic_json(path, manifest)
        with self.assertRaisesRegex(ValueError, '动作库机器人'):
            agent_scene.validate_manifest_files(path)

    def test_event_boundaries_are_captured_beyond_global_three_samples(self):
        scene = SimpleNamespace(frame_start=11, frame_end=110)
        execution = {'motion_execution': {'segments': [
            {'start_frame': 25, 'end_frame': 47, 'contact_frame': 31},
            {'start_frame': -50, 'end_frame': 200, 'peak_frame': float('nan')} ]}}
        frames = agent_scene.execution_sample_frames(scene, execution)
        self.assertTrue({11, 110, 60, 24, 25, 26, 30, 31, 32, 46, 47, 48} <= set(frames))
        self.assertTrue(all(11 <= frame <= 110 for frame in frames))


if __name__ == '__main__':
    unittest.main()
