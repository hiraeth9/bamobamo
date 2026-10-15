"""Default-material actors do not share later shader edits (isolated bpy)."""
import json
import sys
import tempfile
from pathlib import Path
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import duel_plan
from agent_character import actor_ensure, resolve_actor, file_sha256, scene_actor_facts


def main():
    if not bpy.app.background:
        raise RuntimeError('Isolated headless Blender required')
    with tempfile.TemporaryDirectory(prefix='bamo_character_materials_') as directory:
        duel_plan.JOBS = Path(directory)
        job = 'material_test'
        (duel_plan.JOBS/job).mkdir()
        (duel_plan.JOBS/job/'request.json').write_text(json.dumps({'prompt':'two robots'}))
        bpy.ops.wm.read_factory_settings(use_empty=True)
        actor_ensure(job,'A',position=(1,2,0),yaw=.6)
        actor_ensure(job,'B',position=(-1,0,0),yaw=-.4)
        a = resolve_actor(job,'A')
        b = resolve_actor(job,'B')
        a_materials = {slot.material.as_pointer():slot.material for mesh in a['meshes']
                       for slot in mesh.material_slots if slot.material is not None}
        b_materials = {slot.material.as_pointer():slot.material for mesh in b['meshes']
                       for slot in mesh.material_slots if slot.material is not None}
        assert a_materials and b_materials and not(a_materials.keys() & b_materials.keys())
        before = {pointer:tuple(material.diffuse_color) for pointer,material in b_materials.items()}
        for material in a_materials.values():
            material.diffuse_color = (.9,.1,.2,1)
        assert all(tuple(material.diffuse_color)==before[pointer] for pointer,material in b_materials.items())
        bpy.ops.mesh.primitive_cube_add(size=.1)
        prop = bpy.context.object
        prop.parent = a['armature']
        prop.parent_type = 'BONE'
        prop.parent_bone = 'mixamorig:RightHand'
        prop['wm_object_role'] = 'prop'
        facts = scene_actor_facts(job)
        assert not facts['errors'] and prop.name in facts['declared_prop_meshes']
        report = {'passed':True,'scope':'Original default-material Beta instance isolation; no appearance quality claim.',
                  'checks':[{'name':'nonzero_position_and_yaw_default_material_identity','passed':True},
                            {'name':'editing_A_default_material_does_not_change_B','passed':True},
                            {'name':'declared_bone_parent_hand_prop_is_not_robot_replacement','passed':True}],
                  'source_sha256':file_sha256(ROOT/'scripts/agent_character.py')}
        (ROOT/'reports/robot_character_20261014/material_validation.json').write_text(json.dumps(report,indent=2))
        print('MATERIAL_VALIDATION',json.dumps(report),flush=True)


if __name__ == '__main__':
    main()
