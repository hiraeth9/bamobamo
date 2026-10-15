import json

raw = open('/root/rivermind-data/bamo/bones_result.json').read()
idx = raw.find('{')
d, _ = json.JSONDecoder().raw_decode(raw[idx:])

def show(k):
    v = d[k]
    if 'error' in v:
        print(k, 'ERR', v['error'][:80])
        return
    print(f'--- {k}  (bones={v["bone_count"]}, fr={v["frame_range"]}) ---')
    for b in v['bones']:
        print(f'   {b["name"]:35} parent={b["parent"]}')

show('movella/20210714_s001_teaching_Guestures_tk01_ERJA-mvn001.fbx')
print()
show('unknown/01walkforward_walkspline.fbx')
print()
show('unknown/Reloading.fbx')
print()
# 现有 mixamo_fbx 的 Mma Idle 骨骼，确认目标骨架
show('mixamo/Boxing_mixamo.fbx')
