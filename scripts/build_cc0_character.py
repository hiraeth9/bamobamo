"""Build only the independently licensed Quaternius character profile/cache.

Run with background Blender. Beta and every other catalog entry are retained.
The real source mesh/weights are kept; semantic bone aliases let the ordinary
motion masks and FK/IK tools address this independent character.
"""
import argparse
import json
import math
from pathlib import Path
import shutil
import sys
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'scripts'))
CHARACTER='quaternius_mannequin_f'
IDLE_ASSET='assets/incoming/combat/quaternius/ual1_standard/extracted/Universal Animation Library[Standard]/Unreal-Godot/UAL1_Standard_RM.glb'


def main():
    import bpy
    from mathutils import Matrix,Vector
    from agent_character import CATALOG,binding_facts,file_sha256,matrix_values,rig_facts
    from agent_motion import _import_samples
    from agent_motion_controls import read_pose,set_pose
    from duel_motion_sources import bone_mapping
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path)
    parser.add_argument('--output-dir',type=Path,default=PROJECT/'bundled_assets/characters'/CHARACTER)
    parser.add_argument('--idle-asset',default=IDLE_ASSET)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    output=args.output_dir.resolve();output.mkdir(parents=True,exist_ok=True)
    source=output/'source.fbx'
    incoming=args.source or (source if source.exists() else PROJECT/'rokoko_staging/unknown/Mannequin_F.fbx')
    if incoming.resolve()!=source.resolve():shutil.copy2(incoming,source)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(source))
    arm=next(o for o in bpy.context.scene.objects if o.type=='ARMATURE')
    meshes=[o for o in bpy.context.scene.objects if o.type=='MESH']
    rename={original:alias for alias,original in bone_mapping().items()}
    rename['root']='MotionRoot'
    for bone in arm.data.bones:
        bone.name=rename[bone.name]
    for mesh in meshes:
        for group in mesh.vertex_groups:
            if group.name in rename:group.name=rename[group.name]
    arm.name='Quaternius_Mannequin_F_Rig'
    arm.animation_data_clear()
    # FBX exports this mannequin physically facing +Y, while the framework
    # and GLB motion sources face -Y. Rotate the whole imported character,
    # including its real skin, rather than reversing individual limb motion.
    bpy.context.view_layer.update()
    left=arm.matrix_world@arm.data.bones['mixamorig:LeftArm'].head_local
    right=arm.matrix_world@arm.data.bones['mixamorig:RightArm'].head_local
    across=left-right
    facing=Matrix.Rotation(-math.atan2(across.y,across.x),4,'Z')
    imported=set([arm,*meshes])
    for obj in imported:
        if obj.parent not in imported:obj.matrix_world=facing@obj.matrix_world
    bpy.context.view_layer.update()
    # Cache a real CC0 Idle pose, so adding an actor does not expose the bind T.
    idle=_import_samples(arm,{'provider':'quaternius_glb','asset':args.idle_asset,
                              'clip':'Idle_Loop','source_fps':30.})
    set_pose(arm,idle['poses'][0])
    material=bpy.data.materials.new('CC0_Matte_White')
    material.use_nodes=True;material.diffuse_color=(.74,.76,.79,1)
    bsdf=material.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value=material.diffuse_color
    bsdf.inputs['Roughness'].default_value=.8
    for mesh in meshes:
        mesh.animation_data_clear();mesh.data.materials.clear();mesh.data.materials.append(material)
    bpy.context.view_layer.update()
    skeleton=rig_facts(arm);binding=binding_facts(meshes,arm)
    assert binding['binding_ok'],binding
    points=[]
    for mesh in meshes:
        evaluated=mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
        geometry=evaluated.to_mesh()
        try:points.extend(evaluated.matrix_world@vertex.co for vertex in geometry.vertices)
        finally:evaluated.to_mesh_clear()
    low=[min(point[i] for point in points) for i in range(3)]
    high=[max(point[i] for point in points) for i in range(3)]
    cache=output/'character.blend'
    bpy.data.libraries.write(str(cache),set([arm,*meshes]),fake_user=True,compress=True)
    relative=lambda path:str(path.resolve().relative_to(PROJECT))
    entry={'character_id':CHARACTER,'version':1,'source_asset':relative(source),'source_sha256':file_sha256(source),
        'cache_asset':relative(cache),'cache_sha256':file_sha256(cache),
        'skeleton':skeleton,'binding':binding,'accepted_binding_signatures':[binding['signature']],
        'cache_binding_signature':binding['signature'],
        'object_transform':{'scale':list(arm.scale),'matrix_basis':matrix_values(arm.matrix_basis),
                            'matrix_world':matrix_values(arm.matrix_world)},
        'world_unit':'metre','rest_bone_unit':'metre','up_axis':'+Z','forward_axis':'-Y',
        'validation_state':'independent_cc0_profile_built','report':None,
        'license':{'source':'Quaternius Universal Animation Library 2 Mannequin_F',
                   'identifier':'CC0-1.0','redistribution_review_required':False,
                   'url':'https://quaternius.com/packs/universalanimationlibrary2.html'},
        'height_m':high[2]-low[2], 'rest_world_bounds_m':{'min':low,'max':high},
        'default_pose_source':args.idle_asset,'default_pose_source_frame':idle['source_frame_range'][0],
        'default_pose':read_pose(arm),
        'source_bone_aliases':rename,'extra_root_bone':'MotionRoot',
        'identity_scope':'Original CC0 Mannequin mesh/weights, semantic bone aliases and a real CC0 Idle pose; independent of Beta.'}
    document=json.loads(CATALOG.read_text(encoding='utf-8'))
    document['characters'][CHARACTER]=entry
    CATALOG.write_text(json.dumps(document,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (output/'LICENSE.txt').write_text('Quaternius Mannequin_F from Universal Animation Library 2\nLicense: CC0 1.0 Universal\nhttps://creativecommons.org/publicdomain/zero/1.0/\nhttps://quaternius.com/packs/universalanimationlibrary2.html\n\nThis bundle retains the original source FBX. The character cache preserves its real mesh and weights, adds semantic bone aliases, a matte material and a CC0 Idle pose.\n',encoding='utf-8')
    print('CC0_CHARACTER_BUILT '+json.dumps({'character_id':CHARACTER,'source':relative(source),
          'cache':relative(cache),'cache_bytes':cache.stat().st_size,'bone_count':skeleton['bone_count'],
          'mesh_vertex_counts':[len(mesh.data.vertices) for mesh in meshes], 'height_m':entry['height_m'],
          'idle_clip':'Idle_Loop'}),flush=True)


if __name__=='__main__':main()
