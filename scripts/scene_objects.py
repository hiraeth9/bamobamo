"""Parametric clay props with independent hinges, contact anchors and solids."""
import math
import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view


def material(name, color, alpha=1):
    mat=bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes=True
    mat.diffuse_color=(*color,alpha)
    bsdf=mat.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value=(*color,alpha)
    bsdf.inputs['Roughness'].default_value=.65
    bsdf.inputs['Alpha'].default_value=alpha
    if alpha<1:
        mat.surface_render_method='DITHERED'
    return mat


def corners(obj):
    return [obj.matrix_world@Vector(v) for v in obj.bound_box]


class SceneObjects:
    def __init__(self, plan):
        self.plan=plan
        self.collection=bpy.data.collections.new('Scene_Props')
        bpy.context.scene.collection.children.link(self.collection)
        self.objects={}; self.solids=[]; self.wall_meshes=[]
        self.clay=material('Prop_Clay',(.68,.69,.71))
        self.frame_mat=material('Prop_Frames',(.48,.49,.51))
        self.handle_mat=material('Prop_Handles',(.3,.32,.35))
        self.glass=material('Prop_Glass',(.63,.75,.82),.18)
        self.wall_mat=material('Prop_Wall',(.64,.65,.67))
        for specification in plan['objects']:
            self.create(specification)
        if plan['room']['enabled']:
            self.room(plan['room']['size'])
        bpy.context.view_layer.update()

    def empty(self,name,parent=None,location=(0,0,0)):
        obj=bpy.data.objects.new(name,None)
        self.collection.objects.link(obj)
        obj.parent=parent; obj.location=location
        return obj

    def box(self,name,size,position,parent,mat=None,solid=True):
        x,y,z=[v/2 for v in size]
        mesh=bpy.data.meshes.new(name)
        mesh.from_pydata([(-x,-y,-z),(-x,-y,z),(-x,y,-z),(-x,y,z),(x,-y,-z),(x,-y,z),(x,y,-z),(x,y,z)],[],
                         [(0,4,6,2),(1,3,7,5),(0,1,5,4),(2,6,7,3),(0,2,3,1),(4,5,7,6)])
        mesh.update()
        obj=bpy.data.objects.new(name,mesh); self.collection.objects.link(obj)
        obj.parent=parent; obj.location=position
        obj.data.materials.append(mat or self.clay)
        bevel=obj.modifiers.new('Soft_Edges','BEVEL'); bevel.width=min(.012,min(size)/6); bevel.segments=2
        obj['prop_solid']=solid
        if solid: self.solids.append(obj)
        return obj

    def create(self,spec):
        ident=spec['id']; kind=spec['type']; w,d,h=spec['size']
        root=self.empty('Prop_'+ident,location=spec['position'])
        root.rotation_euler.z=math.radians(spec['yaw_deg'])
        root['prop_id']=ident; root['prop_type']=kind
        entry={'spec':spec,'root':root,'meshes':[],'anchors':{}}
        self.objects[ident]=entry
        def part(label,size,pos,parent=None,mat=None,solid=True):
            obj=self.box(ident+'_'+label,size,pos,parent or root,mat,solid)
            obj['prop_id']=ident; entry['meshes'].append(obj); return obj
        def anchor(label,pos,parent=None):
            # Contact nodes must not collide with visible parts such as handle.
            suffix='' if label=='hinge' else '_anchor'
            obj=self.empty(ident+'_'+label+suffix,parent or root,pos)
            obj['contact_anchor']=label;obj['prop_id']=ident
            entry['anchors'][label]=obj; return obj
        if kind=='table':
            part('top',(w,d,.055),(0,0,h-.0275))
            for i,(x,y) in enumerate(((-1,-1),(-1,1),(1,-1),(1,1))):
                part('leg_'+str(i),(.075,.075,h-.055),(x*(w/2-.07),y*(d/2-.07),(h-.055)/2))
            anchor('touch',(-w*.2,-d/2+.07,h+.018))
        elif kind=='chair':
            seat=h*.52
            part('seat',(w,d,.05),(0,0,seat))
            part('back',(w,.055,h-seat),(0,d/2-.03,seat+(h-seat)/2))
            for i,(x,y) in enumerate(((-1,-1),(-1,1),(1,-1),(1,1))):
                part('leg_'+str(i),(.045,.045,seat-.025),(x*(w/2-.035),y*(d/2-.035),(seat-.025)/2))
            anchor('seat',(0,0,seat+.03))
        elif kind in ('window','door'):
            thickness=.055
            for side in (-1,1):
                part('frame_'+str(side),(thickness,d,h),(side*(w/2-thickness/2),0,h/2),mat=self.frame_mat)
            part('frame_top',(w,d,thickness),(0,0,h-thickness/2),mat=self.frame_mat)
            if kind=='window': part('sill',(w+.1,d+.05,thickness),(0,0,thickness/2),mat=self.frame_mat)
            if kind=='window':
                # Traversal anchors belong to the fixed frame, never the sash.
                span=min(.2,(w-2*thickness)*.23)
                anchor('sill_left',(-span,-d/2-.015,thickness+.015))
                anchor('sill_right',(span,-d/2-.015,thickness+.015))
                anchor('entry',(0,-.85,0))
                anchor('exit',(0,1.05,0))
            elif kind=='door':
                # Passage targets stay on the fixed frame; opening the leaf
                # must not drag the character's destination around its hinge.
                anchor('entry',(0,-1.25,0))
                anchor('exit',(0,1.25,0))
            leaf_w=w-2*thickness; leaf_h=h-thickness-(thickness if kind=='window' else .02)
            hinge=anchor('hinge',(-w/2+thickness,0,thickness if kind=='window' else .02))
            entry['hinge']=hinge
            if kind=='window':
                for x in (.015,leaf_w-.015): part('sash_vertical_'+str(x),(.03,.04,leaf_h),(x,0,leaf_h/2),hinge)
                for z in (.015,leaf_h-.015): part('sash_horizontal_'+str(z),(leaf_w,.04,.03),(leaf_w/2,0,z),hinge)
                part('pane',(leaf_w-.06,.008,leaf_h-.06),(leaf_w/2,0,leaf_h/2),hinge,self.glass)
                handle_position=(leaf_w-.07,-.062,leaf_h*.5)
            else:
                part('leaf',(leaf_w,.045,leaf_h),(leaf_w/2,0,leaf_h/2),hinge)
                handle_position=(leaf_w-.085,-.06,h*.46)
            part('handle',(.027,.035,.13),handle_position,hinge,self.handle_mat)
            anchor('handle',handle_position,hinge)
            if kind=='door':
                # Keep the existing front-side handle unchanged for old jobs.
                # A real back-side grip is needed after passing through.
                back_handle=(handle_position[0],-handle_position[1],handle_position[2])
                part('handle_back',(.027,.035,.13),back_handle,hinge,self.handle_mat)
                anchor('handle_back',back_handle,hinge)
                # A real two-sided pull bar provides a reachable grip near the
                # hinge during passage chains. The old far handles stay intact.
                bar_z=min(1.03,leaf_h-.18)
                bar_start,bar_end=.18,leaf_w-.18
                for label,sign in (('front',-1),('back',1)):
                    part('grip_bar_'+label,(bar_end-bar_start,.025,.03),
                         (leaf_w/2,sign*.06,bar_z),hinge,self.handle_mat)
                    for index,x in enumerate((bar_start+.02,bar_end-.02)):
                        part('grip_bar_'+label+'_support_'+str(index),(.03,.04,.03),
                             (x,sign*.04,bar_z),hinge,self.handle_mat)
                    anchor('contact_'+label,(min(.30,bar_end-.08),sign*.0725,bar_z),hinge)
            hinge.rotation_euler.z=-math.radians(spec['open_angle_deg']) if spec['state']=='open' else 0
        else:
            part('body',(w,d,h-.025),(0,0,(h-.025)/2))
            part('lid',(w+.015,d+.015,.025),(0,0,h-.0125),mat=self.frame_mat)
            anchor('top',(0,0,h+.01))

    def room(self,size):
        w,d,h=size; root=self.empty('Room_Cutaway')
        self.box('Room_Floor',(w,d,.02),(0,0,-.01),root,self.wall_mat,False)
        holes=[(o['position'][0]-o['size'][0]/2,o['position'][0]+o['size'][0]/2,
                o['position'][2],o['position'][2]+o['size'][2]) for o in self.plan['objects'] if o['type'] in ('door','window')]
        xs=sorted({-w/2,w/2,*[x for hole in holes for x in hole[:2]]})
        zs=sorted({0,h,*[z for hole in holes for z in hole[2:]]})
        for i,(left,right) in enumerate(zip(xs,xs[1:])):
            for j,(bottom,top) in enumerate(zip(zs,zs[1:])):
                x,z=(left+right)/2,(bottom+top)/2
                if not any(a<x<b and c<z<e for a,b,c,e in holes):
                    self.wall_meshes.append(self.box(f'Room_Back_{i}_{j}',(right-left,.14,top-bottom),(x,d/2,z),root,self.wall_mat))

    def focus_meshes(self):
        return [m for e in self.objects.values() if e['spec']['focus'] for m in e['meshes']]

    def anchor(self,ident,label):
        bpy.context.view_layer.update()
        return self.objects[ident]['anchors'][label].matrix_world.translation.copy()

    def normal(self,ident):
        entry=self.objects[ident]
        basis=entry.get('hinge',entry['root']).matrix_world.to_3x3()
        return (basis@Vector((0,-1,0))).normalized()

    def frame_normal(self,ident):
        """The inside-facing normal remains fixed when a door/window opens."""
        basis=self.objects[ident]['root'].matrix_world.to_3x3()
        return (basis@Vector((0,-1,0))).normalized()

    def traversal_cutaway(self,ident):
        """Reveal the landing, while retaining the real wall collision solids.

        The lower/upper wall strip at this window is a render-only architectural
        cutaway. The aperture and frame still bound physical body clearance.
        """
        spec=self.objects[ident]['spec'];left=spec['position'][0]-spec['size'][0]/2
        right=spec['position'][0]+spec['size'][0]/2
        hidden=[]
        for wall in self.wall_meshes:
            bounds=corners(wall);a=min(p.x for p in bounds);b=max(p.x for p in bounds)
            if a>=left-.01 and b<=right+.01:
                wall.hide_render=True;wall['traversal_cutaway']=ident;hidden.append(wall.name)
        return hidden

    def set_angle(self,ident,angle,frame=None):
        hinge=self.objects[ident]['hinge']; hinge.rotation_euler.z=angle
        if frame is not None: hinge.keyframe_insert('rotation_euler',frame=frame)
        bpy.context.view_layer.update()

    def floor_blocked(self,xy,padding=.24):
        p=Vector((xy[0],xy[1],0))
        for e in self.objects.values():
            if e['spec']['type'] in ('table','chair','box'):
                local=e['root'].matrix_world.inverted()@p
                w,d,_=e['spec']['size']
                if abs(local.x)<w/2+padding and abs(local.y)<d/2+padding: return True
        for obj in self.solids:
            if obj.name in {m.name for e in self.objects.values() if e['spec']['type'] in ('table','chair','box') for m in e['meshes']}:
                continue
            bounds=corners(obj)
            if min(p.z for p in bounds)>.45: continue
            local=obj.matrix_world.inverted()@p
            mins=[min(v[i] for v in obj.bound_box) for i in range(3)]
            maxs=[max(v[i] for v in obj.bound_box) for i in range(3)]
            if all(mins[i]-padding<local[i]<maxs[i]+padding for i in (0,1)): return True
        return False


