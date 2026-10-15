"""Bounded single-person ground movement, distinct from animation playlists.

Hermes supplies the route and timing from natural language. This module only
validates that plan and its coverage; it never infers intent from prompt words.
"""
import math

from duel_plan import number


BODY_SOURCES = frozenset((
    'mixamo_fbx/basic/Idle.fbx',
    'mixamo_fbx/basic/Walk.fbx',
    'mixamo_fbx/basic/Run.fbx',
))
POSITION_TOLERANCE = 1e-5


def _keys(value, allowed, label):
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(label + '存在未执行字段：' + '、'.join(sorted(str(k) for k in unknown)))


def position(raw, label):
    if not isinstance(raw, list) or len(raw) != 2:
        raise ValueError(label + '需要[x,y]两个地面坐标，单位米')
    return [number(value, label, -6, 6) for value in raw]


def validate_locomotion(raw, duration):
    """Return the exact, continuous route without changing requested timing."""
    if not isinstance(raw, dict) or isinstance(raw.get('version'), bool) or raw.get('version') != 1:
        raise ValueError('locomotion需要version=1的移动计划对象')
    _keys(raw, ('version', 'start_position', 'start_yaw_deg', 'segments'), 'locomotion')
    current = position(raw.get('start_position'), '移动起点')
    initial = current.copy()
    yaw = number(raw.get('start_yaw_deg', 0), '移动起始朝向', -180, 180)
    segments = raw.get('segments')
    if not isinstance(segments, list) or not 1 <= len(segments) <= 16:
        raise ValueError('locomotion.segments需要1至16段移动、停步或转身')
    previous_end = 0
    normalized = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise ValueError('每个移动段需要对象')
        gait = segment.get('gait')
        if gait not in ('walk', 'run', 'idle', 'turn'):
            raise ValueError('移动gait只支持walk、run、idle或turn；其它动作尚未接入')
        allowed = ('start', 'end', 'gait', 'waypoints') if gait in ('walk', 'run') else (
            ('start', 'end', 'gait', 'end_yaw_deg') if gait == 'turn' else ('start', 'end', 'gait')
        )
        _keys(segment, allowed, f'第{index + 1}移动段')
        start = number(segment.get('start'), '移动段起点', 0, duration)
        end = number(segment.get('end'), '移动段终点（至少0.5秒）', start + .5, duration)
        if start < previous_end - POSITION_TOLERANCE:
            raise ValueError('移动段需要按时间顺序且不能重叠')
        clean = {'start': start, 'end': end, 'gait': gait}
        if gait in ('walk', 'run'):
            raw_points = segment.get('waypoints')
            if not isinstance(raw_points, list) or not 2 <= len(raw_points) <= 8:
                raise ValueError('walk/run的waypoints需要2至8个地面坐标')
            points = [position(point, '移动路径点') for point in raw_points]
            if math.dist(points[0], current) > POSITION_TOLERANCE:
                raise ValueError('移动路径首点必须等于人物当前位置，不能瞬移到新起点')
            if any(math.dist(a, b) <= POSITION_TOLERANCE for a, b in zip(points, points[1:])):
                raise ValueError('移动路径不能包含相邻重复点；原地等待使用idle，原地转身使用turn')
            clean['waypoints'] = points
            current = points[-1].copy()
        elif gait == 'turn':
            clean['end_yaw_deg'] = number(segment.get('end_yaw_deg'), '转身终点朝向', -180, 180)
        normalized.append(clean)
        previous_end = end
    return {'version': 1, 'start_position': initial, 'start_yaw_deg': yaw, 'segments': normalized}


def validate_locomotion_coverage(cast, scene, single=None):
    """Require route intent and executable fields to agree, without contact claims.

    The scene can be checked before the body plan exists. Once single is given,
    route omission and undeclared route execution both fail. Old casts without
    requires_locomotion continue to mean ordinary animation playback.
    """
    required = False
    if cast is not None:
        if not isinstance(cast, dict):
            raise ValueError('移动覆盖检查需要人物解析对象')
        required = cast.get('requires_locomotion', False)
        if not isinstance(required, bool):
            raise ValueError('requires_locomotion需要布尔值')
        if required and cast.get('actor_count', len(cast.get('actors', [])) or 1) != 1:
            raise ValueError('通用路线移动执行器当前仅支持单人，多人路线移动尚未接入')
        if required and (cast.get('requires_interaction', False) or cast.get('object_intents', [])):
            raise ValueError('通用路线移动与物品操作或翻窗的串联尚未接入；不能删除关键操作')
    if scene is not None and not isinstance(scene, dict):
        raise ValueError('移动覆盖检查需要场景对象')
    if required and scene is not None and scene.get('interactions', []):
        raise ValueError('通用路线移动当前只允许静态物品，不与物品操作或翻窗混编')
    if single is None:
        return None
    if not isinstance(single, dict):
        raise ValueError('移动覆盖检查需要单人人物计划对象')
    locomotion = single.get('locomotion')
    if cast is not None and required != (locomotion is not None):
        if required:
            raise ValueError('用户要求通用路线移动，但single_plan缺少locomotion；普通Walk/Run轮播不能替代目标路径')
        raise ValueError('single_plan存在locomotion，但人物解析没有声明requires_locomotion=true')
    if locomotion is None:
        return None
    if scene is not None and scene.get('interactions', []):
        raise ValueError('通用路线移动当前只允许静态物品，不与物品操作或翻窗混编')
    if any(asset not in BODY_SOURCES for asset in single.get('animations', [])) or not single.get('animations'):
        raise ValueError('通用路线移动只支持Idle/Walk/Run身体来源，与跳跃、拳脚等混编尚未接入')
    if 'camera' in single:
        from camera_plan import validate_camera
        validate_camera(single['camera'])
    elif single.get('camera_policy', 'fixed') != 'fixed' or single.get('pitch_deg', 0) != 0 or single.get('distance_ratio', 3.5) != 3.5 or single.get('fov', 50) != 50:
        raise ValueError('通用路线移动当前使用固定全身全景：fixed、pitch_deg=0、distance_ratio=3.5、fov=50')
    return validate_locomotion(locomotion, single['duration_seconds'])
