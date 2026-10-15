"""Door-local passage corridors and real, two-sided grip geometry.

The door leaf, both handles and pull bars are real SceneObjects solids. Routes
are checked against a conservative envelope measured from the existing Beta
Idle/Walk skin; the executor still has to verify its actual posed skin and hand
contacts. No actor scaling, collision exclusions or wall cutaways are applied.
"""
import math

import bpy
from mathutils import Matrix, Vector

from locomotion_path import compile_timeline


# Actual root-extracted source skin, measured at every Idle and Walk source
# frame. Coordinates refer to Actor yaw=0, forward=-Y, at an unscaled floor root.
BODY_ENVELOPE = {
    'min': [-.406259746, -.665395499, -.000373036],
    'max': [.402280570, .476072640, 1.782869101],
    'measurement': 'reports/door_passage_body_measurement_20261013.json',
}
DIRECTIONS = {'outward': 1, 'inward': -1,
              'inside_to_outside': 1, 'outside_to_inside': -1}


def _hull(points):
    points = sorted(set((float(p[0]), float(p[1])) for p in points))
    if len(points) <= 2:
        return points
    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
    lower, upper = [], []
    for point in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    for point in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1]+upper[:-1]


def _overlap(a, b):
    """Convex polygon SAT; touching an inflated boundary is conservatively blocked."""
    for polygon in (a, b):
        for first, second in zip(polygon, polygon[1:]+polygon[:1]):
            axis = (-(second[1]-first[1]), second[0]-first[0])
            pa = [p[0]*axis[0]+p[1]*axis[1] for p in a]
            pb = [p[0]*axis[0]+p[1]*axis[1] for p in b]
            if max(pa) < min(pb)-1e-9 or max(pb) < min(pa)-1e-9:
                return False
    return True