def path_to(props,start,end):
    """Small floor grid; no diagonal corner cutting through furniture."""
    import heapq
    from math import hypot
    step=.2; width,depth,_=props.plan['room']['size']; xlim=width/2-.3; ylim=depth/2-.3
    if not props.plan['room']['enabled']:
        xlim=max(xlim,abs(start.x)+1,abs(end.x)+1)
        ylim=max(ylim,abs(start.y)+1,abs(end.y)+1)
    def node(p): return (round(p[0]/step),round(p[1]/step))
    initial,goal=node(start),node(end)
    def free(n):
        x,y=n[0]*step,n[1]*step
        return abs(x)<xlim and abs(y)<ylim and not props.floor_blocked((x,y))
    if props.floor_blocked(end): raise ValueError('交互站位被家具挡住，请调整物品位置')
    queue=[(0,initial)]; cost={initial:0}; parent={}
    while queue:
        _,current=heapq.heappop(queue)
        if current==goal: break
        for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
            nxt=(current[0]+dx,current[1]+dy)
            if not free(nxt) or (dx and dy and (not free((current[0]+dx,current[1])) or not free((current[0],current[1]+dy)))): continue
            value=cost[current]+hypot(dx,dy)
            if value<cost.get(nxt,float('inf')):
                parent[nxt]=current;cost[nxt]=value
                heapq.heappush(queue,(value+hypot(nxt[0]-goal[0],nxt[1]-goal[1]),nxt))
    if goal not in cost: raise ValueError('物品之间没有可通行的交互路径')
    route=[end.copy()]; n=goal
    while n!=initial:
        route.append(Vector((n[0]*step,n[1]*step,0)));n=parent[n]
    route.append(start.copy());route.reverse()
    # Round turns without removing collision-tested grid waypoints.
    for i in range(1,len(route)-1):
        candidate=(route[i-1]+route[i]*2+route[i+1])/4
        if not props.floor_blocked(candidate): route[i]=candidate
    return route


