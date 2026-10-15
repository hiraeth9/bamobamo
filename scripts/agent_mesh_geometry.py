"""Optional exact box construction and measured world-mesh bounds.

Importing this module does not import bpy or alter a scene.  Functions operate
on the current Blender scene only when called.  They impose no action, actor
count, or scene-size whitelist and certify geometry dimensions only.

make_box returns the actual new Object.  Blender may suffix a conflicting name
with .001; callers must explicitly remove or rename their own old object if
replacement is intended.  This helper never removes another object.

world_mesh_bounds measures evaluated polygon vertices after modifiers and all
parent/world transforms.  It does not use obj.dimensions or declared metadata.
Procedural instances must be realized as mesh geometry before measurement.
All lengths and coordinates use Blender scene units without implicit scaling.
"""
import math
from numbers import Real


def _xyz(values, label, positive=False):
    if isinstance(values, (str, bytes)):
        raise ValueError(label + ' must contain three finite numbers')
    try:
        values = tuple(values)
    except TypeError as error:
        raise ValueError(label + ' must contain three finite numbers') from error
    if len(values) != 3 or any(isinstance(value, bool) or not isinstance(value, Real)
                               or not math.isfinite(value) for value in values):
        raise ValueError(label + ' must contain three finite numbers')
    if positive and any(value <= 0 for value in values):
        raise ValueError(label + ' dimensions must be positive')
    return tuple(float(value) for value in values)


def make_box(name, size, location, material=None, collection=None):
    """Create a centered box with exact half-extents and a world translation.

The returned mesh has no parent, identity scale, and no context/operator cube
size assumptions.  Material and destination Collection are optional.
"""
    if not isinstance(name, str) or not name:
        raise ValueError('name must be a nonempty string')
    size = _xyz(size, 'size', positive=True)
    location = _xyz(location, 'location')
    import bpy
    from mathutils import Matrix
    x, y, z = (value * .5 for value in size)
    vertices = [(-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z),
                (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z)]
    faces = [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    data = bpy.data.meshes.new(name + '_Mesh')
    data.from_pydata(vertices, [], faces)
    data.update()
    obj = bpy.data.objects.new(name, data)
    obj.matrix_world = Matrix.Translation(location)
    if material is not None:
        data.materials.append(material)
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def world_mesh_bounds(obj):
    """Return actual evaluated world min/max/dimensions for a mesh object."""
    import bpy
    if obj is None or obj.type not in ('MESH', 'CURVE', 'FONT', 'SURFACE', 'META'):
        raise ValueError('obj must provide polygon mesh geometry')
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(graph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=graph)
    try:
        if mesh is None or not mesh.vertices:
            raise ValueError('object has no evaluated mesh vertices')
        minimum = [float('inf')] * 3
        maximum = [float('-inf')] * 3
        for vertex in mesh.vertices:
            point = evaluated.matrix_world @ vertex.co
            if any(not math.isfinite(value) for value in point):
                raise ValueError('evaluated world vertices are not finite')
            for axis in range(3):
                minimum[axis] = min(minimum[axis], float(point[axis]))
                maximum[axis] = max(maximum[axis], float(point[axis]))
        return {'name': obj.name, 'min': minimum, 'max': maximum,
                'dimensions': [maximum[axis] - minimum[axis] for axis in range(3)],
                'vertex_count': len(mesh.vertices), 'frame': int(bpy.context.scene.frame_current),
                'scope': 'actual evaluated polygon vertices in world coordinates'}
    finally:
        evaluated.to_mesh_clear()
