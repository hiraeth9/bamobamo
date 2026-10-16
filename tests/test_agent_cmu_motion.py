"""CMU body-map contracts without importing Blender."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from agent_motion import _cmu_body_mapping

BODY = ('hip abdomen chest neck head lCollar lShldr lForeArm lHand lThigh lShin lFoot '
        'rCollar rShldr rForeArm rHand rThigh rShin rFoot').split()
TARGETS = {name:'mixamorig:'+name for name in ('Hips','Spine','Spine2','Neck','Head',
    'LeftShoulder','LeftArm','LeftForeArm','LeftHand','LeftUpLeg','LeftLeg','LeftFoot',
    'RightShoulder','RightArm','RightForeArm','RightHand','RightUpLeg','RightLeg','RightFoot')}


def test_cmu_map_leaves_uncaptured_finger_and_extra_spine_channels_to_inherit():
    targets = {**TARGETS,**{name:'mixamorig:'+name for name in ('Spine1','LeftHandIndex1','LeftToeBase')}}
    mapping = _cmu_body_mapping(BODY+['lIndex1','lIndex2'],targets)
    assert mapping['mixamorig:LeftArm']=='lShldr'
    assert mapping['mixamorig:Spine2']=='chest'
    assert 'mixamorig:Spine1' not in mapping
    assert 'mixamorig:LeftHandIndex1' not in mapping
    assert 'mixamorig:LeftToeBase' not in mapping


def test_cmu_missing_body_joint_names_the_source_channel_to_fix():
    with pytest.raises(ValueError,match='missing: lHand'):
        _cmu_body_mapping([name for name in BODY if name!='lHand'],{'Hips':'mixamorig:Hips'})


def test_unrelated_target_rig_gets_a_readable_body_mapping_error():
    with pytest.raises(ValueError,match='target needs semantic body bones; missing: Spine'):
        _cmu_body_mapping(BODY,{'Hips':'mixamorig:Hips'})