def along_path(path,progress):
    lengths=[(b-a).length for a,b in zip(path,path[1:])]; total=sum(lengths); wanted=total*progress
    for a,b,length in zip(path,path[1:],lengths):
        if wanted<=length: return a.lerp(b,wanted/max(length,1e-6))
        wanted-=length
    return path[-1].copy()


def scene_quality(props,actors,frames):
    from duel_scene import point,evaluated_corners
    scene=bpy.context.scene; collisions=[]; framing=[]; actor_framing=[]
    actor_meshes={ident:[obj for obj in scene.objects if obj.type=='MESH' and
                        (obj.get('actor_id')==ident or obj.parent==arm or
                         any(m.type=='ARMATURE' and m.object==arm for m in obj.modifiers))]
                  for ident,arm in actors.items()}
    for frame in range(1,frames+1):
        scene.frame_set(frame)
        for ident,arm in actors.items():
            for bone in ('Hips','Spine','Spine1','Spine2','Head','LeftLeg','RightLeg','LeftFoot','RightFoot'):
                position=point(arm,bone)
                for obj in props.solids:
                    local=obj.matrix_world.inverted()@position
                    lo=[min(v[i] for v in obj.bound_box) for i in range(3)]
                    hi=[max(v[i] for v in obj.bound_box) for i in range(3)]
                    closest=Vector([min(hi[i],max(lo[i],local[i])) for i in range(3)])
                    # Coarse body clearance; intentional hand contact is checked
                    # separately against its semantic contact anchor.
                    if (local-closest).length<.025:
                        collisions.append({'frame':frame,'actor':ident,'bone':bone,'part':obj.name})
                        break
        if scene.camera.name=='Duel_FullCamera':
            for ident,meshes in actor_meshes.items():
                if any(min(uv.x,uv.y,1-uv.x,1-uv.y)<.0499 or uv.z<0
                       for uv in [world_to_camera_view(scene,scene.camera,p) for p in evaluated_corners(meshes)]):
                    actor_framing.append({'frame':frame,'actor':ident})
            for ident,entry in props.objects.items():
                if entry['spec']['focus']:
                    if any(min(uv.x,uv.y,1-uv.x,1-uv.y)<.0499 or uv.z<0
                           for m in entry['meshes'] for uv in [world_to_camera_view(scene,scene.camera,p) for p in corners(m)]):
                        framing.append({'frame':frame,'object':ident})
    return {'passed':not collisions and not framing and not actor_framing,'object_count':len(props.objects),'body_obstacle_failures':collisions[:30],
            'object_camera_failures':framing[:30],'actor_camera_failures':actor_framing[:30],
            'scope':'Coarse bone-to-solid clearance and full-body/required-prop framing; not full skin/self-collision physics.'}
