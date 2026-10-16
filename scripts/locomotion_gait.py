"""Distance-driven, contact-aware Walk/Run gait for the existing Beta actor.

The controller keeps one gait clock across actions, aligns corresponding source
footfalls, and locks world toe contacts rather than classifying stance from a
world FK speed after a turn. It never changes Actor or the original clips.
"""
import math

import bpy
from mathutils import Quaternion, Vector

from duel_scene import PREFIX, blend_pose, point, smooth

RUN_PHASE_OFFSET = 19 / 22 - 8 / 30


def clamp(value, lower=0.0, upper=1.0):
    return max(lower, min(upper, value))


def damp(current, target, dt, time_constant):
    return current + (target-current) * (1-math.exp(-dt/max(.001,time_constant)))


def standing_weight(zero_age, *, landing_seconds=.28, settle_seconds=.30):
    """Only settle a standing pose after the last swing foot has landed."""
    return smooth((zero_age-landing_seconds)/settle_seconds)


class LocomotionGait:
    """Call pose(), apply FK/root transforms, update, then call feet().

    feet() returns desired ankle points, world foot rotations, IK influences and
    anchored stance goals. The caller disables previous constraints before pose
    sampling and bakes all returned goals/rotations with a non-stretching IK rig.
    delta_turn_distance is abs(delta_yaw) * .25 for an in-place stepping turn.
    """
    def __init__(self, actor, sources, fps=30):
        self.actor, self.sources, self.fps = actor, sources, fps
        self.phase = .30  # Idle has the left foot forward; begin on that support.
        self.run_weight = 0.0
        self.motion_weight = 0.0
        self.stride_scale = .20
        self.elapsed = 0.0
        self.active = False
        self.effective_speed = 0.0
        self.dt = 1 / fps
        self.stop_age = 0.0
        self.resume_age = 0.0
        self.stop_feet = None
        self.anchors = {}
        self.anchor_yaws = {}
        self.last_toes = {}
        self.last_rotations = {}
        self.last_contact_weights = {}
        self.zero_age = .58
        self.standing_amount = 0.0
        self.last_pose = None
        self.resume_pose = None
        self.pose_resume_age = .22
        self.was_moving = False
        self.has_moved = False
        self.standing_reference = None
        self.standing_pose = None
        self.standing_ankles = {}
        self.standing_rotations = {}
        self.standing_toe_z = {}
        self.stop_standing_pose = None
        self.periods = {name:sources.ends[name]-1 for name in ('guard','walk','run')}
        self.distances = {name:(sources.root_motion[name][-1]-sources.root_motion[name][0]).xy.length
                          for name in ('walk','run')}
        self.native_speeds = {name:self.distances[name] / (self.periods[name] / fps)
                             for name in ('walk','run')}
        self.hip_drift = {name:sources.poses[name][-1][PREFIX+'Hips'][0]
                         - sources.poses[name][0][PREFIX+'Hips'][0]
                          for name in ('walk','run')}
        self.foot_indices = {}
        for side in ('Left','Right'):
            selected=[]
            for mesh in actor.meshes:
                groups={g.index for g in mesh.vertex_groups
                        if g.name.removeprefix(PREFIX).startswith(side)
                        and any(part in g.name for part in ('Foot','Toe'))}
                selected.append([v.index for v in mesh.data.vertices
                                 if any(g.group in groups and g.weight >= .3 for g in v.groups)])
            self.foot_indices[side]=selected
        # The neutral source foot's toe/sole offset calibrates the floor contact
        # in the actual skinned rig. The six-mm margin avoids surface coincidence.
        pose_backup={bone.name:(bone.location.copy(),bone.rotation_quaternion.copy(),bone.scale.copy())
                     for bone in actor.arm.pose.bones}
        actor.set_pose(sources.sample('guard',1));bpy.context.view_layer.update()
        self.flat_toe_z={}
        self.flat_rotations={}
        root_yaw=actor.root.rotation_euler.z
        for side in ('Left','Right'):
            toe=point(actor.arm,side+'ToeBase')
            self.flat_toe_z[side]=toe.z-self._skin_min(side)+.006
            self.flat_rotations[side]=Quaternion((0,0,1),-root_yaw) @ self._rotation(side)
            self.last_toes[side]=toe.copy()
            self.last_rotations[side]=self._rotation(side)
            self.anchors[side]=toe.copy();self.anchor_yaws[side]=root_yaw
        if 'standing' in sources.ends:
            self._calibrate_standing(root_yaw)
        actor.set_pose(pose_backup);bpy.context.view_layer.update()

    def _calibrate_standing(self, root_yaw):
        """A relaxed source torso plus target-sized, parallel flat support.

        This is a standing-support correction, not an untouched playback of
        the source: its Beta FK feet are staggered and require leg IK below.
        """
        actor=self.actor;arm=actor.arm
        reference_time=1.2
        actor.set_pose(self.sources.sample('standing',1+reference_time*self.fps))
        bpy.context.view_layer.update()
        hips=arm.pose.bones[PREFIX+'Hips']
        across=point(arm,'LeftUpLeg')-point(arm,'RightUpLeg')
        yaw=math.atan2(across.y,across.x)-root_yaw
        world=arm.matrix_world@hips.matrix
        pivot=world.translation.copy()
        from mathutils import Matrix
        world=Matrix.Translation(pivot)@Quaternion((0,0,1),-yaw).to_matrix().to_4x4()@Matrix.Translation(-pivot)@world
        hips.matrix=arm.matrix_world.inverted()@world
        bpy.context.view_layer.update()
        # Keep the measured flat source sole orientation, but use the relaxed
        # reference's fixed toe articulation, so a looping toe never curls into
        # the floor after the ankle has already stopped.
        for side in ('Left','Right'):
            foot=arm.pose.bones[PREFIX+side+'Foot']
            matrix=arm.matrix_world@foot.matrix
            flat=Quaternion((0,0,1),root_yaw)@self.flat_rotations[side]
            ankle=point(arm,side+'Foot')
            local_tip=matrix.to_quaternion().inverted()@(point(arm,side+'ToeBase')-ankle)
            direction=flat@local_tip
            heading=math.atan2(direction.x,-direction.y)
            flat=Quaternion((0,0,1),root_yaw-heading)@flat
            self.standing_rotations[side]=Quaternion((0,0,1),-root_yaw)@flat
            matrix=flat.to_matrix().to_4x4();matrix.translation=ankle
            # matrix_world includes the FBX's centimetre scale.
            matrix=matrix@Matrix.Diagonal((*arm.matrix_world.to_scale(),1))
            foot.matrix=arm.matrix_world.inverted()@matrix
        bpy.context.view_layer.update()
        hip=point(arm,'Hips');root=actor.root.location
        heights=[]
        self.standing_hip=Quaternion((0,0,1),-root_yaw)@(hip-root)
        self.standing_top_offsets={}
        self.leg_lengths={}
        self.foot_tips={}
        for side in ('Left','Right'):
            top=point(arm,side+'UpLeg');knee=point(arm,side+'Leg');ankle=point(arm,side+'Foot')
            upper=(top-knee).length;lower=(knee-ankle).length
            self.leg_lengths[side]=(upper,lower)
            self.standing_top_offsets[side]=Quaternion((0,0,1),-root_yaw)@(top-hip)
            self.foot_tips[side]=self._rotation(side).inverted()@(point(arm,side+'ToeBase')-ankle)
            height=ankle.z-self._skin_min(side)+.006
            lateral=(top-hip).dot(actor.lateral)
            goal=root+actor.lateral*lateral
            goal.z=height
            reach_sq=upper*upper+lower*lower-2*upper*lower*math.cos(math.radians(170))
            horizontal=(top-goal).xy.length
            heights.append(height+math.sqrt(max(0,reach_sq-horizontal*horizontal))-(top.z-hip.z))
            self.standing_ankles[side]=Quaternion((0,0,1),-root_yaw)@(goal-root)
            self.standing_toe_z[side]=height+(self.standing_rotations[side]@self.foot_tips[side]).z
        world=arm.matrix_world@hips.matrix;world.translation.z+=min(heights)-hip.z
        hips.matrix=arm.matrix_world.inverted()@world
        bpy.context.view_layer.update()
        self.standing_pose={bone.name:(bone.location.copy(),bone.rotation_quaternion.copy(),bone.scale.copy())
                            for bone in arm.pose.bones}
        self.standing_hip.z=min(heights)-root.z
        self.root_to_hip=(hips.bone.matrix_local.to_3x3().inverted()@
                          arm.matrix_world.to_3x3().inverted()@
                          Quaternion((0,0,1),root_yaw).to_matrix())
        self.standing_reference={'motion_id':'quaternius.idle_loop',
            'source_window_seconds':[0.,self.sources.metadata['standing']['duration_s']],
            'calibration_time_seconds':reference_time,'corrections':['pelvis_heading','target_leg_length_height',
            'initial_parallel_under_hip_support','stop_preserves_world_toe_anchors',
            'fixed_lower_body_and_toes'], 'raw_source_playback':False}

    def _stop_rotation(self, side, root_yaw):
        flat=Quaternion((0,0,1),root_yaw)@self.flat_rotations[side]
        source_direction=flat@self.foot_tips[side]
        actual_direction=self.stop_feet[side]['rotation']@self.foot_tips[side]
        delta=math.atan2(actual_direction.x,-actual_direction.y)-math.atan2(source_direction.x,-source_direction.y)
        return Quaternion((0,0,1),delta)@flat

    def _prepare_stop_standing(self):
        """Fit pelvis height over the actual support, without shuffling feet."""
        yaw=self.actor.root.rotation_euler.z;unrotate=Quaternion((0,0,1),-yaw)
        ankles={}
        for side in ('Left','Right'):
            toe=self.stop_feet[side]['toe'].copy();toe.z=self.standing_toe_z[side]
            ankle=toe-self._stop_rotation(side,yaw)@self.foot_tips[side]
            ankles[side]=unrotate@(ankle-self.actor.root.location)
        middle=(ankles['Left']+ankles['Right'])*.5
        neutral_middle=(self.standing_ankles['Left']+self.standing_ankles['Right'])*.5
        hip=self.standing_hip.copy();hip.x+=middle.x-neutral_middle.x;hip.y+=middle.y-neutral_middle.y
        heights=[]
        for side in ('Left','Right'):
            upper,lower=self.leg_lengths[side]
            top=hip+self.standing_top_offsets[side]
            horizontal=(top-ankles[side]).xy.length
            reach_sq=upper*upper+lower*lower-2*upper*lower*math.cos(math.radians(170))
            heights.append(ankles[side].z+math.sqrt(max(0,reach_sq-horizontal*horizontal))-self.standing_top_offsets[side].z)
        hip.z=min(heights)
        self.stop_standing_pose=dict(self.standing_pose)
        loc,rot,scale=self.standing_pose[PREFIX+'Hips']
        self.stop_standing_pose[PREFIX+'Hips']=(loc+self.root_to_hip@(hip-self.standing_hip),rot,scale)

    def remember_pose(self, pose):
        """Remember actual post-IK channels for a subsequent restart blend."""
        self.last_pose={name:tuple(value.copy() for value in channels) for name,channels in pose.items()}

    def _standing(self):
        pose=self.sources.sample('standing',1+(self.elapsed*self.fps)% (self.sources.ends['standing']-1))
        for name,channels in (self.stop_standing_pose or self.standing_pose).items():
            if name.removeprefix(PREFIX).startswith(('Hips','LeftUpLeg','RightUpLeg','LeftLeg','RightLeg',
                                                   'LeftFoot','RightFoot','LeftToe','RightToe')):
                pose[name]=channels
        return pose

    def _rotation(self, side):
        return (self.actor.arm.matrix_world @ self.actor.arm.pose.bones[PREFIX+side+'Foot'].matrix).to_quaternion()

    def _skin_min(self, side):
        depsgraph=bpy.context.evaluated_depsgraph_get()
        minimum=float('inf')
        for mesh,indices in zip(self.actor.meshes,self.foot_indices[side]):
            evaluated=mesh.evaluated_get(depsgraph)
            for index in indices:
                minimum=min(minimum,(evaluated.matrix_world @ evaluated.data.vertices[index].co).z)
        return minimum

    def _sample_cycle(self, name, phase):
        phase %= 1
        pose=self.sources.sample(name,1+phase*self.periods[name])
        loc,rot,scale=pose[PREFIX+'Hips']
        pose[PREFIX+'Hips']=(loc-self.hip_drift[name]*phase,rot,scale)
        return pose

    def pose(self, speed, gait, delta_distance, delta_turn_distance=0.0, dt=None):
        """Return a cyclic pose without restarting phase at a planner boundary."""
        self.dt=dt or 1/self.fps;self.elapsed+=self.dt
        turn_speed=max(0,delta_turn_distance)/self.dt
        self.effective_speed=max(abs(speed),turn_speed)
        was_active=self.active
        self.active=self.effective_speed>.025
        moving=self.effective_speed>=1e-6
        if moving and not self.was_moving:
            self.resume_pose=self.last_pose
            self.pose_resume_age=0.0
            self.stop_standing_pose=None
            if self.last_pose is not None:
                self.stop_feet={side:{'toe':self.last_toes[side].copy(),
                                      'rotation':self.last_rotations[side].copy()}
                                for side in ('Left','Right')}
        elif moving:
            self.pose_resume_age+=self.dt
        self.was_moving=moving
        self.has_moved=self.has_moved or moving
        self.zero_age=self.zero_age+self.dt if not moving else 0.0
        self.standing_amount=standing_weight(self.zero_age) if self.standing_pose is not None else 0.0
        if self.standing_pose is not None and not moving and self.has_moved and self.stop_feet is not None and self.stop_standing_pose is None:
            self._prepare_stop_standing()
        if self.active:
            if not was_active and self.stop_feet is not None:
                # The initial braking snapshot can contain a raised/running
                # foot. After settling, resume from the current planted pose,
                # never from that old pre-landing rotation or airborne toe.
                self.stop_feet={side:{'toe':self.last_toes[side].copy(),
                                      'rotation':self.last_rotations[side].copy()}
                                for side in ('Left','Right')}
            self.resume_age=self.resume_age+self.dt if was_active else 0.0
            self.stop_age=0.0
        else:
            self.stop_age=self.stop_age+self.dt if not was_active else 0.0
            self.resume_age=0.0
        mode=str(gait).lower()
        target_run=1.0 if mode=='run' else (smooth((self.effective_speed-1.7)/1.0) if mode=='auto' else 0.0)
        self.run_weight=damp(self.run_weight,target_run,self.dt,.12)
        self.motion_weight=damp(self.motion_weight,clamp(self.effective_speed/.10),self.dt,.12 if self.active else .22)
        native_speed=self.native_speeds['walk']*(1-self.run_weight)+self.native_speeds['run']*self.run_weight
        target_stride=clamp(self.effective_speed/max(.01,native_speed),.10,1.25)
        # A turn needs short alternating steps. Actual translation dominates the
        # clock whenever the actor is already walking along a curved path.
        if turn_speed>abs(speed)*1.2:
            target_stride=min(.30,target_stride)
        self.stride_scale=damp(self.stride_scale,target_stride,self.dt,.09)
        cycle_distance=self.distances['walk']*(1-self.run_weight)+self.distances['run']*self.run_weight
        advance=max(0,delta_distance,delta_turn_distance)/(cycle_distance*max(.10,self.stride_scale))
        self.phase+=advance
        walk=self._sample_cycle('walk',self.phase)
        run=self._sample_cycle('run',self.phase+RUN_PHASE_OFFSET)
        locomotion=blend_pose(walk,run,self.run_weight)
        idle=self.sources.sample('guard',1+(self.elapsed*self.fps)%self.periods['guard'])
        # Short strides are produced by foot goals, retaining the source knee
        # lift. Scale the arm/torso effort, rather than flattening both legs into
        # an Idle pose and translating the body across planted feet.
        effort=clamp(self.stride_scale/.65,.25,1)
        for name in locomotion:
            short=name.removeprefix(PREFIX)
            if short.startswith(('Spine','Neck','Head','LeftShoulder','RightShoulder',
                                 'LeftArm','RightArm','LeftForeArm','RightForeArm','LeftHand','RightHand')):
                locomotion[name]=blend_pose({name:idle[name]},{name:locomotion[name]},effort)[name]
        pose=blend_pose(idle,locomotion,self.motion_weight)
        if self.standing_pose is not None and self.standing_amount>0:
            pose=blend_pose(pose,self._standing(),self.standing_amount)
        if moving and self.resume_pose is not None and self.pose_resume_age<.22:
            pose=blend_pose(self.resume_pose,pose,smooth(self.pose_resume_age/.22))
        return pose

    def _contact_weight(self, side):
        phase=self.phase%1
        beginning=8/30 if side=='Left' else 23/30
        relative=(phase-beginning)%1
        # Walk's long double-support interval contracts to Run's flight phase.
        walk_length=.50
        run_length=(3/22+1-RUN_PHASE_OFFSET-beginning) if side=='Left' else (15/22-RUN_PHASE_OFFSET-beginning+1)
        length=walk_length*(1-self.run_weight)+run_length*self.run_weight
        if relative>=length:return 0.0
        ramp=.045
        return smooth(relative/ramp)*smooth((length-relative)/ramp)

    def feet(self, record):
        actor=self.actor;root=actor.root.location;root_yaw=actor.root.rotation_euler.z
        output={'desired':{},'rotations':{},'influences':{},'stance':{}}
        record.setdefault('stance',{});record.setdefault('swing',{});record.setdefault('toe_stance',{})
        record.update({'gait_phase':round(self.phase%1,6),'gait_cycle':math.floor(self.phase),
                       'run_weight':round(self.run_weight,6),'stride_scale':round(self.stride_scale,6),
                       'motion_weight':round(self.motion_weight,6),
                       'standing_weight':round(self.standing_amount,6)})
        if not self.active and self.stop_feet is None:
            self.stop_feet={side:{'toe':self.last_toes[side].copy(),'rotation':self.last_rotations[side].copy()}
                            for side in ('Left','Right')}
        restart=self.stop_feet if self.active else None
        for side in ('Left','Right'):
            key=side+'Foot';ankle=point(actor.arm,key);toe=point(actor.arm,side+'ToeBase')
            rotation=self._rotation(side)
            local_tip=rotation.inverted()@(toe-ankle)
            skin_min=self._skin_min(side)
            natural_ankle=ankle.copy()
            displacement=ankle-root
            natural_ankle+=actor.forward*(displacement.dot(actor.forward)*(self.stride_scale-1))
            natural_ankle.z+=max(0,-.003-skin_min)
            natural_toe=natural_ankle+rotation@local_tip
            weight=self._contact_weight(side) if self.active else 1.0
            previous_weight=self.last_contact_weights.get(side,0.0)
            if self.active:
                if weight>0 and previous_weight<=0:
                    self.anchors[side]=natural_toe.copy();self.anchors[side].z=self.flat_toe_z[side]
                    self.anchor_yaws[side]=root_yaw
                if weight>0:
                    fixed_rotation=Quaternion((0,0,1),self.anchor_yaws[side]-root_yaw)@rotation
                    rotation=rotation.slerp(fixed_rotation,weight)
                    target=self.anchors[side].copy()
                    # Heel contact precedes toe contact. Preserve the source
                    # heel/toe roll until the ball of the foot actually rests.
                    target.z=max(target.z,toe.z-skin_min)
                    desired_toe=natural_toe.lerp(target,weight)
                else:desired_toe=natural_toe
                if restart:
                    amount=smooth(self.resume_age/.22)
                    desired_toe=restart[side]['toe'].lerp(desired_toe,amount)
                    rotation=restart[side]['rotation'].slerp(rotation,amount)
                    if amount<1:weight=0.0
            else:
                state=self.stop_feet[side]
                # The first eased path delta may be below the gait's contact
                # threshold. Hold the actual standing orientation until the
                # stepping resume blend starts, rather than snapping it back
                # to the old source-Idle foot on that first moving interval.
                amount=0.0 if self.was_moving else smooth(self.stop_age/.28)
                flat_rotation=(self._stop_rotation(side,root_yaw) if self.standing_pose is not None and self.has_moved
                               else Quaternion((0,0,1),root_yaw)@self.flat_rotations[side])
                rotation=state['rotation'].slerp(flat_rotation,amount)
                desired_toe=state['toe'].copy()
                floor_z=self.standing_toe_z[side] if self.standing_pose is not None else self.flat_toe_z[side]
                desired_toe.z+=(floor_z-desired_toe.z)*amount
                foot_settle=1.0 if self.standing_pose is not None and not self.has_moved else 0.0
                if foot_settle>0:
                    standing_rotation=Quaternion((0,0,1),root_yaw)@self.standing_rotations[side]
                    rotation=state['rotation'].slerp(standing_rotation,foot_settle)
                    neutral=root+Quaternion((0,0,1),root_yaw)@self.standing_ankles[side]+standing_rotation@local_tip
                    desired_toe=state['toe'].lerp(neutral,foot_settle)
                self.anchors[side]=desired_toe.copy();self.anchor_yaws[side]=root_yaw
                if amount<1:weight=0.0
            desired_ankle=desired_toe-rotation@local_tip
            output['desired'][key]=desired_ankle
            output['rotations'][key]=rotation
            output['influences'][key]=1.0
            name=actor.id+':'+key
            locked=(weight>=.999 and abs(desired_toe.z-self.anchors[side].z)<.0005)
            if weight>=.999:
                output['stance'][name]=desired_ankle.copy();record['stance'][name]=list(desired_ankle)
            else:record['swing'][name]=list(desired_ankle)
            if locked:record['toe_stance'][actor.id+':'+side+'ToeBase']=list(desired_toe)
            record.setdefault('foot_contact_weights',{})[side]=round(weight,6)
            self.last_toes[side]=desired_toe.copy();self.last_rotations[side]=rotation.copy()
            self.last_contact_weights[side]=weight
        if self.active and self.resume_age>=.22:self.stop_feet=None
        return output