class DoorPassageGeometry:
    def __init__(self, props, door_id, body_envelope=None, clearance=.02):
        self.props, self.door_id = props, door_id
        self.entry = props.objects[door_id]
        self.spec = self.entry['spec']
        if self.spec['type'] != 'door':
            raise ValueError('门洞通行几何需要door资产')
        self.body = body_envelope or BODY_ENVELOPE
        if any(len(self.body[k]) != 3 for k in ('min', 'max')):
            raise ValueError('人物净空包络需要三维min/max')
        if any(not math.isfinite(float(v)) for key in ('min', 'max') for v in self.body[key]):
            raise ValueError('人物净空包络需要有限数值')
        if any(self.body['min'][axis] >= self.body['max'][axis] for axis in range(3)):
            raise ValueError('人物净空包络min必须严格小于max')
        self.clearance = float(clearance)
        if not math.isfinite(self.clearance) or self.clearance < .015:
            raise ValueError('门洞通行不能降低最小1.5厘米几何安全余量')
        bpy.context.view_layer.update()
        self.frame = self.entry['root']
        self.inverse = self.frame.matrix_world.inverted()
        self.hinge = self.entry['hinge']
        self.angle = abs(math.degrees(self.hinge.rotation_euler.z))
        self.width, self.depth, self.height = self.spec['size']
        self.solid_cache = {}

    def world(self, local):
        point = Vector((*local, 0.)) if len(local) == 2 else Vector(local)
        return self.frame.matrix_world @ point

    def local(self, world):
        point = Vector((*world, 0.)) if len(world) == 2 else Vector(world)
        return self.inverse @ point

    def _angle(self, angle):
        return self.angle if angle is None else float(angle)

    def _leaf_matrix(self, angle):
        # SceneObjects' established convention: a positive open_angle_deg
        # rotates the leaf towards the room's inside by a negative Z angle.
        return Matrix.Translation(self.hinge.location) @ Matrix.Rotation(-math.radians(angle), 4, 'Z')

    def _anchor_local(self, label, angle):
        anchor = self.entry['anchors'][label]
        if anchor.parent == self.hinge:
            return self._leaf_matrix(angle) @ anchor.location
        return anchor.location.copy()

    def handle(self, side='inside', angle_deg=None, grip='bar'):
        if side not in ('inside', 'outside') or grip not in ('bar', 'handle'):
            raise ValueError('门操作side为inside/outside，grip为bar/handle')
        label = ('contact_front' if side == 'inside' else 'contact_back') if grip == 'bar' else (
            'handle' if side == 'inside' else 'handle_back')
        return self.world(self._anchor_local(label, self._angle(angle_deg)))

    def leaf_normal(self, side='inside', angle_deg=None):
        angle = self._angle(angle_deg)
        normal = self._leaf_matrix(angle).to_3x3() @ Vector((0, -1 if side == 'inside' else 1, 0))
        return (self.frame.matrix_world.to_3x3() @ normal).normalized()

    def operation_stance(self, side='inside', angle_deg=None, *, grip='bar',
                         distance=.55, lateral_offset=0.):
        """Candidate stance, with a real contact target; reach is not certified.

        The passage-chain pull bar uses fixed roots close to the hinge, avoiding
        the far-handle arc which would carry the outside actor through the jamb.
        Optional old-handle stances are reported for calibration only.
        """
        if side not in ('inside', 'outside'):
            raise ValueError('门操作side为inside或outside')
        angle = self._angle(angle_deg)
        target = self.handle(side, angle, grip)
        if grip == 'bar':
            local = Vector((-self.width/2-.28, -.50, 0)) if side == 'inside' else Vector((-.25, .45, 0))
            root = self.world(local)
            # Face the mean of this physical side's closed/open contacts. This
            # keeps root yaw fixed throughout the sweep. The inside root is
            # 6 cm left of the initial static calibration so stepping turns do
            # not carry the knee into the open leaf. Runtime must verify the
            # resulting hand reach and every posed frame without exclusions.
            mid = (self.handle(side, 0, grip)+self.handle(side, self.spec['open_angle_deg'], grip))/2
            direction = mid-root
            direction.z = 0
        else:
            normal = self.leaf_normal(side, angle)
            tangent = self.frame.matrix_world.to_3x3() @ (
                self._leaf_matrix(angle).to_3x3() @ Vector((1, 0, 0)))
            root = target+normal*distance+tangent*lateral_offset
            root.z = self.spec['position'][2]
            direction = -normal
        yaw = math.atan2(direction.x, -direction.y)
        return {'side': side, 'angle_deg': angle, 'grip': grip,
                'position': list(root), 'local_position': list(self.local(root)),
                'yaw': yaw, 'target': list(target),
                'stance_calibration': 'Fixed roots and physical closed/open contact midpoint; runtime skin/IK QA remains required.',
                'root_to_grip_distance_m': (target-root).length,
                'requires_actual_fk_ik_reach_and_skin_review': True}

    def operation_path(self, side, start_angle, end_angle, *, grip='bar',
                       distance=.55, lateral_offset=0., step_deg=1.):
        if not math.isfinite(step_deg) or step_deg <= 0:
            raise ValueError('门操作角度采样步长必须为有限正数')
        count = max(1, math.ceil(abs(end_angle-start_angle)/step_deg))
        return [self.operation_stance(side, start_angle+(end_angle-start_angle)*i/count,
                                      grip=grip, distance=distance, lateral_offset=lateral_offset)
                for i in range(count+1)]

    def _solid_geometry(self, angle):
        key = round(angle, 8)
        if key not in self.solid_cache:
            geometries = []
            for solid in self.props.solids:
                if solid.parent == self.hinge:
                    matrix = self._leaf_matrix(angle) @ solid.matrix_basis
                else:
                    matrix = self.inverse @ solid.matrix_world
                vertices = [matrix @ Vector(corner) for corner in solid.bound_box]
                geometries.append({'name': solid.name,
                    'polygon': _hull(vertices),
                    'minimum_z': min(v.z for v in vertices),
                    'maximum_z': max(v.z for v in vertices),
                    'moving_leaf': solid.parent == self.hinge})
            self.solid_cache[key] = geometries
        return self.solid_cache[key]

    def passage_band(self, direction='outward', angle_deg=None):
        if direction not in DIRECTIONS:
            raise ValueError('穿门方向为outward或inward')
        angle = self._angle(angle_deg)
        if not 0 < angle <= 90:
            raise ValueError('穿门需要真实打开的门扇')
        sign = DIRECTIONS[direction]
        if sign == 1:
            left, right = -self.body['max'][0], -self.body['min'][0]
        else:
            left, right = self.body['min'][0], self.body['max'][0]
        inner_left, inner_right = -self.width/2+.055, self.width/2-.055
        moving = [s for s in self._solid_geometry(angle) if s['moving_leaf']]
        leaf_right = max(p[0] for item in moving for p in item['polygon'])
        minimum = max(inner_left, leaf_right)-left+self.clearance
        maximum = inner_right-right-self.clearance
        required_width = right-left+2*self.clearance
        available_width = inner_right-max(inner_left, leaf_right)
        if minimum > maximum:
            raise ValueError(f'门扇与把手后的通行净宽仅{available_width:.3f}米，'
                             f'当前人物至少需要{required_width:.3f}米；请增大真实门宽或开角')
        if self.body['max'][2]+self.clearance > self.height-.055:
            raise ValueError('门洞净高不足以容纳人物真实蒙皮和安全余量')
        return {'direction': 'outward' if sign == 1 else 'inward', 'angle_deg': angle,
                'minimum_root_x': minimum, 'maximum_root_x': maximum,
                'center_root_x': (minimum+maximum)/2,
                'available_width_m': available_width, 'required_width_m': required_width,
                'leaf_right_x': leaf_right, 'inner_left_x': inner_left,
                'inner_right_x': inner_right, 'safety_clearance_m': self.clearance}

    def _body_polygon(self, point, direction):
        outward = DIRECTIONS[direction] == 1
        minimum, maximum = self.body['min'], self.body['max']
        xmin, xmax = (-maximum[0], -minimum[0]) if outward else (minimum[0], maximum[0])
        ymin, ymax = (-maximum[1], -minimum[1]) if outward else (minimum[1], maximum[1])
        margin = self.clearance
        return [(point.x+x, point.y+y) for x, y in
                ((xmin-margin, ymin-margin), (xmax+margin, ymin-margin),
                 (xmax+margin, ymax+margin), (xmin-margin, ymax+margin))]

    def route_blocked(self, xy, angle_deg=None, direction='outward'):
        angle = self._angle(angle_deg)
        band = self.passage_band(direction, angle)
        point = self.local(xy)
        room = self.props.plan['room']
        if room['enabled']:
            width, depth, _ = room['size']
            radius_x = max(abs(self.body['min'][0]), abs(self.body['max'][0]))+self.clearance
            world = self.world(point)
            if abs(world.x) > width/2-radius_x or world.y < -depth/2+max(abs(self.body['min'][1]), abs(self.body['max'][1]))+self.clearance:
                return True
            # Extend only the actual target portal's exterior strip. No path
            # search may go around the end of the rear wall or another doorway.
            if world.y > depth/2+1e-9 and not (
                    band['minimum_root_x']-1e-9 <= point.x <= band['maximum_root_x']+1e-9 and
                    0 <= point.y <= 2.0):
                return True
        polygon = self._body_polygon(point, direction)
        minimum_z, maximum_z = self.body['min'][2], self.body['max'][2]
        return any(solid['maximum_z'] >= minimum_z and solid['minimum_z'] <= maximum_z and
                   _overlap(polygon, solid['polygon'])
                   for solid in self._solid_geometry(angle))

    def passage_route(self, direction='outward', angle_deg=None):
        """Return a straight corridor through this aperture, never around a wall."""
        band = self.passage_band(direction, angle_deg)
        sign, x = DIRECTIONS[direction], band['center_root_x']
        a, b = Vector((x, -sign*1.25, 0)), Vector((x, sign*1.25, 0))
        count = math.ceil((b-a).length/.025)
        for i in range(count+1):
            point = self.world(a.lerp(b, i/count))
            if self.route_blocked(point, band['angle_deg'], direction):
                raise ValueError('门洞入口、净空或外侧出口被真实门框/墙/家具阻挡，不能绕墙代替穿门')
        yaw = math.atan2((self.world(b)-self.world(a)).x, -(self.world(b)-self.world(a)).y)
        return {**band, 'entry': list(self.world(a)), 'exit': list(self.world(b)),
                'local_entry': list(a), 'local_exit': list(b), 'yaw': yaw,
                'waypoints': [list(self.world(a)[:2]), list(self.world(b)[:2])],
                'fixed_anchor_names': ['entry', 'exit'],
                'scope': 'Measured phase-independent source skin envelope vs real static solid footprints; executor must verify posed skin, hands, feet and camera.'}

    def compile_passage(self, direction, start, end, duration, *, angle_deg=None,
                        start_position=None, start_yaw_deg=None, fps=30):
        route = self.passage_route(direction, angle_deg)
        if start_position is not None and (Vector((*start_position[:2], 0))-Vector(route['entry'])).xy.length > .025:
            raise ValueError('请先在门叶和墙体净空范围内移至实际入口站位，再执行直通门洞阶段')
        yaw_deg = math.degrees(route['yaw']) if start_yaw_deg is None else start_yaw_deg
        movement = {'version': 1, 'start_position': route['waypoints'][0],
                    'start_yaw_deg': yaw_deg, 'segments': [{'start': start, 'end': end,
                    'gait': 'walk', 'waypoints': route['waypoints']}]}
        result = compile_timeline(movement, duration,
            blocked=lambda xy: self.route_blocked(xy, route['angle_deg'], direction), fps=fps)
        result['door_passage_geometry'] = route
        return result
