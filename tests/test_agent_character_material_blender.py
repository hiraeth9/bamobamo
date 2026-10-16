"""Default-material actors do not share later shader edits (isolated bpy)."""
import json
import sys
import tempfile
from pathlib import Path
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import duel_plan
from agent_character import actor_ensure, resolve_actor, scene_actor_facts


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
        for material in [*a_materials.values(), *b_materials.values()]:
            assert max(abs(a-b) for a,b in zip(material.diffuse_color,(.82,.82,.84,1)))<1e-6
            assert max(abs(a-b) for a,b in zip(material.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value,(.82,.82,.84,1)))<1e-6
        before = {pointer:tuple(material.diffuse_color) for pointer,material in b_materials.items()}
        for material in a_materials.values():
            material.diffuse_color = (.9,.1,.2,1)
        actor_ensure(job,'A')
        retained = {slot.material.as_pointer():slot.material
                    for mesh in resolve_actor(job,'A')['meshes'] for slot in mesh.material_slots if slot.material}
        assert retained.keys()==a_materials.keys()
        assert all(max(abs(a-b) for a,b in zip(material.diffuse_color,(.9,.1,.2,1)))<1e-6
                   for material in retained.values())
        assert all(tuple(material.diffuse_color)==before[pointer] for pointer,material in b_materials.items())
        actor_ensure(job,'Original',color=None)
        original = resolve_actor(job,'Original')
        assert any(max(abs(a-b) for a,b in zip(slot.material.diffuse_color,(.82,.82,.84,1)))>.01
                   for mesh in original['meshes'] for slot in mesh.material_slots if slot.material)
        actor_ensure(job,'Colored',color=(.1,.7,.3,1))
        assert all(max(abs(a-b) for a,b in zip(slot.material.diffuse_color,(.1,.7,.3,1)))<1e-6
                   for mesh in resolve_actor(job,'Colored')['meshes'] for slot in mesh.material_slots if slot.material)
        bpy.ops.mesh.primitive_cube_add(size=.1)
        prop = bpy.context.object
        prop.parent = a['armature']
        prop.parent_type = 'BONE'
        prop.parent_bone = 'mixamorig:RightHand'
        prop['wm_object_role'] = 'prop'
        facts = scene_actor_facts(job)
        assert not facts['errors'] and prop.name in facts['declared_prop_meshes']
        print('MATERIAL_VALIDATION default white, explicit color/source materials, actor isolation and original binding passed',flush=True)


if __name__ == '__main__':
    main()
