import bpy, json

targets = [
    'mixamo_fbx/combat_melee/Mma Idle.fbx',
    'mixamo_fbx/combat_melee/Hook.fbx',
    'mixamo_fbx/basic/Walk.fbx',
    'mixamo_fbx/combat_reaction/Head Hit.fbx',
]

out = {}
for t in targets:
    p = '/root/rivermind-data/bamo/' + t
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for a in list(bpy.data.actions):
        bpy.data.actions.remove(a)
    try:
        bpy.ops.import_scene.fbx(filepath=p)
    except Exception as e:
        out[t] = {'error': repr(e)}
        continue
    arms = [o for o in bpy.data.objects if o.type == 'ARMATURE']
    if not arms:
        out[t] = {'error': 'no_armature'}
        continue
    arm = arms[0]
    bones = [b.name for b in arm.data.bones]
    out[t] = {
        'bone_count': len(bones),
        'has_headtop': 'mixamorig:HeadTop_End' in bones,
        'has_toe_end': 'mixamorig:LeftToe_End' in bones,
        'finger4': [b for b in bones if b.endswith('4')],
        'bones': bones,
    }

print(json.dumps(out, indent=1, ensure_ascii=False))
