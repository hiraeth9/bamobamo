"""Reusable FK/IK controls on the existing skinned character, in world metres.

IK is a pose tool, not a motion generator or a balance/physical quality claim.
Targets are matched to the evaluated FK pose before influence is enabled.
"""
from __future__ import annotations
import json
import math

CONTROL_STORE = 'wm_actor_controls_v1'


def _actor(job_id, actor_id):
    from agent_character import resolve_actor
    return resolve_actor(job_id,actor_id)


def read_pose(armature, evaluated=False):
    import bpy
    source=armature.evaluated_get(bpy.context.evaluated_depsgraph_get()) if evaluated else armature
    result={}
    for bone in source.pose.bones:
        if evaluated:
            kwargs={}
            if bone.parent:
                kwargs={'parent_matrix':bone.parent.matrix,
                        'parent_matrix_local':bone.parent.bone.matrix_local}
            # Matrix inversion recovers local channels including constraints;
            # posebone.location alone would omit their visible result.
            basis=bone.bone.convert_local_to_pose(bone.matrix,bone.bone.matrix_local,invert=True,**kwargs)
            location,rotation,scale=basis.decompose()
        else:
            location=bone.location
            rotation=bone.rotation_quaternion if bone.rotation_mode=='QUATERNION' else bone.matrix_basis.to_quaternion()
            scale=bone.scale
        result[bone.name]={'location':list(location),'rotation_quaternion':list(rotation),'scale':list(scale)}
    return result


def set_pose(armature, pose):
    for name,channels in pose.items():
        if name not in armature.pose.bones:
            raise ValueError('pose addresses missing existing robot bone: '+name)
        bone=armature.pose.bones[name]
        bone.rotation_mode='QUATERNION'
        for channel in ('location','rotation_quaternion','scale'):
            if channel in channels:
                setattr(bone,channel,channels[channel])


def _bone(armature, short):
    matches=[bone for bone in armature.pose.bones if bone.name.split(':')[-1]==short]
    if len(matches)!=1:
        raise ValueError('cannot resolve unique robot bone '+short)
    return matches[0]


def _point(armature, short):
    return armature.matrix_world @ _bone(armature,short).head


def _new_target(name, location, collection):
    import bpy
    obj=bpy.data.objects.new(name,None)
    collection.objects.link(obj)
    obj.empty_display_type='SPHERE'; obj.empty_display_size=.035
    obj.hide_render=True; obj.location=location
    obj.rotation_mode='QUATERNION'
    return obj


def _pole_location(armature, start, middle, end):
    from mathutils import Vector
    origin,joint,goal=(_point(armature,n) for n in (start,middle,end))
    axis=goal-origin
    if axis.length<1e-9:
        axis=Vector((0,1,0))
    axis.normalize()
    bend=joint-origin
    bend-=axis*bend.dot(axis)
    if bend.length<.001:
        preferred=Vector((0,0,-1)) if end.endswith('Hand') else Vector((0,-1,0))
        bend=preferred-axis*preferred.dot(axis)
        if bend.length<.001:
            bend=axis.cross(Vector((1,0,0)))
    return origin.lerp(goal,.5)+bend.normalized()*.7


def controls_ensure(job_id,actor_id):
    return _controls_ensure_resolved(job_id,actor_id,_actor(job_id,actor_id))


def _controls_ensure_resolved(job_id,actor_id,actor):
    import bpy
    arm,root=actor['armature'],actor['root']
    store=json.loads(root.get(CONTROL_STORE,'{"version":1,"chains":{}}'))
    bpy.context.view_layer.update()
    for side in ('Left','Right'):
        for start,middle,end in (('Arm','ForeArm','Hand'),('UpLeg','Leg','Foot')):
            key=side+end
            if key in store['chains']:
                chain=store['chains'][key]
                if chain['target_object'] in bpy.data.objects and chain['pole_object'] in bpy.data.objects:
                    continue
                raise ValueError('persistent IK control target is missing: '+key)
            start,middle,end=(side+n for n in (start,middle,end))
            expected=_point(arm,middle)
            goal=_new_target(f'WM_{job_id}_{actor_id}_{key}_Goal',_point(arm,end),actor['collection'])
            goal.rotation_quaternion=(arm.matrix_world @ _bone(arm,end).matrix).to_quaternion()
            pole=_new_target(f'WM_{job_id}_{actor_id}_{key}_Pole',_pole_location(arm,start,middle,end),actor['collection'])
            constraint=_bone(arm,middle).constraints.new('IK')
            constraint.name='WM_'+key+'_IK'
            constraint.target=goal; constraint.pole_target=pole
            constraint.chain_count=2; constraint.use_stretch=False; constraint.iterations=64
            constraint.influence=1
            # Calibrate rig-dependent pole angle against the existing bend plane.
            best=(float('inf'),0)
            for step in range(32):
                angle=-math.pi+step*math.pi/16
                constraint.pole_angle=angle
                bpy.context.view_layer.update()
                error=(_point(arm,middle)-expected).length
                if error<best[0]: best=(error,angle)
            constraint.pole_angle=best[1]
            constraint.influence=0
            for name in (start,middle): _bone(arm,name).ik_stretch=0
            orientation=_bone(arm,end).constraints.new('COPY_ROTATION')
            orientation.name='WM_'+key+'_Orientation'
            orientation.target=goal; orientation.target_space='WORLD'; orientation.owner_space='WORLD'
            orientation.influence=0
            store['chains'][key]={'start_bone':_bone(arm,start).name,'middle_bone':_bone(arm,middle).name,
                                  'end_bone':_bone(arm,end).name,'target_object':goal.name,'pole_object':pole.name,
                                  'ik_constraint':constraint.name,'rotation_constraint':orientation.name,
                                  'pole_angle':best[1],'use_stretch':False,'initial_match_error_m':best[0]}
    root[CONTROL_STORE]=json.dumps(store,ensure_ascii=False,sort_keys=True)
    return {'ok':True,'actor_id':actor_id,'job_id':job_id,**store,'status':'controls_ready_influence_zero'}


