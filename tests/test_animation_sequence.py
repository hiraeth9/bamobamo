"""Run with: ./blender -b --python-exit-code 1 --python tests/test_animation_sequence.py"""
import math
import sys
import unittest
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from animation_sequence import sequence_actions


class ActionSequenceTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.object.armature_add()
        self.armature = bpy.context.object
        self.bone = self.armature.pose.bones[0]
        self.bone.rotation_mode = "XYZ"
        self.actions = []
        # The third clip is longer than its allocated segment, exercising a
        # fractional repeat as well as slots created for distinct actions.
        for angle, duration in [(0.6, 38), (1.0, 102), (1.4, 480)]:
            self.armature.animation_data_create().action = None
            self.bone.rotation_euler = (angle, 0, 0)
            self.bone.keyframe_insert(data_path="rotation_euler", frame=1)
            self.bone.keyframe_insert(data_path="rotation_euler", frame=1 + duration)
            action = self.armature.animation_data.action
            action.use_fake_user = True
            self.actions.append(action)

    def angle_at(self, frame):
        whole = math.floor(frame)
        bpy.context.scene.frame_set(whole, subframe=frame - whole)
        evaluated = self.armature.evaluated_get(bpy.context.evaluated_depsgraph_get())
        return evaluated.pose.bones[0].matrix_basis.to_euler("XYZ").x

    def test_crossfades_never_fade_to_rest_pose(self):
        sequence_actions(self.armature, self.actions, 1, 450, blend_frames=12)
        # Every output frame must contain an animated pose, including the two
        # boundaries where independent fades used to produce a T-pose.
        for frame in range(1, 451):
            angle = self.angle_at(frame)
            self.assertGreaterEqual(angle, 0.6 - 1e-5, f"rest pose at {frame}")
            self.assertLessEqual(angle, 1.4 + 1e-5, f"unexpected pose at {frame}")
        for frame, expected in [(145, 0.6), (148, 0.7), (151, 0.8),
                                (154, 0.9), (157, 1.0), (295, 1.0),
                                (301, 1.2), (307, 1.4)]:
            self.assertAlmostEqual(self.angle_at(frame), expected, places=5)

    def test_hard_cuts_have_no_gaps_or_held_upper_tracks(self):
        sequence_actions(self.armature, self.actions, 1, 450, blend_frames=0)
        for frame, expected in [(1, 0.6), (150, 0.6), (151, 1.0),
                                (300, 1.0), (301, 1.4), (450, 1.4)]:
            self.assertAlmostEqual(self.angle_at(frame), expected, places=5)

    def test_uneven_segments_and_odd_overlap_keep_requested_length(self):
        strips = sequence_actions(self.armature, self.actions, 7, 455, blend_frames=11)
        self.assertAlmostEqual(strips[0].frame_start, 7)
        self.assertAlmostEqual(strips[-1].frame_end, 456)
        self.assertLess(strips[-1].repeat, 1)
        for frame in range(7, 456):
            self.assertGreaterEqual(self.angle_at(frame), 0.6 - 1e-5)

    def test_short_segments_and_single_action_remain_animated(self):
        sequence_actions(self.armature, self.actions, 1, 6, blend_frames=12)
        for frame in range(1, 7):
            self.assertGreaterEqual(self.angle_at(frame), 0.6 - 1e-5)
        sequence_actions(self.armature, self.actions[:1], 1, 450)
        for frame in [1, 38, 150, 450]:
            self.assertAlmostEqual(self.angle_at(frame), 0.6, places=5)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ActionSequenceTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError("Action sequence regression tests failed")
