"""Real Beta fall/hold/get-up checks and keyframes; all files in a new directory.

./blender -b -t 2 --python-exit-code 1 --python tests/test_agent_ground_motion_blender.py -- --render
No production jobs or existing outputs are opened.
"""
import argparse
import math
from pathlib import Path
import subprocess
import sys
import tempfile
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT/'scripts'))


def main():
    import bpy
    from mathutils import Vector
    import duel_plan
    from agent_character import actor_ensure, resolve_actor
    from agent_ground_motion import ground_motion_events, check_ground_motion
    from agent_interactions import guard_events, register_interaction, verify_interaction
    from agent_motion_timeline import motion_commit
    parser = argparse.ArgumentParser()
    parser.add_argument('--output')
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--source', default='KnockOut_Loser_mixamo.fbx')
    parser.add_argument('--yaw', type=float, default=.45)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    output = Path(args.output) if args.output else Path(tempfile.mkdtemp(prefix='bamo_p4_ground_'))
    output.mkdir(parents=True, exist_ok=True)
    assert not (output/'scene.blend').exists(), 'Choose a new output directory'
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.world = bpy.data.worlds.new('GroundWorld')
    scene.world.color = (.3, .3, .3)
    scene.frame_start, scene.frame_end, scene.render.fps = 1, 104, 12
    duel_plan.JOBS = output/'jobs'
    job = 'ground_beta'
    folder = duel_plan.JOBS/job
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'request.json').write_text('{"prompt":"原Beta单人失衡倒地、保持、手脚支撑起身站稳","character_policy":"library_beta"}', encoding='utf-8')
    actor_ensure(job, 'Person', position=(.2, -.1, 0), yaw=args.yaw)
    actor = resolve_actor(job, 'Person')
    register_interaction(job, guard_events(job, 'Person', 'initial_guard', 1, 1))
    motion_commit(job, 'Person', 1, 1)
    originals = set(bpy.data.objects)
    source_window = [7.8, 20.4] if 'Loser' in args.source else [9.8, 13.5]
    plan = ground_motion_events(job, 'Person', 'fall_hold_recover', PROJECT/'rokoko_staging/mixamo'/args.source,
        source_window=source_window, start_frame=1, end_frame=96, settle_frames=8, expect_standing=True,
        supports=[{'chain': 'LeftHand', 'start_frame': 77, 'end_frame': 78}] if 'Loser' in args.source else None)
    assert set(bpy.data.objects) == originals, 'Temporary source/probe objects survived'
    print('GROUND_COMPILED', {key: plan[key] for key in ('source_bones','hips_source','mapped_bones','retarget_scale','support_windows')}, flush=True)
    register_interaction(job, plan)
    motion_commit(job, 'Person', 1, 104)
    checked = check_ground_motion(job, 'Person')
    support = verify_interaction(job)
    print('GROUND_CHECK', {key: value for key, value in checked.items() if key != 'contact_report'}, flush=True)
    print('GROUND_SUPPORT', support['passed'], support['contact_report'].get('failures', [])[:8], flush=True)
    # Require actual floor/correct support checks; never substitute saved-file success.
    assert checked['passed'], checked['failures'][:10]
    assert support['passed'], support['failures'][:10]
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.color_type = 'MATERIAL'
    scene.display.shading.show_cavity = True
    scene.display.shading.show_shadows = True
    scene.render.resolution_x, scene.render.resolution_y = 720, 540
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, -.012))
    material = bpy.data.materials.new('FloorGray')
    material.diffuse_color = (.38, .38, .38, 1)
    bpy.context.object.data.materials.append(material)
    data = bpy.data.cameras.new('GroundWide')
    camera = bpy.data.objects.new('GroundWide', data)
    scene.collection.objects.link(camera)
    camera.location = (3.5, -4.5, 2.5)
    camera.rotation_euler = (Vector((.1, -.2, .75))-camera.location).to_track_quat('-Z', 'Y').to_euler()
    data.type, data.ortho_scale = 'ORTHO', 3.8
    scene.camera = camera
    frames = output/'frames'
    frames.mkdir(exist_ok=True)
    selected = range(1, 105) if args.render else (1, 14, 28, 46, 62, 78, 96, 104)
    for frame in selected:
        scene.frame_set(frame)
        scene.render.filepath = str(frames/('frame_%04d.png'%frame))
        bpy.ops.render.render(write_still=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output/'scene.blend'), check_existing=False)
    # Verify real saved animation in a fresh Blender as well.
    reload = output/'reload.py'
    reload.write_text("import sys\nsys.path.insert(0,"+repr(str(PROJECT/'scripts'))+")\n"
        "import duel_plan\nfrom pathlib import Path\nduel_plan.JOBS=Path("+repr(str(duel_plan.JOBS))+")\n"
        "from agent_ground_motion import check_ground_motion\n"
        "from agent_interactions import verify_interaction\n"
        "from agent_character import resolve_actor\n"
        "from agent_motion_quality import registered_ground_report,_active_channelbags\n"
        "import bpy\n"
        "r=check_ground_motion('ground_beta','Person')\nassert r['passed'],r['failures'][:3]\n"
        "assert check_ground_motion('ground_beta','Person',frame_start=20,frame_end=25)['tested_frames']==6\n"
        "assert check_ground_motion('ground_beta','Person',frame_start=200,frame_end=201)['tested_frames']==0\n"
        "assert verify_interaction('ground_beta')['passed']\n"
        "assert registered_ground_report('ground_beta')['passed']\n"
        "root=resolve_actor('ground_beta','Person')['root']\n"
        "curves=[c for bag in _active_channelbags(root.animation_data) for c in bag.fcurves if c.data_path=='location' and c.array_index==2]\n"
        "assert curves\nsaved=[(p,p.co.copy(),p.handle_left.copy(),p.handle_right.copy()) for c in curves for p in c.keyframe_points]\n"
        "for p,co,left,right in saved:\n p.co.y=co.y-.2\n p.handle_left.y=left.y-.2\n p.handle_right.y=right.y-.2\n"
        "root.update_tag()\nbad=registered_ground_report('ground_beta')\nassert not bad['passed'],bad\n"
        "assert any(a['maximum_skin_penetration_m']>.15 for a in bad['actors'])\n"
        "for p,co,left,right in saved:\n p.co=co\n p.handle_left=left\n p.handle_right=right\n"
        "root.update_tag()\nassert registered_ground_report('ground_beta')['passed']\n"
        "print('GROUND_RELOAD_AND_REAL_PENETRATION_NEGATIVE_PASS',flush=True)\n", encoding='utf-8')
    result = subprocess.run([bpy.app.binary_path, '-b', str(output/'scene.blend'), '-t', '2', '--python-exit-code', '1', '--python', str(reload)],
        capture_output=True, text=True)
    print(result.stdout[-1200:], flush=True)
    assert result.returncode == 0, result.stderr[-2000:]
    if args.render:
        subprocess.run(['ffmpeg','-y','-framerate','12','-i',str(frames/'frame_%04d.png'),'-c:v','libx264','-pix_fmt','yuv420p','-crf','20',str(output/'ground_motion.mp4')], check=True, capture_output=True)
    print('GROUND_PASS', output, flush=True)


if __name__ == '__main__':
    main()