def controls_match(job_id,actor_id,chain=None):
    """Match goal+pole to currently evaluated FK before any IK handover."""
    import bpy
    from mathutils import Matrix
    actor=_actor(job_id,actor_id)
    info=_controls_ensure_resolved(job_id,actor_id,actor)
    for key,record in info['chains'].items():
        if chain and key!=chain: continue
        arm=actor['armature']
        ik=_bone(arm,record['middle_bone'].split(':')[-1]).constraints[record['ik_constraint']]
        orientation=_bone(arm,record['end_bone'].split(':')[-1]).constraints[record['rotation_constraint']]
        # Baked controls are muted. Matching explicitly prepares the next FK/IK
        # handover, so enable evaluation while keeping influence at zero.
        ik.mute=False;ik.influence=0
        orientation.mute=False;orientation.influence=0
        bpy.context.view_layer.update()
        goal=bpy.data.objects[record['target_object']]
        goal.matrix_world=arm.matrix_world @ arm.pose.bones[record['end_bone']].matrix
        # Bone world matrix contains FBX .01 scale; target must only carry orientation.
        goal.scale=(1,1,1)
        bpy.data.objects[record['pole_object']].location=_pole_location(arm,*[record[k].split(':')[-1] for k in ('start_bone','middle_bone','end_bone')])
    return info


def mute_controls(actor,muted=True):
    for bone in actor['armature'].pose.bones:
        for constraint in bone.constraints:
            if constraint.name.startswith('WM_'):
                constraint.mute=bool(muted)
                if muted: constraint.influence=0


def apply_targets(job_id,actor_id,targets,frame,weight=1):
    """Targets: {RightHand:{keyframes:[{frame,position:[3],pole:[3],...}]}}.

    positions are world metres; unreachable targets are reported, not stretched.
    A target's influence can be keyed independently; event blending multiplies it.
    """
    actor=_actor(job_id,actor_id)
    info=_controls_ensure_resolved(job_id,actor_id,actor)
    return _apply_targets_resolved(actor,info,targets,frame,weight)


def _apply_targets_resolved(actor,info,targets,frame,weight=1):
    import bpy
    from mathutils import Vector,Quaternion
    measurements=[]
    for key,spec in targets.items():
        if key not in info['chains']:
            raise ValueError('unknown IK chain: '+key)
        keys=sorted(spec.get('keyframes',[]),key=lambda k:k['frame'])
        if not keys:
            keys=[dict(spec,frame=frame)]
        if frame<=keys[0]['frame']: state=keys[0]
        elif frame>=keys[-1]['frame']: state=keys[-1]
        else:
            left,right=next((a,b) for a,b in zip(keys,keys[1:]) if a['frame']<=frame<=b['frame'])
            t=(frame-left['frame'])/max(1,right['frame']-left['frame'])
            if left.get('interpolation','smooth')=='smooth':t=t*t*(3-2*t)
            state=dict(left)
            for field in ('position','pole'):
                if field in left and field in right:
                    state[field]=[a+(b-a)*t for a,b in zip(left[field],right[field])]
            state['influence']=left.get('influence',1)+(right.get('influence',1)-left.get('influence',1))*t
            if 'rotation_quaternion' in left and 'rotation_quaternion' in right:
                state['rotation_quaternion']=list(Quaternion(left['rotation_quaternion']).slerp(Quaternion(right['rotation_quaternion']),t))
        chain=info['chains'][key]
        arm=actor['armature']
        goal=bpy.data.objects[chain['target_object']]
        goal.location=state.get('position',goal.location)
        if 'rotation_quaternion' in state:goal.rotation_quaternion=state['rotation_quaternion']
        if 'pole' in state:bpy.data.objects[chain['pole_object']].location=state['pole']
        else:
            start,middle,end=(chain[k].split(':')[-1] for k in ('start_bone','middle_bone','end_bone'))
            bpy.data.objects[chain['pole_object']].location=_pole_location(arm,start,middle,end)
        ik=arm.pose.bones[chain['middle_bone']].constraints[chain['ik_constraint']]
        ik.mute=False; ik.influence=max(0,min(1,float(state.get('influence',1))*weight))
        orientation=arm.pose.bones[chain['end_bone']].constraints[chain['rotation_constraint']]
        orientation.mute=False
        orientation.influence=ik.influence if 'rotation_quaternion' in state else 0
        start_point=arm.matrix_world @ arm.pose.bones[chain['start_bone']].head
        mid_point=arm.matrix_world @ arm.pose.bones[chain['middle_bone']].head
        end_point=arm.matrix_world @ arm.pose.bones[chain['end_bone']].head
        reach=(mid_point-start_point).length+(end_point-mid_point).length
        measurements.append({'chain':key,'target_distance_m':(goal.location-start_point).length,
                             'reach_m':reach,'reachable':(goal.location-start_point).length<=reach+1e-4,
                             'influence':ik.influence,'use_stretch':False})
    return {'ok':True,'frame':frame,'targets':measurements}


def recipe_pose_adapter(pose):
    """Adapt calibrated Actor/MotionSources pose tuples without importing a scene."""
    return {name:{'location':list(values[0]),'rotation_quaternion':list(values[1]),'scale':list(values[2])}
            for name,values in pose.items()}
