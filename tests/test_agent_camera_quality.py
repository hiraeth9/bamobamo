import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from agent_camera_quality import normalize_requirements, REGIONS, DEFAULT_REGIONS


class CameraContractTests(unittest.TestCase):
    def contract(self, **fields):
        return {'version':1,'shots':[{'camera':'Camera any name','actor_ids':['A','B','C','D'],
                                     'focus_objects':['Custom machine'],**fields}]}

    def test_open_counts_names_and_long_scene(self):
        value=normalize_requirements(self.contract(), 'job', -20, 12000)
        self.assertEqual(value['shots'][0]['frame_end'],12000)
        self.assertEqual(value['shots'][0]['actor_ids'],['A','B','C','D'])
        self.assertNotIn('job_id',self.contract())

    def test_regions_support_closeup_and_pov(self):
        value=normalize_requirements(self.contract(framing='regions',regions=['right_hand']), 'job',1,600)
        self.assertEqual(value['shots'][0]['regions'],['right_hand'])
        self.assertIn('left_arm',REGIONS)

    def test_composite_focus_groups_need_no_duplicate_members(self):
        value=normalize_requirements(self.contract(focus_objects=[],focus_groups={'chair':['Seat','Leg','Seat']}),'job',1,600)
        self.assertEqual(value['shots'][0]['focus_groups'],{'chair':['Seat','Leg']})
        self.assertEqual(value['shots'][0]['focus_objects'],[])

    def test_shots_without_subjects_are_rejected(self):
        for shot in ({'camera':'Empty'},
                     {'camera':'Empty','actor_ids':[], 'focus_objects':[], 'focus_groups':{}}):
            with self.subTest(shot=shot), self.assertRaisesRegex(ValueError, 'at least one actor or focus object'):
                normalize_requirements({'version':1,'shots':[shot]},'job',1,600)

    def test_actor_shots_cannot_disable_all_occlusion_regions(self):
        for framing in ('full_body','regions'):
            with self.subTest(framing=framing), self.assertRaisesRegex(ValueError, 'regions must name'):
                normalize_requirements(self.contract(framing=framing,regions=[]),'job',1,600)

    def test_full_body_still_defaults_to_body_occlusion_regions(self):
        value=normalize_requirements(self.contract(),'job',1,600)
        self.assertEqual(value['shots'][0]['regions'],list(DEFAULT_REGIONS))
        self.assertNotIn('regions',self.contract()['shots'][0])

    def test_object_only_shots_need_no_actor_regions(self):
        for fields in ({'focus_objects':['Door']},
                       {'focus_objects':[], 'focus_groups':{'chair':['Seat','Leg']}}):
            for framing in ('full_body','regions'):
                with self.subTest(fields=fields,framing=framing):
                    value=normalize_requirements(self.contract(actor_ids=[],framing=framing,regions=[],**fields),
                                                 'job',1,600)
                    self.assertEqual(value['shots'][0]['actor_ids'],[])
                    self.assertEqual(value['shots'][0]['regions'],[])

    def test_explicit_camera_facts_are_preserved(self):
        value=normalize_requirements(self.contract(fixed_transform=True,expected_position=[61,2,3],
                    look_at=[1,2,3],expected_azimuth_deg=55,expected_elevation_deg=18,
                    expected_fov_deg=75,expected_projection='perspective'), 'job',1,600)
        self.assertEqual(value['shots'][0]['expected_position'],[61.,2.,3.])
        self.assertEqual(value['shots'][0]['expected_projection'],'PERSP')

    def test_bad_or_foreign_contract_is_rejected(self):
        for value in [{'version':1,'shots':[]},{'version':2,'shots':[]},{'version':True,'shots':[]},
                      {**self.contract(),'job_id':'foreign'},self.contract(margin=float('nan')),
                      self.contract(frame_start=0),self.contract(framing='regions',regions=['imaginary']),
                      self.contract(expected_position=[float('nan'),0,0]),self.contract(fixed_transform=1),
                      self.contract(expected_elevation_deg=20)]:
            with self.subTest(value=value),self.assertRaises((ValueError,TypeError)):
                normalize_requirements(value,'job',1,600)


if __name__=='__main__':unittest.main()
