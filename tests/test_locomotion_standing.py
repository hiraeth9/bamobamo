"""Standing timing contracts without importing Blender or reproducing the ramp.

These checks cover seconds and transition timing. Real evaluated skin, gait
phase continuity and resume pose continuity require the Blender validation.
"""
import ast
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


@pytest.fixture(scope='module')
def standing_weight():
    namespace = {}
    for filename, names in (('duel_scene.py', {'smooth'}),
                            ('locomotion_gait.py', {'clamp', 'standing_weight'})):
        tree = ast.parse((SCRIPTS / filename).read_text(encoding='utf-8'))
        definitions = [node for node in tree.body
                       if isinstance(node, ast.FunctionDef) and node.name in names]
        assert {node.name for node in definitions} == names
        module = ast.Module(body=definitions, type_ignores=[])
        exec(compile(module, str(SCRIPTS / filename), 'exec'), namespace)
    return namespace['standing_weight']


def test_short_pause_leaves_the_actual_landing_pose_in_control(standing_weight):
    # A brief pause cannot replace the still-landing motion with a standing pose.
    assert all(standing_weight(age) == 0 for age in (0., .05, .15, .25, .28))


def test_standing_blend_is_gradual_and_finishes_in_finite_time(standing_weight):
    samples = [standing_weight(age / 100) for age in range(28, 59)]
    assert 0 < standing_weight(.4) < 1
    assert samples == sorted(samples)
    assert max(b - a for a, b in zip(samples, samples[1:])) < .1
    assert standing_weight(.58) == pytest.approx(1)
    assert standing_weight(.8) == standing_weight(10.) == 1


@pytest.mark.parametrize('fps', [24, 30, 60, 120])
def test_standing_deadlines_are_seconds_at_different_render_fps(standing_weight, fps):
    samples = [(frame / fps, standing_weight(frame / fps)) for frame in range(fps + 1)]
    first_transition = next(age for age, weight in samples if weight > 1e-10)
    first_standing = next(age for age, weight in samples if weight >= 1 - 1e-10)
    # Each renderer samples the same physical deadlines within one frame;
    # a fixed frame-count delay would violate this at other frame rates.
    assert .28 < first_transition <= .28 + 1 / fps + 1e-12
    assert .58 - 1e-12 <= first_standing <= .58 + 1 / fps + 1e-12


def test_requested_landing_and_settle_durations_have_separate_effects(standing_weight):
    custom = lambda age: standing_weight(age, landing_seconds=.1, settle_seconds=.2)
    assert custom(.1) == 0
    assert 0 < custom(.2) < 1
    assert custom(.3) == pytest.approx(1)
    assert standing_weight(.2) == 0
    assert standing_weight(.3) < 1
