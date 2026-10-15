"""Upper-body FK and non-stretching hand targets for real door pull bars.

The caller owns root motion, a persistent LocomotionGait, constraints and baking.
Call apply() after source pose/root transforms and before gait.feet(); the helper
never replaces the gait clock or its toe anchors. HandMiddle1 is the measured
contact point; a small outward offset keeps knuckle skin on the bar surface.
A near-hinge grip is geometry, not a substituted far-handle target.
"""
import math

import bpy
from mathutils import Vector

from duel_scene import PREFIX, point, rotate_world


class DoorInteractionPose:
    def __init__(self, actor, sources, fps=30):
        self.actor, self.sources, self.fps = actor, sources, fps
        self.lean = 0.0
        self.drop = 0.0
        self.last_side = None
        self.last_foot_rotations = {}
        self.arm_lengths = {}
        for side in ('Left', 'Right'):
            self.arm_lengths[side] = sum(
                (point(actor.arm, side+bone, True)-point(actor.arm, side+bone)).length
                for bone in ('Arm', 'ForeArm'))

    def _aim_bone(self, name, direction, weight=1.0):
        actor=self.actor
        current=point(actor.arm,name,True)-point(actor.arm,name)
        if current.length<1e-6 or direction.length<1e-6:return
        rotation=current.rotation_difference(direction)
        axis,angle=rotation.to_axis_angle()
        rotate_world(actor,name,axis,angle*weight)
        bpy.context.view_layer.update()

    def tuck_arms(self, weight=1.0):
        """Keep both non-contact hands clear during near-door walks/turns.

        The caller supplies a smooth spatial weight and calls this after source
        FK/root update, before apply(). It changes no pelvis or foot targets.
        """
        weight=max(0.,min(1.,float(weight)))
        if weight<=0:return
        for side,sign in (('Left',1),('Right',-1)):
            self._aim_bone(side+'Arm',Vector((0,0,-.30))+self.actor.forward*.045
                           +self.actor.lateral*(sign*.025),weight)
            self._aim_bone(side+'ForeArm',Vector((0,0,-.18))-self.actor.forward*.08,weight)

    def smooth_feet(self, feet, record, dt=None, max_rotation_rate_deg_s=360.):
        """Blend released feet continuously around their identical toe goals.

        During a curved route a planted foot can retain an old world heading.
        At release, interpolate the complete quaternion on its shortest arc;
        source heel/toe rolls can make a projected heading singular. Recompute
        the ankle around the same toe target, preserving gait phase and locks.
        """
        dt=dt or 1/self.fps;corrections={}
        for side in ('Left','Right'):
            key=side+'Foot';rotation=feet['rotations'][key].normalized()
            fk=(self.actor.arm.matrix_world@self.actor.arm.pose.bones[PREFIX+key].matrix).to_quaternion()
            tip=fk.inverted()@(point(self.actor.arm,side+'ToeBase')-point(self.actor.arm,key))
            desired_toe=feet['desired'][key]+rotation@tip
            previous=self.last_foot_rotations.get(side)
            remaining=0.
            if previous is not None:
                if previous.dot(rotation)<0:rotation.negate()
                angle=2*math.acos(max(-1.,min(1.,previous.dot(rotation))))
                limit=math.radians(max_rotation_rate_deg_s)*dt
                if angle>limit:
                    rotation=previous.slerp(rotation,limit/angle)
                    remaining=angle-limit
            feet['rotations'][key]=rotation
            feet['desired'][key]=desired_toe-rotation@tip
            name=self.actor.id+':'+key
            if name in feet.get('stance',{}):feet['stance'][name]=feet['desired'][key].copy()
            if name in record.get('stance',{}):record['stance'][name]=list(feet['desired'][key])
            if name in record.get('swing',{}):record['swing'][name]=list(feet['desired'][key])
            self.last_foot_rotations[side]=rotation.copy()
            corrections[side]=math.degrees(remaining)
        record['door_foot_rotation_remaining_deg']=corrections
        return feet

    def _lean_pose(self, base, axis, angle):
        actor=self.actor
        for name,rotation in base.items():
            actor.arm.pose.bones[PREFIX+name].rotation_quaternion=rotation
        bpy.context.view_layer.update()
        rotate_world(actor,'Spine',axis,angle*.60)
        bpy.context.view_layer.update()
        rotate_world(actor,'Spine1',axis,angle*.40)
        bpy.context.view_layer.update()

    def apply(self, side, contact_world, inward_direction, weight=1.0,
              hip_drop=.10, max_lean=.95, contact_offset=.045, dt=None):
        """Apply upper FK and return world hand IK data, without enabling IK.

        side is left/right; inward_direction points from the grasping side into
        the door. weight eases the same FK/IK contact in and out. Root rotation
        must already be final; an operation should stand with both feet settled.
        Returned desired/rotations/influences/poles dictionaries can be merged
        with gait.feet() and keyed by the caller. reach diagnostics are evidence,
        not proof: verify actual skinned geometry and finger-root after baking.
        """
        actor=self.actor;side=side.title()
        if side not in ('Left','Right'):raise ValueError('door hand side must be left/right')
        weight=max(0.,min(1.,float(weight)));dt=dt or 1/self.fps
        contact=Vector(contact_world);inward=Vector(inward_direction)
        if inward.length<1e-6:raise ValueError('door contact direction cannot be zero')
        inward.normalize()
        wrist0=point(actor.arm,side+'Hand')
        tip0=(point(actor.arm,side+'HandMiddle1')-wrist0).length
        raw_reach=(contact-inward*(tip0+contact_offset)-point(actor.arm,side+'Arm')).length
        drop_weight=max(0.,min(1.,(raw_reach-(self.arm_lengths[side]-.06))/.20))
        target_drop=hip_drop*drop_weight
        self.drop+=(target_drop-self.drop)*(1-math.exp(-dt/.09))
        actor.hip_offset(Vector((0,0,-self.drop*weight)))
        bpy.context.view_layer.update()
        direction=contact-point(actor.arm,'Spine');direction.z=0
        if direction.length<1e-6:direction=actor.forward.copy()
        direction.normalize();axis=Vector((0,0,1)).cross(direction)
        base={name:actor.arm.pose.bones[PREFIX+name].rotation_quaternion.copy()
              for name in ('Spine','Spine1')}
        wrist=point(actor.arm,side+'Hand')
        tip_length=(point(actor.arm,side+'HandMiddle1')-wrist).length
        wrist_target=contact-inward*(tip_length+contact_offset)
        # Preserve three cm of elbow bend for the torso controller's transient
        # lag while the bar moves, including the reverse opening direction.
        desired_reach=self.arm_lengths[side]-.030
        # Solve the minimal trunk lean on the current real skeleton. Crouch and
        # hand-side shoulder offsets matter more than a nominal root distance.
        best_angle=0.;best_distance=float('inf')
        for index in range(20):
            angle=max_lean*index/19
            self._lean_pose(base,axis,angle*weight)
            distance=(wrist_target-point(actor.arm,side+'Arm')).length
            if distance<best_distance:best_distance=distance;best_angle=angle
            if distance<=desired_reach:best_angle=angle;break
        # A continuous controller avoids a discrete grid-angle elbow/torso pop.
        if self.last_side is None:self.lean=best_angle
        else:self.lean+=(best_angle-self.lean)*(1-math.exp(-dt/.09))
        self.last_side=side
        self._lean_pose(base,axis,self.lean*weight)
        # Both neutral arms are tucked before the active IK is applied. This
        # prevents Idle's wide non-contact hand from entering leaf or jamb.
        for arm_side,sign in (('Left',1),('Right',-1)):
            self._aim_bone(arm_side+'Arm',
                Vector((0,0,-.30))+actor.forward*.045+actor.lateral*(sign*.025),weight)
            self._aim_bone(arm_side+'ForeArm',
                Vector((0,0,-.18))+actor.forward*(-.08),weight)
        if 'grip' in self.sources.definitions:
            grip=self.sources.sample('grip',8,side=='Right')
            for name in grip:
                if side+'Hand' in name and any(f in name for f in ('Thumb','Index','Middle','Ring','Pinky')):
                    bone=actor.arm.pose.bones[name]
                    bone.rotation_quaternion=bone.rotation_quaternion.slerp(grip[name][1],weight*.75)
        bpy.context.view_layer.update()
        key=side+'Hand';wrist=point(actor.arm,key)
        tip=point(actor.arm,side+'HandMiddle1')-wrist
        natural=(actor.arm.matrix_world@actor.arm.pose.bones[PREFIX+key].matrix).to_quaternion()
        rotation=tip.rotation_difference(inward)@natural
        wrist_target=contact-inward*(tip.length+contact_offset)
        shoulder=point(actor.arm,side+'Arm');axis_to_goal=wrist_target-shoulder
        if axis_to_goal.length<1e-6:axis_to_goal=actor.forward.copy()
        axis_to_goal.normalize()
        bend=Vector((0,0,-1))+actor.lateral*(.10 if side=='Left' else -.10)
        bend-=axis_to_goal*bend.dot(axis_to_goal)
        if bend.length<.02:bend=actor.lateral.copy()
        pole=shoulder.lerp(wrist_target,.5)+bend.normalized()*.60
        reach_distance=(wrist_target-shoulder).length
        return {'desired':{key:wrist_target},'rotations':{key:rotation},
                'influences':{key:weight},'poles':{key:pole},
                'hand_name':key,'contact_tip':side+'HandMiddle1',
                'target':contact.copy(),'inward_direction':inward.copy(),
                'lean_rad':self.lean*weight,'hip_drop_m':self.drop*weight,
                'contact_offset_m':contact_offset,
                'arm_length_m':self.arm_lengths[side],
                'shoulder_wrist_distance_m':reach_distance,
                'reachable':reach_distance<=self.arm_lengths[side]+.001}
