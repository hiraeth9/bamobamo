"""Read-only 30/60-frame camera timing on the saved be08 scene; no rendering."""
import sys,json,time,uuid,hashlib,cProfile,pstats
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import bpy
from agent_camera_quality import check_camera_visibility


def main():
    job='be08e914bd8d4726';source=ROOT/'output/jobs'/job/'scene.blend'
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    before=sha(source);module=ROOT/'scripts/agent_camera_quality.py';module_before=sha(module)
    bpy.ops.wm.open_mainfile(filepath=str(source),use_scripts=False)
    scene=bpy.context.scene;actors=json.loads(scene.get('wm_actor_registry','{}'))
    chair=sorted(o.name for o in scene.objects if o.type=='MESH' and o.name.startswith('Chair_'))
    groups={'chair':chair,'door':['Door_Panel']}
    assert chair and 'Door_Panel' in scene.objects
    contract={'version':1,'shots':[{'camera':scene.camera.name,'frame_start':scene.frame_start,
                  'frame_end':scene.frame_end,'actor_ids':list(actors),'focus_objects':[],
                  'focus_groups':groups,'framing':'full_body','margin':.04}]}
    runs=[]
    for count in (30,60):
        start=time.perf_counter();result=check_camera_visibility(job,contract,frame_start=1,frame_end=count)
        elapsed=time.perf_counter()-start
        runs.append({'frames':count,'elapsed_seconds':elapsed,'seconds_per_frame':elapsed/count,
                     'measured_result':result})
        print('BE08_CAMERA_TIMING',count,round(elapsed,4),'sec',round(elapsed/count,5),'sec/frame',flush=True)
    profile=cProfile.Profile();profile.enable()
    check_camera_visibility(job,frame_start=1,frame_end=30)
    profile.disable();stats=pstats.Stats(profile)
    entries=sorted(stats.stats.items(),key=lambda item:item[1][3],reverse=True)[:25]
    profile_summary=[{'function':str(key),'calls':value[1],'total_seconds':value[2],'cumulative_seconds':value[3]} for key,value in entries]
    out=ROOT/'reports/camera_visibility_20261014'/('performance_'+uuid.uuid4().hex[:16]);out.mkdir(parents=True)
    report={'job_id':job,'scene_sha256':before,'source_scene_unchanged':sha(source)==before,
            'module_sha256':module_before,'module_unchanged_during_measurement':sha(module)==module_before,
            'actual_focus_groups':groups,'runs':runs,
            'profile_top':profile_summary,
            'scope':'Read-only saved original be08 scene; actual 30 and 60 evaluated frames. No camera repair, no rendering, no GUI, no scene save; timing is not whole-film acceptance.'}
    p=out/'validation.json';p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('BE08_CAMERA_TIMING_REPORT',str(p),sha(p),flush=True)


if __name__=='__main__':main()
