import bpy, os, json

base = '/root/rivermind-data/bamo/rokoko_staging'
out = {}

def read_fbx(p):
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for a in list(bpy.data.actions):
        bpy.data.actions.remove(a)
    for ar in list(bpy.data.armatures):
        bpy.data.armatures.remove(ar)
    try:
        bpy.ops.import_scene.fbx(filepath=p)
    except Exception as e:
        return {'error': repr(e)}
    arms = [o for o in bpy.data.objects if o.type == 'ARMATURE']
    if not arms:
        return {'error': 'no_armature'}
    arm = arms[0]
    bones = [{'name': b.name, 'parent': b.parent.name if b.parent else None}
             for b in arm.data.bones]
    action = arm.animation_data.action if arm.animation_data else None
    fr = [int(action.frame_range[0]), int(action.frame_range[1])] if action else None
    return {'bone_count': len(bones), 'bones': bones, 'frame_range': fr,
            'fps': bpy.context.scene.render.fps}

for cat in ['movella', 'ual', 'unknown']:
    d = os.path.join(base, cat)
    if not os.path.isdir(d):
        continue
    for f in sorted(os.listdir(d)):
        if not f.lower().endswith('.fbx'):
            continue
        out[f'{cat}/{f}'] = read_fbx(os.path.join(d, f))

# 抽样 mixamo 3 个确认命名
d = os.path.join(base, 'mixamo')
if os.path.isdir(d):
    for f in sorted(os.listdir(d))[:3]:
        if f.lower().endswith('.fbx'):
            out[f'mixamo/{f}'] = read_fbx(os.path.join(d, f))

print(json.dumps(out, indent=1, ensure_ascii=False))
