"""Executable fixed-camera intent, independent of Blender and prompt wording.

New placements use the same heading convention as actor navigation: zero is
the reference front (-Y), +90 is its right (+X). Legacy duel camera azimuths
remain readable in their existing convention; new placements are explicit.
Elevation describes the camera's position above the target: +20 is a high
camera looking down by 20 degrees, -20 a low camera looking up. It is not the
legacy camera orientation pitch_deg, whose sign convention is different.
"""
import math
import re


FIELDS = frozenset(('version', 'reference', 'target', 'azimuth_deg', 'elevation_deg',
                    'projection', 'framing', 'fov_deg', 'occlusion_policy',
                    'position', 'look_at'))


def _number(value, label, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label+'需要有限数字')
    if not lo <= value <= hi:
        raise ValueError(f'{label}必须在{lo}至{hi}之间')
    return value


def _vector(value, label):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(label+'需要世界坐标[x,y,z]')
    return [_number(v, label, -30, 30) for v in value]


def validate_camera(raw):
    if not isinstance(raw, dict):
        raise ValueError('camera需要对象')
    unknown = set(raw)-FIELDS
    if unknown:
        raise ValueError('camera存在未执行字段：'+'、'.join(sorted(str(k) for k in unknown)))
    if type(raw.get('version', 1)) is not int or raw.get('version', 1) != 1:
        raise ValueError('camera需要version=1')
    projection = raw.get('projection', 'orthographic')
    if projection not in ('orthographic', 'perspective'):
        raise ValueError('camera.projection支持orthographic或perspective')
    if raw.get('framing', 'full_body') != 'full_body':
        raise ValueError('当前camera仅支持full_body全身构图；特写尚未接入')
    occlusion = raw.get('occlusion_policy', 'transparent_architecture')
    if occlusion not in ('transparent_architecture', 'strict'):
        raise ValueError('camera.occlusion_policy支持transparent_architecture或strict')
    result = {'version': 1, 'projection': projection, 'framing': 'full_body',
              'fov_deg': _number(raw.get('fov_deg', 50), 'camera.fov_deg', 20, 90),
              'occlusion_policy': occlusion}
    if 'position' in raw:
        if any(key in raw for key in ('reference', 'target', 'azimuth_deg', 'elevation_deg')):
            raise ValueError('明确camera.position不能同时指定reference/target/azimuth/elevation；位置与方位可能冲突')
        result['position'] = _vector(raw['position'], 'camera.position')
    else:
        reference = raw.get('reference', 'world')
        if reference not in ('world', 'object', 'actor'):
            raise ValueError('camera.reference支持world、object或actor')
        target = raw.get('target')
        if reference == 'world' and 'target' in raw:
            raise ValueError('world相机不使用target；物品或人物参考需明确reference')
        if reference in ('object', 'actor'):
            if not isinstance(target, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', target):
                raise ValueError('物品或人物参考相机需要有效target')
            if reference == 'actor' and target not in ('A', 'B'):
                raise ValueError('人物参考相机target需要A或B')
            result['target'] = target
        result.update(reference=reference,
                      azimuth_deg=_number(raw.get('azimuth_deg', 0), 'camera.azimuth_deg', -180, 180),
                      elevation_deg=_number(raw.get('elevation_deg', 15), 'camera.elevation_deg', -80, 80))
    if 'look_at' in raw:
        result['look_at'] = _vector(raw['look_at'], 'camera.look_at')
        if 'position' in result and math.dist(result['position'], result['look_at']) < .25:
            raise ValueError('camera.position与look_at至少相距0.25米')
    return result


def camera_capabilities():
    return {
        'placement_field': 'single_plan.camera or duel_plan.camera.placement',
        'projection': ['orthographic', 'perspective'],
        'framing': 'full_body',
        'reference': ['world', 'object', 'actor'],
        'angle_convention': '0=参考正前方(-Y)，+90=右(+X)，-90=左(-X)，180=后(+Y)；object固定门窗框yaw，actor视频首帧朝向',
        'elevation_convention': 'elevation_deg是相机相对目标的机位仰角；+20=高机位向下俯视20度，-20=低机位向上仰视20度；不是旧pitch_deg朝向角，符号不能混用。自定义look_at以真实机位与目标方向为准。',
        'parameters': sorted(FIELDS),
        'explicit_geometry': 'position/look_at为世界米坐标；position不能与方向参数同时指定。明确参数不自动换角、退后或改变目标。',
        'auto_fit': '方向计划可自动计算距离及正交scale；明确position的透视构图不可满足则拒绝。',
        'occlusion_policy': 'transparent_architecture仅参考展示，墙框门扇透射但碰撞实体保留；strict不将不透明物体偷偷透明化，主体被遮挡时拒绝。',
        'unsupported': '该placement不包含环绕动画、移动镜头、自动跟拍、特写、多个分镜；双人已有cinematic full/pov协议保持可用。',
    }
