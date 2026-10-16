"""Standing and landing contracts without importing Blender.

These checks cover physical timing and reliable landing directions. Real skin,
gait phase continuity and resume pose continuity require Blender validation.
"""
import ast
import math
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


@pytest.fixture(scope='module')
def landing_heading():
    path = SCRIPTS / 'locomotion_gait.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    definitions = [node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and node.name == 'landing_heading']
    assert len(definitions) == 1
    namespace = {'math': math}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['landing_heading']


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


@pytest.mark.parametrize('projected_heading', [-179, -40, 0, 179, None])
def test_swing_projection_noise_cannot_turn_the_landing_foot(landing_heading, projected_heading):
    # The foot was ten degrees outward when the body faced thirty degrees.
    # A sixty-degree body turn carries that reliable direction to 100 degrees,
    # regardless of the rolling swing foot's current projected ankle/toe ray.
    current = None if projected_heading is None else math.radians(projected_heading)
    actual = landing_heading(current, 0, math.radians(90),
                             (math.radians(40), math.radians(30)))
    assert math.degrees(actual) == pytest.approx(100)


@pytest.mark.parametrize('foot,old_body,new_body,expected', [
    (40, 30, 90, 100),
    (-40, -30, -90, -100),
    (40, 30, -30, -20),
    (-40, -30, 30, 20),
])
def test_reliable_foot_direction_follows_actual_body_turn_in_either_direction(
        landing_heading, foot, old_body, new_body, expected):
    actual = landing_heading(math.radians(170), 0, math.radians(new_body),
                             (math.radians(foot), math.radians(old_body)))
    assert math.degrees(actual) == pytest.approx(expected)


@pytest.mark.parametrize('support_weight', [1e-9, .5, 1])
def test_valid_supported_foot_keeps_its_actual_world_direction(landing_heading, support_weight):
    current = math.radians(-73)
    assert landing_heading(current, support_weight, math.radians(120),
                           (math.radians(20), math.radians(-10))) == current


@pytest.mark.parametrize('support_weight', [0, 1e-9, 1])
def test_missing_projected_yaw_uses_reliable_history_even_during_contact(
        landing_heading, support_weight):
    # A near-vertical toe ray has no reliable horizontal direction. A tiny
    # contact influence must not turn that missing direction into an anchor.
    actual = landing_heading(None, support_weight, math.radians(90),
                             (math.radians(40), math.radians(30)))
    assert math.degrees(actual) == pytest.approx(100)


@pytest.mark.parametrize('projected_heading,support_weight', [(179, 0), (-179, 0), (None, 1)])
def test_first_landing_without_support_history_uses_body_direction(
        landing_heading, projected_heading, support_weight):
    current = None if projected_heading is None else math.radians(projected_heading)
    body = math.radians(-35)
    assert landing_heading(current, support_weight, body) == body


@pytest.mark.parametrize('foot,old_body,new_body,expected', [
    (170, 179, -179, 172),
    (-170, -179, 179, -172),
])
def test_body_wrap_crossing_transports_the_foot_by_two_degrees(
        landing_heading, foot, old_body, new_body, expected):
    actual = landing_heading(None, 0, math.radians(new_body),
                             (math.radians(foot), math.radians(old_body)))
    assert math.degrees(actual) == pytest.approx(expected)
