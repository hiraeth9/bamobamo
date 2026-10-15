"""Candidate discovery remains open-text and keeps measured facts distinct."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))
import agent_motion_catalog as motion


class MotionCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = motion.load_catalog()

    def test_complete_local_index_and_specific_retarget_sources(self):
        entries = self.catalog['entries']
        self.assertEqual(self.catalog['fbx_count'], len(list((PROJECT / 'mixamo_fbx').rglob('*.fbx'))))
        self.assertEqual(self.catalog['fbx_count'], 63)
        self.assertEqual(len(entries), len({item['motion_id'] for item in entries}))
        glb = [item for item in entries if item['provider'] == 'quaternius_glb']
        self.assertEqual({item['clip'] for item in glb}, {'Punch_Jab', 'Punch_Cross', 'Death01'})
        self.assertTrue(all(item['license']['id'] == 'CC0-1.0' for item in glb))
        self.assertTrue(all(item['license']['id'] != 'CC0-1.0' for item in entries if item['provider'] == 'fbx'))

    def test_natural_language_keeps_ordered_multiple_candidates(self):
        result = motion.search_motions('先走路，再跑向窗户，最后翻越窗户并招手', catalog=self.catalog)
        ids = {item['motion_id'] for item in result['candidates']}
        self.assertIn('mixamo.basic.walk', ids)
        self.assertIn('mixamo.basic.run', ids)
        self.assertTrue(result['procedural_allowed'])
        self.assertFalse(result['unknown_action_rejected'])
        self.assertTrue(all(item['requires_semantic_review'] for item in result['candidates']))

    def test_unknown_actions_and_empty_queries_do_not_become_gate(self):
        for query in ('转动三把钥匙后绕身体打结', '翻越窗户', ''):
            result = motion.search_motions(query, catalog=self.catalog)
            self.assertEqual(result['candidates'], [])
            self.assertTrue(result['procedural_allowed'])
            self.assertFalse(result['unknown_action_rejected'])

    def test_right_hand_straight_punch_prefers_specific_source(self):
        result = motion.search_motions('B用右手直拳攻击A', catalog=self.catalog)
        self.assertEqual(result['candidates'][0]['motion_id'], 'quaternius.punch_cross')
        self.assertEqual(result['candidates'][0]['side'], 'right')
        self.assertFalse(result['candidates'][0]['loop_allowed'])

    def test_english_substrings_do_not_create_false_run_candidate(self):
        result = motion.search_motions('a robot is returning and holding a trunk', catalog=self.catalog)
        self.assertEqual(result['candidates'], [])

    def test_real_source_hash_change_is_not_silently_trusted(self):
        catalog = copy.deepcopy(self.catalog)
        item = next(item for item in catalog['entries'] if item['motion_id'] == 'mixamo.basic.walk')
        item['asset_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'changed'):
            motion.motion_info(item['motion_id'], catalog)

    def test_open_text_validators_are_only_tool_parameter_checks(self):
        for value in (True, 0, 101, 1.3):
            with self.assertRaises(ValueError):
                motion.search_motions('walk', limit=value, catalog=self.catalog)
        with self.assertRaises(ValueError):
            motion.search_motions({'action': 'walk'})
        with self.assertRaises(ValueError):
            motion.asset_path('../outside.fbx')

    def test_measured_scan_facts_have_real_fps_and_no_quality_pass(self):
        for item in self.catalog['entries']:
            if item['provider'] == 'fbx':
                self.assertEqual(item['scan_status'], 'scanned')
                self.assertEqual(item['source_fps'], 30.)
                self.assertTrue(item['compatibility']['direct_action'])
                self.assertGreater(item['duration_s'], 0)
            self.assertNotIn('quality_passed', item)
            self.assertNotIn('passed', item)

    def test_stale_scan_is_not_rebound_to_changed_source_hash(self):
        scan = json.loads((PROJECT / 'reports/robot_asset_scan_20261014.json').read_text())
        scan['assets'][0]['path_sha256'] = '0' * 64
        result = motion.build_catalog(scan)
        item = next(item for item in result['entries'] if item['asset'] == scan['assets'][0]['asset'])
        self.assertEqual(item['scan_status'], 'scan_stale_source_changed')
        self.assertIsNone(item['compatibility'])
        self.assertIsNone(item['source_fps'])

    def test_sampling_report_cannot_claim_a_different_source(self):
        report = json.loads((PROJECT / 'reports/agent_motion_validation_20261014.json').read_text())
        for check in report['checks']:
            if 'source_info' in check:
                check['source_info']['source_sha256'] = '0' * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'changed_source_report.json'
            path.write_text(json.dumps(report))
            result = motion.build_catalog('reports/robot_asset_scan_20261014.json', path)
        self.assertFalse(any(item.get('source_sampling_status') for item in result['entries']))


if __name__ == '__main__':
    unittest.main()
