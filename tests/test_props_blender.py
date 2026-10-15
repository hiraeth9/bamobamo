"""Real Blender checks: contact, moving hinges, furniture, cameras and reload."""
import contextlib
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import bpy

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from duel_plan import atomic_json,validate_plan
from single_plan import validate_single
from scene_plan import validate_scene,object_catalog
from scene_objects import SceneObjects,corners
from prop_interaction_scene import build
from duel_scene import build as build_duel,point


def single(name,seconds):
    return validate_single({'version':1,'duration_seconds':seconds,'animations':['mixamo_fbx/basic/Idle.fbx']},name)


def environment(seconds,objects,events):
    return {'version':1,'duration_seconds':seconds,'room':{'enabled':True},'objects':objects,'interactions':events}


def main():
    report={'checks':[],'scope':'Procedural object geometry, real Blender contact/feet/clearance/full-frame QA, saved-scene reload; visual naturalness needs preview.'}
    def add(name,passed,**details):
        report['checks'].append({'case':name,'passed':bool(passed),**details})
        print('PROPS',name,'PASS' if passed else 'FAIL',flush=True)
        atomic_json(ROOT/'reports/props_blender_regression.json',report)
    cases=[]
    for name,side,height in [('table_right','right',.75),('table_left','left',.75),('table_low','right',.6),('table_high','right',1.)]:
        obj={'id':'table_1','type':'table','size':[1.2,.7,height]}
        cases.append((name,5,[obj],[{'object':'table_1','action':'touch_table','side':side,'start':0,'end':5}]))
    for kind,side in [('window','right'),('door','left')]:
        cases.append((kind+'_open_close',10,[{'id':kind+'_1','type':kind}],
                      [{'object':kind+'_1','action':'open_'+kind,'side':side,'start':0,'end':5},
                       {'object':kind+'_1','action':'close_'+kind,'side':side,'start':5,'end':10}]))
    cases.append(('furnished_sequence',15,[{'id':k+'_1','type':k} for k in object_catalog()['objects']],
                  [{'object':'table_1','action':'touch_table','start':0,'end':3},
                   {'object':'window_1','action':'open_window','start':3,'end':10},
                   {'object':'window_1','action':'close_window','start':10,'end':15}]))
    for name,seconds,objects,events in cases:
        with tempfile.TemporaryDirectory(prefix='bamo-props-') as tmp:
            folder=Path(tmp);plan=single(name,seconds)
            atomic_json(folder/'scene_plan.json',validate_scene(environment(seconds,objects,events),name,1,seconds))
            with contextlib.redirect_stdout(io.StringIO()): quality=build(plan,folder)
            samples=[]
            for contact in quality['contacts'][::max(1,len(quality['contacts'])//5)]:
                bpy.context.scene.frame_set(contact['frame'])
                samples.append((contact['frame'],point(bpy.data.objects['A_Rig'],contact['limb'].replace('Hand','HandMiddle1')).copy(),contact['limb']))
            bpy.ops.wm.open_mainfile(filepath=str(folder/'scene.blend'))
            reload_error=0
            for frame,expected,limb in samples:
                bpy.context.scene.frame_set(frame)
                reload_error=max(reload_error,(point(bpy.data.objects['A_Rig'],limb.replace('Hand','HandMiddle1'))-expected).length)
            bpy.context.scene.frame_set(plan['frame_count'])
            hinges_retained=all(abs(math.degrees(bpy.data.objects[k+'_hinge'].rotation_euler.z)-v)<1e-3 for k,v in quality['final_angles_deg'].items())
            add(name,quality['passed'] and reload_error<1e-5 and hinges_retained,
                quality=quality,reload_error_m=reload_error,hinges_retained=hinges_retained)
    with tempfile.TemporaryDirectory(prefix='bamo-props-duel-') as tmp:
        folder=Path(tmp)
        plan=validate_plan({'version':1,'duration_seconds':4,'events':[
            {'kind':'strike','actor':'A','motion':'jab','outcome':'hit','region':'head','side':'left','start':.2,'peak':.8,'end':1.4},
            {'kind':'strike','actor':'B','motion':'cross','outcome':'hit','region':'body','side':'right','start':1.8,'peak':2.5,'end':3.3}]},'furnished_duel')
        atomic_json(folder/'scene_plan.json',validate_scene(environment(4,[{'id':k+'_1','type':k} for k in object_catalog()['objects']],[]),plan['job_id'],2,4))
        with contextlib.redirect_stdout(io.StringIO()): quality=build_duel(plan,folder)
        add('furnished_duel',quality['passed'],quality=quality)
    with tempfile.TemporaryDirectory(prefix='bamo-props-single-') as tmp:
        folder=Path(tmp);plan=validate_scene(environment(3,[{'id':k+'_1','type':k} for k in object_catalog()['objects']],[]),'furnished_single',1,3)
        atomic_json(folder/'scene_plan.json',plan)
        cmd=[str(ROOT/'blender'),'-b','-t','4','--python-exit-code','1','--python',str(ROOT/'scripts/render_white_model.py'),'--',
             '--fbx','mixamo_fbx/basic/Idle.fbx','--output',str(folder/'final.mp4'),'--frames','90','--fps','30',
             '--camera_policy','fixed','--scene-plan',str(folder/'scene_plan.json'),'--save-scene',str(folder/'scene.blend')]
        result=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=90)
        quality=json.loads((folder/'quality.json').read_text()) if (folder/'quality.json').exists() else {}
        add('furnished_single',result.returncode==0 and quality.get('passed') and (folder/'scene.blend').is_file(),quality=quality,error_tail=(result.stderr+result.stdout)[-2000:] if result.returncode else '')
    manifest=json.loads((ROOT/'assets/props/generated/manifest.json').read_text())
    for asset in manifest['assets']:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        path=ROOT/asset['glb']
        with contextlib.redirect_stdout(io.StringIO()): bpy.ops.import_scene.gltf(filepath=str(path))
        meshes=[o for o in bpy.context.scene.objects if o.type=='MESH'];vertices=[c for o in meshes for c in corners(o)]
        bounds=[max(v[i] for v in vertices)-min(v[i] for v in vertices) for i in range(3)]
        root=bpy.data.objects.get('Prop_'+asset['id']);definition=object_catalog()['objects'][asset['id']]
        anchors_ok=all((obj:=bpy.data.objects.get(asset['anchor_nodes'][name])) is not None and
                       obj.type=='EMPTY' and obj.get('contact_anchor')==name for name in definition['anchors'])
        dimension_error=max(abs(a-b) for a,b in zip(bounds,asset['size']))
        # Window sills intentionally extend 5cm beyond each side.
        tolerance=.101 if asset['id']=='window' else .03
        add('portable_'+asset['id'],root is not None and anchors_ok and dimension_error<=tolerance and hashlib.sha256(path.read_bytes()).hexdigest()==asset['sha256'],
            dimensions=bounds,anchors_retained=anchors_ok,dimension_error_m=dimension_error)
    report['passed']=all(c['passed'] for c in report['checks'])
    report['engine_sources_sha256']={f:hashlib.sha256((ROOT/'scripts'/f).read_bytes()).hexdigest() for f in ('scene_plan.py','scene_objects.py','prop_interaction_scene.py','duel_scene.py','render_white_model.py')}
    atomic_json(ROOT/'reports/props_blender_regression.json',report)
    if not report['passed']: raise RuntimeError('Props regression failed: '+','.join(c['case'] for c in report['checks'] if not c['passed']))


if __name__=='__main__': main()
