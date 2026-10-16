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
        self.assertEqual({item['clip'] for item in glb}, {'Punch_Jab', 'Punch_Cross', 'Death01',
                                                       'Idle_Loop', 'Walk_Loop', 'Sprint_Loop'})
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

    def test_everyday_words_find_existing_measured_motion_sources(self):
        expected = {'连拳': {'mixamo.combat_melee.jab_cross'},
                    '连续出拳': {'mixamo.combat_melee.jab_cross'},
                    '踢': {'mixamo.combat_melee.mma_kick', 'cmu.144_05'},
                    '走停': {'cmu.16_33'}, '退步': {'cmu.76_11'},
                    '倒地': {'quaternius.death01'},
                    '防守': {'cmu.144_07', 'cmu.144_26'},
                    '护架': {'mixamo.combat_melee.mma_idle'}}
        for query, identifiers in expected.items():
            result = motion.search_motions(query, limit=100, usable_only=True, catalog=self.catalog)
            self.assertTrue(identifiers <= {entry['motion_id'] for entry in result['candidates']}, query)
            self.assertTrue(result['procedural_allowed'])
            self.assertFalse(result['unknown_action_rejected'])
        # These terms improve retrieval without certifying named combinations
        # inside the two CMU boxing clips or inventing a forward fighting step.
        self.assertFalse({'cmu.79_08', 'cmu.80_10'} & {entry['motion_id'] for entry in
            motion.search_motions('连拳', limit=100, catalog=self.catalog)['candidates']})

    def test_public_core_accepts_everyday_terms_without_promoting_quality(self):
        identifiers = {'cmu.16_33', 'cmu.76_11', 'cmu.144_05', 'cmu.144_07',
                       'cmu.144_26', 'quaternius.death01'}
        core = {'entries': [entry for entry in self.catalog['entries'] if entry['motion_id'] in identifiers]}
        before = copy.deepcopy(core)
        for query in ('走停', '退步', '踢', '防守', '倒地'):
            result = motion.search_motions(query, usable_only=True, catalog=core)
            self.assertTrue(result['candidates'], query)
            self.assertTrue(all(entry['sampling_available'] and entry['requires_semantic_review']
                                for entry in result['candidates']))
        self.assertEqual(core, before)
        unknown = motion.search_motions('用手在空中写一个螺旋星图', catalog=core)
        self.assertEqual(unknown['candidates'], [])
        self.assertTrue(unknown['procedural_allowed'])
        self.assertFalse(unknown['unknown_action_rejected'])

    def test_generic_idle_is_retained_with_explicit_guard_semantics_feedback(self):
        idle = next(entry for entry in self.catalog['entries'] if entry['motion_id'] == 'quaternius.idle_loop')
        before = copy.deepcopy(idle)
        result = motion.search_motions('格斗待机', catalog={'entries': [idle]})
        self.assertEqual(result['candidates'][0]['motion_id'], idle['motion_id'])
        self.assertTrue(any('普通站立待机' in reason and '没有格斗护架验证' in reason
                            for reason in result['candidates'][0]['match_reasons']))
        self.assertEqual(idle, before)
        self.assertEqual(motion.search_motions('格斗待机', catalog=self.catalog)['candidates'][0]['motion_id'],
                         'mixamo.combat_melee.mma_idle')

    def test_daily_aliases_survive_normal_catalog_refresh(self):
        refreshed = motion.build_catalog()
        for query, identifier in (('连拳', 'mixamo.combat_melee.jab_cross'),
                                  ('踢', 'cmu.144_05'), ('踢', 'mixamo.combat_melee.mma_kick'),
                                  ('走停', 'cmu.16_33'), ('退步', 'cmu.76_11'),
                                  ('倒地', 'quaternius.death01'), ('防守', 'cmu.144_07')):
            self.assertIn(identifier, {entry['motion_id'] for entry in
                motion.search_motions(query, limit=100, catalog=refreshed)['candidates']})

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
        self.assertFalse(any(item.get('source_sampling_status') for item in result['entries'] if item['collection'] == 'main'))

    def test_staging_files_are_discoverable_without_inflating_motion_count(self):
        entries = self.catalog['entries']
        staged = {item['asset']: item for item in entries if item['collection'] == 'rokoko_staging'}
        actual = {str(path.relative_to(PROJECT)) for path in (PROJECT/'rokoko_staging').rglob('*')
                  if path.is_file() and path.suffix.lower() in ('.fbx', '.glb')}
        self.assertEqual(set(staged), actual)
        self.assertEqual((self.catalog['legacy_main_motion_count'], self.catalog['staging_fbx_count'],
                          self.catalog['staging_glb_count']), (66, 87, 5))
        self.assertEqual(self.catalog['main_motion_count'], 69)
        self.assertEqual(self.catalog['entry_counts']['character_asset'], 2)
        self.assertEqual(self.catalog['entry_counts']['collection_candidate'], 8)
        self.assertEqual(self.catalog['unique_asset_file_count'], 170)

    def test_collections_and_models_do_not_become_callable_actions(self):
        collections = motion.search_motions('UAL1', catalog=self.catalog)['candidates']
        self.assertEqual(len(collections), 4)
        self.assertTrue(all(entry['entry_kind'] == 'collection_candidate' for entry in collections))
        self.assertTrue(all(not entry['execution']['available'] for entry in collections))
        self.assertEqual(motion.search_motions('女性模型', usable_only=True, catalog=self.catalog)['candidates'], [])
        model = motion.motion_info('staging.unknown.mannequin_f.fbx', self.catalog)
        self.assertTrue(model['available'])
        self.assertEqual(model['available_scope'], 'source_file_exists_only')
        self.assertFalse(model['sampling_available'])

    def test_plain_walking_and_boxing_candidates_include_staging_with_status(self):
        walking = motion.search_motions('普通走路', limit=100, catalog=self.catalog)['candidates']
        self.assertIn('rokoko.mixamo.09_slowwalkforward_mixamo_769', {item['motion_id'] for item in walking})
        self.assertNotIn('weapon', walking[0]['style_hints'])
        boxing = motion.search_motions('拳击', limit=100, catalog=self.catalog)['candidates']
        self.assertIn('mixamo.combat_melee.boxing', {item['motion_id'] for item in boxing})
        self.assertIn('rokoko.mixamo.boxing_mixamo', {item['motion_id'] for item in boxing})
        self.assertTrue(all('execution' in item and 'use_status' in item for item in boxing))

    def test_suggestions_do_not_become_visual_or_naturalness_claims(self):
        candidate = motion.motion_info('rokoko.mixamo.shadowboxing_mixamo', self.catalog)
        self.assertEqual(candidate['semantic_status'], 'filename_inference_only')
        self.assertFalse(candidate['naturalness_verified'])
        self.assertFalse(candidate['execution']['available'])
        self.assertIsNone(candidate['source_fps'])
        self.assertIn('support_metadata', candidate['metadata_gaps'])
        self.assertEqual(candidate['source_provenance']['archives'], ['Combat.zip'])
        self.assertEqual(candidate['source_provenance']['capture_method'], 'not_documented_in_local_manifest')

    def test_retreat_candidates_keep_weapon_style_separate_from_new_unarmed_sources(self):
        result = motion.search_motions('后退', limit=100, catalog=self.catalog)
        self.assertTrue(result['candidates'])
        unarmed = [item for item in result['candidates'] if 'weapon' not in item['style_hints']]
        self.assertEqual({item['motion_id'] for item in unarmed}, {'cmu.76_11','cmu.76_01'})
        weapons = [item for item in result['candidates'] if 'weapon' in item['style_hints']]
        self.assertTrue(weapons)
        self.assertTrue(all(not item['execution']['available'] for item in weapons))
        dodge = motion.search_motions('闪避', catalog=self.catalog)
        self.assertEqual(dodge['candidates'], [])
        self.assertFalse(dodge['unknown_action_rejected'])

    def test_new_alias_and_status_filters_do_not_change_legacy_exact_ids(self):
        result = motion.search_motions('揍一拳', usable_only=True, catalog=self.catalog)
        self.assertIn('mixamo.combat_melee.punching', {item['motion_id'] for item in result['candidates']})
        self.assertTrue(all(item['execution']['available'] for item in result['candidates']))
        self.assertEqual(motion.motion_info('cross', self.catalog)['motion_id'], 'quaternius.punch_cross')
        self.assertEqual(motion.motion_info('Walk', self.catalog)['motion_id'], 'mixamo.basic.walk')
        self.assertTrue(all(item['entry_kind'] == 'collection_candidate' for item in
            motion.search_motions('UAL1', entry_kind='collection_candidate', catalog=self.catalog)['candidates']))

    def test_refresh_preserves_measured_metadata_and_old_source_registration(self):
        result = motion.build_catalog()
        old = {entry['motion_id']: entry for entry in self.catalog['entries']}
        new = {entry['motion_id']: entry for entry in result['entries']}
        self.assertEqual(set(old), set(new))
        for identifier in ('mixamo.basic.walk', 'quaternius.punch_cross'):
            self.assertEqual(new[identifier]['source_fps'], old[identifier]['source_fps'])
            self.assertEqual(new[identifier]['source_frame_range'], old[identifier]['source_frame_range'])
            self.assertEqual(new[identifier]['source_sampling_status'], old[identifier]['source_sampling_status'])

    def test_measured_rokoko_sources_keep_native_fps_calibration_and_preview_separate(self):
        for identifier, fps, bones in (('rokoko.mixamo.09_slowwalkforward_mixamo_769', 30., 53),
                                       ('rokoko.mixamo.boxing_mixamo', 24., 52)):
            entry = motion.motion_info(identifier, self.catalog)
            self.assertEqual(entry['entry_kind'], 'motion')
            self.assertEqual(entry['source_fps'], fps)
            self.assertEqual(entry['key_timebase_fps'], fps)
            self.assertEqual(entry['skeleton']['bone_count'], bones)
            self.assertTrue(entry['sampling_available'])
            self.assertFalse(entry['naturalness_verified'])
            self.assertFalse(entry['loop_allowed'])
            self.assertTrue(entry['calibration_metadata']['t_pose_at_source_zero'])
            self.assertGreater(entry['valid_motion_window_seconds'][0], 0)
            self.assertLess(entry['suggested_source_window_seconds'][1], entry['duration_s'])
            self.assertEqual(entry['validated_playback_windows'][0]['time_scale'], 1.)

    def test_selected_ground_route_is_usable_without_claiming_ordinary_whole_clip_sampling(self):
        entry = motion.motion_info('rokoko.mixamo.getup_wipebloodfrommouth_mixamo_769', self.catalog)
        self.assertEqual(entry['use_status'], 'limited_validated_ground_window')
        self.assertEqual(entry['execution']['function'], 'agent_ground_motion.ground_motion_events')
        self.assertFalse(entry['execution']['ordinary_sampler_validated'])
        self.assertFalse(entry['sampling_available'])
        self.assertIn(entry['motion_id'], {item['motion_id'] for item in
            motion.search_motions('起身', usable_only=True, catalog=self.catalog)['candidates']})

    def test_refresh_keeps_validated_rokoko_and_limited_routes(self):
        refreshed = {entry['motion_id']: entry for entry in motion.build_catalog()['entries']}
        for identifier in ('rokoko.mixamo.boxing_mixamo', 'rokoko.mixamo.getup_wipebloodfrommouth_mixamo_769'):
            old = motion.motion_info(identifier, self.catalog)
            self.assertEqual(refreshed[identifier]['execution'], old['execution'])
            self.assertEqual(refreshed[identifier]['entry_kind'], old['entry_kind'])
            self.assertEqual(refreshed[identifier]['source_fps'], old['source_fps'])

    def test_actual_installed_files_control_search_availability_without_rewriting_facts(self):
        catalog = copy.deepcopy(self.catalog)
        before = json.dumps(catalog, sort_keys=True)
        core = next(item for item in catalog['entries'] if item['motion_id'] == 'quaternius.walk_loop')
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            source = project/core['asset']; source.parent.mkdir(parents=True)
            source.write_bytes(b'An installed file for discovery-only availability tests')
            with patch.object(motion, 'PROJECT', project):
                all_results = motion.search_motions('走路', limit=100, catalog=catalog)['candidates']
                usable = motion.search_motions('走路', usable_only=True, catalog=catalog)['candidates']
        self.assertEqual([item['motion_id'] for item in usable], ['quaternius.walk_loop'])
        self.assertEqual(all_results[0]['motion_id'], 'quaternius.walk_loop')
        missing = next(item for item in all_results if item['motion_id'] == 'mixamo.basic.walk')
        self.assertFalse(missing['available'])
        self.assertFalse(missing['sampling_available'])
        self.assertTrue(missing['execution']['available'])
        self.assertEqual(json.dumps(catalog, sort_keys=True), before)

    def test_present_unadapted_file_is_not_usable(self):
        candidate = next(item for item in self.catalog['entries'] if item['motion_id'] == 'rokoko.mixamo.shadowboxing_mixamo')
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            path = project/candidate['asset'];path.parent.mkdir(parents=True)
            path.write_bytes(b'Present, unadapted candidate for discovery-only tests')
            with patch.object(motion, 'PROJECT', project):
                result = motion.search_motions('影子拳击', catalog={'entries':[candidate]})['candidates'][0]
                self.assertTrue(result['available'])
                self.assertFalse(result['sampling_available'])
                self.assertEqual(motion.search_motions('影子拳击', usable_only=True, catalog={'entries':[candidate]})['candidates'], [])

    def test_refresh_in_checkout_without_optional_packs_keeps_registered_ids_and_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            folder = project/'knowledge/motions';folder.mkdir(parents=True)
            index = folder/'catalog.json'
            index.write_text(json.dumps(self.catalog))
            (folder/'duel.json').write_text((PROJECT/'knowledge/motions/duel.json').read_text())
            with patch.object(motion, 'PROJECT', project), patch.object(motion, 'CATALOG_PATH', index):
                refreshed = motion.write_catalog(path=index)
                self.assertEqual({entry['motion_id'] for entry in refreshed['entries']},
                                 {entry['motion_id'] for entry in self.catalog['entries']})
                self.assertEqual(len(refreshed['entries']), 175)
                self.assertEqual(motion.search_motions('走路', usable_only=True, catalog=refreshed)['candidates'], [])
                discovered = motion.search_motions('走路', limit=100, catalog=refreshed)['candidates']
                self.assertTrue(discovered)
                self.assertTrue(all(not entry['available'] for entry in discovered))
                by_id = {entry['motion_id']:entry for entry in refreshed['entries']}
                self.assertEqual(by_id['rokoko.mixamo.boxing_mixamo']['source_fps'], 24.)
                self.assertEqual(by_id['rokoko.mixamo.getup_wipebloodfrommouth_mixamo_769']['execution']['function'],
                                 'agent_ground_motion.ground_motion_events')
                self.assertEqual(by_id['cmu.02_01']['source_fps'], 30.)
                self.assertEqual(by_id['cmu.80_10']['calibration_metadata']['first_motion_source_frame'], 2)
                self.assertTrue(all('available' not in entry and 'sampling_available' not in entry
                                    for entry in json.loads(index.read_text())['entries']))

    def test_cmu_selected_sources_keep_actual_import_facts_and_clear_license(self):
        cmu = [entry for entry in self.catalog['entries'] if entry['provider'] == 'cmu_fbx']
        self.assertEqual({entry['motion_id'] for entry in cmu},
                         {'cmu.'+identifier for identifier in ('02_01','07_01','02_03','79_08','80_10',
                          '69_16','16_17','16_33','22_14','144_05','144_07','144_26','76_11','76_01')})
        last_frames = {'cmu.02_01': 86, 'cmu.07_01': 79, 'cmu.02_03': 44,
                       'cmu.79_08': 111, 'cmu.80_10': 194, 'cmu.69_16': 282,
                       'cmu.16_17': 130, 'cmu.16_33': 72, 'cmu.22_14': 177,
                       'cmu.144_05': 697, 'cmu.144_07': 612, 'cmu.144_26': 837,
                       'cmu.76_11': 129, 'cmu.76_01': 233}
        for entry in cmu:
            self.assertEqual(entry['source_fps'], 30.)
            self.assertEqual(entry['key_timebase_fps'], 30.)
            self.assertEqual(entry['skeleton']['bone_count'], 43)
            self.assertFalse(entry['skeleton']['finger_animation'])
            self.assertEqual(entry['source_frame_range'], [1., last_frames[entry['motion_id']]])
            self.assertAlmostEqual(entry['duration_s'], (last_frames[entry['motion_id']]-1)/30.)
            self.assertEqual(entry['valid_motion_window_seconds'], [1/30., entry['duration_s']])
            self.assertTrue(entry['calibration_metadata']['t_pose_at_source_zero'])
            self.assertFalse(entry['naturalness_verified'])
            self.assertFalse(entry['loop_allowed'])
            self.assertNotEqual(entry['license']['id'], 'CC0-1.0')
            self.assertFalse(entry['license']['raw_animation_resale_allowed'])
            self.assertTrue(entry['license']['pass_conditions_to_recipients'])
            provenance = entry['source_provenance']
            self.assertEqual(provenance['source_url'], 'https://mocap.cs.cmu.edu/')
            self.assertEqual(provenance['capture_method'], 'human_optical_motion_capture')
            self.assertIn('rancidmilk.itch.io', provenance['fbx_conversion_url'])
            self.assertEqual(provenance['mirror_role'], 'third_party_distributor_not_original_author')
            self.assertIn('animations/'+entry['motion_id'][4:]+'.fbx', provenance['download_url'])

    def test_bundled_core_index_matches_offline_distribution_motion_ids(self):
        manifest = json.loads((Path(__file__).resolve().parents[1]/'knowledge/assets/distribution.json').read_text())
        self.assertEqual(set(self.catalog['bundled_core_motion_ids']),
                         set(manifest['profiles']['core']['motion_ids']))

    def test_cmu_sampler_scope_keeps_contact_and_floor_quality_separate(self):
        for entry in self.catalog['entries']:
            if entry['motion_id'] not in ('cmu.02_01','cmu.07_01','cmu.02_03','cmu.79_08','cmu.80_10'):
                continue
            self.assertTrue(entry['execution']['available'])
            self.assertEqual(set(entry['execution']['target_profiles']), {'beta', 'quaternius_mannequin_f'})
            self.assertEqual(entry['compatibility']['mapped_bones'], 19)
            self.assertEqual(entry['source_sampling_status'], 'verified_selected_sources_and_profiles')
            scope = entry['validation_scope']
            self.assertFalse(scope['contact_validated'])
            self.assertFalse(scope['foot_support_validated'])
            self.assertFalse(scope['recovery_validated'])
            self.assertFalse(scope['naturalness_verified'])
            self.assertEqual(scope['timeline_saved_reload_validated'],
                             entry['motion_id'] in ('cmu.02_01', 'cmu.79_08'))
            self.assertEqual(set(scope['measurements']), {'beta', 'quaternius_mannequin_f'})
            self.assertIsNone(entry['support_metadata'])
            self.assertIsNone(entry['recovery_metadata'])


    def test_new_cmu_source_discovery_needs_actual_adapter_measurements(self):
        discovered = {entry['motion_id']: entry for entry in motion._cmu_entries({})}
        selected = {'cmu.'+identifier for identifier in ('69_16','16_17','16_33','22_14',
                    '144_05','144_07','144_26','76_11','76_01')}
        for identifier in selected:
            candidate = discovered[identifier]
            self.assertEqual(candidate['entry_kind'], 'motion_file_candidate')
            self.assertFalse(candidate['execution']['available'])
            self.assertEqual(candidate['source_sampling_status'], 'adapter_not_validated')
            self.assertIsNone(candidate['support_metadata'])
            self.assertIsNone(candidate['recovery_metadata'])
        self.assertNotIn('cmu.76_04', discovered)
        for identifier in ('cmu.144_05','cmu.144_07','cmu.144_26'):
            self.assertIsNone(discovered[identifier]['suggested_source_window_seconds'])
            self.assertEqual(discovered[identifier]['semantic_annotations']['single_action_window_status'],
                             'not_selected_from_long_source')
        for query, identifier in (('原地转身','cmu.69_16'),('走路左转','cmu.16_17'),('转弯','cmu.16_17'),('stop','cmu.16_33'),
                                  ('停步','cmu.16_33'),('下蹲起立','cmu.22_14'),
                                  ('前踢','cmu.144_05'),('左侧格挡','cmu.144_07'),
                                  ('右侧格挡','cmu.144_26'),('大步后撤','cmu.76_11'),
                                  ('撤步出拳','cmu.76_01')):
            self.assertIn(identifier, {entry['motion_id'] for entry in
                motion.search_motions(query, limit=100, catalog=self.catalog)['candidates']})

    def test_cmu_source_heading_and_long_performances_do_not_become_fixed_single_actions(self):
        sources = {entry['motion_id']:entry for entry in self.catalog['entries']}
        for identifier in ('cmu.22_14','cmu.76_11','cmu.76_01'):
            observation = sources[identifier]['source_motion_metadata']
            self.assertEqual(observation['initial_facing'], 'near_negative_x')
            self.assertLess(observation['first_motion_forward_xy'][0], -.9)
        for identifier in ('cmu.16_17','cmu.16_33','cmu.69_16'):
            observation = sources[identifier]['source_motion_metadata']
            self.assertEqual(observation['initial_facing'], 'near_negative_y')
            self.assertLess(observation['first_motion_forward_xy'][1], -.9)
        self.assertGreater(sources['cmu.69_16']['source_motion_metadata']['unwrapped_heading_change_degrees'], 300)
        squat = sources['cmu.22_14']['source_motion_metadata']['source_hip_start_min_end_cm']
        self.assertLess(squat[1], squat[0] - 50)
        self.assertLess(abs(squat[2] - squat[0]), .2)
        for identifier in ('cmu.144_05','cmu.144_07','cmu.144_26'):
            source = sources[identifier]
            candidate = source['suggested_source_window_seconds']
            self.assertGreater(candidate[0], source['valid_motion_window_seconds'][0])
            self.assertLess(candidate[1] - candidate[0], 2.)
            self.assertEqual(source['semantic_annotations']['single_action_window_status'],
                             'representative_keyframe_candidate_not_full_recovery_validated')
            self.assertFalse(source['naturalness_verified'])


    def test_extra_cmu_adapter_scope_does_not_claim_full_frame_support_or_recovery(self):
        selected = {'cmu.'+identifier for identifier in ('69_16','16_17','16_33','22_14',
                    '144_05','144_07','144_26','76_11','76_01')}
        visual = {'cmu.16_17','cmu.16_33','cmu.22_14','cmu.144_05','cmu.144_07','cmu.144_26'}
        for entry in self.catalog['entries']:
            if entry['motion_id'] not in selected:
                continue
            scope = entry['validation_scope']
            self.assertTrue(entry['execution']['available'])
            self.assertEqual(set(entry['execution']['target_profiles']), {'beta','quaternius_mannequin_f'})
            self.assertFalse(scope['sampling']['full_frame_quality_review'])
            self.assertFalse(scope['foot_support_validated'])
            self.assertFalse(scope['hand_shape_validated'])
            self.assertFalse(scope['contact_validated'])
            self.assertFalse(scope['recovery_validated'])
            self.assertFalse(scope['naturalness_verified'])
            self.assertEqual(scope['timeline_saved_reload_validated'], entry['motion_id'] in visual)
            if entry['motion_id'].startswith('cmu.144_'):
                self.assertEqual(scope['sampling']['method'],
                                 'eight_selected_source_times_including_candidate_window_and_full_source_quartiles')
                self.assertTrue(all(row['sample_count'] == 8 for row in scope['measurements'].values()))
            else:
                self.assertEqual(scope['sampling']['stride_source_frames'], 3)
            self.assertIn(entry['motion_id'], {item['motion_id'] for item in
                motion.search_motions(entry['motion_id'], usable_only=True, catalog=self.catalog)['candidates']})
        squat = next(entry for entry in self.catalog['entries'] if entry['motion_id'] == 'cmu.22_14')
        self.assertEqual(squat['semantic_annotations']['observed_motion'], 'two_very_deep_squat_cycles')
        self.assertFalse(squat['semantic_annotations']['shallow_squat_validated'])
        self.assertLess(squat['validation_scope']['measurements']['quaternius_mannequin_f']['sampled_skin_floor_range_m'][0], -.058)

    def test_refresh_keeps_preview_candidates_without_replacing_full_calibrated_default(self):
        old = {entry['motion_id']:entry for entry in self.catalog['entries']}
        refreshed = {entry['motion_id']:entry for entry in motion.build_catalog()['entries']}
        expected = {'cmu.144_05':[4.1,5.6], 'cmu.144_07':[.6,2.], 'cmu.144_26':[61/30,103/30]}
        for identifier, window in expected.items():
            source = refreshed[identifier]
            self.assertEqual(source['suggested_source_window_seconds'], window)
            self.assertEqual(source['valid_motion_window_seconds'], [1/30,source['duration_s']])
            self.assertEqual(source['validation_scope'], old[identifier]['validation_scope'])
            self.assertEqual(source['semantic_annotations'], old[identifier]['semantic_annotations'])
            self.assertFalse(source['validation_scope']['recovery_validated'])

    def test_cmu_labels_are_discoverable_without_claiming_contact_or_compatibility(self):
        for query, expected in (('普通走路', {'cmu.02_01', 'cmu.07_01'}),
                                ('慢跑', {'cmu.02_03'}), ('拳击', {'cmu.79_08', 'cmu.80_10'})):
            result = motion.search_motions(query, limit=100, catalog=self.catalog)['candidates']
            self.assertTrue(expected <= {entry['motion_id'] for entry in result})
        candidate = copy.deepcopy(next(entry for entry in self.catalog['entries']
                                       if entry['motion_id'] == 'cmu.79_08'))
        candidate['execution']['available'] = False
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            source = project/candidate['asset']; source.parent.mkdir(parents=True)
            source.write_bytes(b'Discovery-only candidate, not an adapted source')
            with patch.object(motion, 'PROJECT', project):
                result = motion.search_motions('拳击', catalog={'entries': [candidate]})['candidates'][0]
                self.assertTrue(result['available'])
                self.assertFalse(result['sampling_available'])
                self.assertEqual(motion.search_motions('拳击', usable_only=True,
                                                     catalog={'entries': [candidate]})['candidates'], [])

    def test_cmu_refresh_preserves_measured_profile_scope_without_erasing_existing_ids(self):
        previous = copy.deepcopy(self.catalog['entries'])
        source = next(entry for entry in previous if entry['motion_id'] == 'cmu.02_01')
        source['execution']['available'] = True
        source['source_sampling_status'] = 'verified_selected_profiles'
        source['validation_scope'] = {'target_profiles': ['test_profile'], 'contact_validated': False}
        with patch.object(motion, '_previous_entries', return_value={entry['motion_id']: entry for entry in previous}):
            refreshed = motion.build_catalog()
        by_id = {entry['motion_id']: entry for entry in refreshed['entries']}
        self.assertEqual(set(by_id), {entry['motion_id'] for entry in self.catalog['entries']})
        self.assertEqual(by_id['cmu.02_01']['validation_scope'], source['validation_scope'])
        self.assertEqual(by_id['cmu.02_01']['execution'], source['execution'])
        self.assertEqual(by_id['cmu.02_01']['source_sampling_status'], 'verified_selected_profiles')


if __name__ == '__main__':
    unittest.main()
