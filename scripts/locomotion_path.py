"""Collision checked floor routes and timed, forward-facing root trajectories.

This module deliberately has no Blender dependency.  A caller supplies the
inflated floor obstacle predicate; the animation executor remains responsible
for real mesh clearance, foot contacts and the appearance of a turn.
"""
from bisect import bisect_right
import heapq
import math


WALK_SPEED = 1.65
RUN_SPEED = 3.5
ACCELERATION = 3.8
YAW_RATE = math.radians(120)
CHECK_STEP = .025
TURN_STEP_RADIUS = .25
CONTINUOUS_HEADING_TOLERANCE = math.radians(.01)
FINAL_SETTLE_SECONDS = .4


def _xy(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError('移动坐标需要两个数值')
    result = tuple(float(v) for v in value)
    if any(not math.isfinite(v) for v in result):
        raise ValueError('移动坐标需要有限数值')
    return result


def _distance(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _lerp(a, b, fraction):
    return (a[0] + (b[0] - a[0]) * fraction,
            a[1] + (b[1] - a[1]) * fraction)


def _angle_delta(a, b):
    return math.atan2(math.sin(b - a), math.cos(b - a))


def _heading(a, b):
    return math.atan2(b[0] - a[0], -(b[1] - a[1]))


def _smooth(value):
    value = max(0., min(1., value))
    return value * value * (3 - 2 * value)


def _predicate(blocked, bounds):
    if bounds is not None:
        if len(bounds) != 4 or not all(math.isfinite(float(v)) for v in bounds):
            raise ValueError('移动边界需要 xmin,xmax,ymin,ymax')
        xmin, xmax, ymin, ymax = map(float, bounds)
        if xmin >= xmax or ymin >= ymax:
            raise ValueError('移动边界没有可通行面积')
    def free(point):
        if bounds is not None and not (xmin - 1e-9 <= point[0] <= xmax + 1e-9 and
                                       ymin - 1e-9 <= point[1] <= ymax + 1e-9):
            return False
        return not (blocked and blocked(point))
    return free


def _line_free(a, b, free):
    count = max(1, math.ceil(_distance(a, b) / CHECK_STEP))
    return all(free(_lerp(a, b, i / count)) for i in range(count + 1))


def plan_route(start, end, blocked=None, bounds=None, grid_step=.15):
    """Use a direct segment when safe, otherwise A* and checked simplification.

    Exact endpoints are connected to nearby *free* grid nodes.  Every grid edge
    and every shortcut is sampled at no more than 2.5 cm; diagonal corner cuts
    are additionally forbidden.  Neither grid rounding nor smoothing can turn
    an obstructed endpoint into an accepted target.
    """
    start, end = _xy(start), _xy(end)
    free = _predicate(blocked, bounds)
    if not free(start):
        raise ValueError('移动起点被障碍物或房间边界挡住')
    if not free(end):
        raise ValueError('移动终点被障碍物或房间边界挡住')
    if _line_free(start, end, free):
        return [start, end] if _distance(start, end) > 1e-8 else [start]
    if bounds is None:
        bounds = (min(start[0], end[0]) - 4, max(start[0], end[0]) + 4,
                  min(start[1], end[1]) - 4, max(start[1], end[1]) + 4)
        free = _predicate(blocked, bounds)
    step = float(grid_step)
    if not .05 <= step <= .3:
        raise ValueError('路径网格步长需要为0.05至0.3米')
    def point(node):
        return (node[0] * step, node[1] * step)
    free_cache = {}
    def node_free(node):
        if node not in free_cache:
            free_cache[node] = free(point(node))
        return free_cache[node]
    def connections(endpoint):
        near = (round(endpoint[0] / step), round(endpoint[1] / step))
        return {node: _distance(endpoint, point(node))
                for dx in range(-1, 2) for dy in range(-1, 2)
                if node_free(node := (near[0] + dx, near[1] + dy)) and
                _line_free(endpoint, point(node), free)}
    starts, goals = connections(start), connections(end)
    if not starts or not goals:
        raise ValueError('移动端点附近没有连续可通行路径')
    queue, costs, parents = [], {}, {}
    for node, cost in starts.items():
        costs[node] = cost
        parents[node] = None
        heapq.heappush(queue, (cost + _distance(point(node), end), cost, node))
    best, best_cost = None, float('inf')
    while queue:
        estimate, cost, current = heapq.heappop(queue)
        if estimate >= best_cost - 1e-10:
            break
        if cost > costs[current] + 1e-10:
            continue
        if current in goals and cost + goals[current] < best_cost:
            best, best_cost = current, cost + goals[current]
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                       (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nxt = (current[0] + dx, current[1] + dy)
            if not node_free(nxt):
                continue
            if dx and dy and (not node_free((current[0] + dx, current[1])) or
                              not node_free((current[0], current[1] + dy))):
                continue
            if not _line_free(point(current), point(nxt), free):
                continue
            candidate = cost + step * math.hypot(dx, dy)
            if candidate < costs.get(nxt, float('inf')) - 1e-10:
                costs[nxt], parents[nxt] = candidate, current
                heapq.heappush(queue, (candidate + _distance(point(nxt), end), candidate, nxt))
    if best is None:
        raise ValueError('起点与终点之间没有可通行路径')
    path = [end]
    node = best
    while node is not None:
        path.append(point(node))
        node = parents[node]
    path.append(start)
    path.reverse()
    path = _deduplicate(path)
    simplified = [path[0]]
    index = 0
    while index < len(path) - 1:
        target = next(i for i in range(len(path) - 1, index, -1)
                      if _line_free(path[index], path[i], free))
        simplified.append(path[target])
        index = target
    return simplified


def _deduplicate(path):
    result = []
    for point in path:
        if not result or _distance(point, result[-1]) > 1e-8:
            result.append(tuple(point))
    return result


def _rounded_route(path, free):
    """Round only verified corners; keep unsafe/sharp reversals as stop-turns."""
    if len(path) < 3:
        return path, []
    result, stops = [path[0]], []
    for i in range(1, len(path) - 1):
        a, b, c = path[i - 1:i + 2]
        before, after = _distance(a, b), _distance(b, c)
        angle = abs(_angle_delta(_heading(a, b), _heading(b, c)))
        if angle < math.radians(.5):
            continue
        accepted = None
        if angle < math.radians(150):
            for radius in (.35, .22, .13, .07):
                distance = min(radius, before * .33, after * .33)
                entry = _lerp(b, a, distance / before)
                exit = _lerp(b, c, distance / after)
                count = max(12, math.ceil(2 * distance / CHECK_STEP))
                curve = [_lerp(_lerp(entry, b, t / count),
                               _lerp(b, exit, t / count), t / count)
                         for t in range(count + 1)]
                if all(_line_free(x, y, free) for x, y in
                       zip([result[-1]] + curve, curve)):
                    accepted = curve
                    break
        if accepted:
            result.extend(accepted)
        else:
            result.append(b)
            stops.append(len(result) - 1)
    result.append(path[-1])
    return result, stops


def _densify(path):
    dense = [path[0]]
    for a, b in zip(path, path[1:]):
        count = max(1, math.ceil(_distance(a, b) / CHECK_STEP))
        dense.extend(_lerp(a, b, i / count) for i in range(1, count + 1))
    # At least one interior speed node is needed for a start-and-stop route.
    if len(dense) == 2:
        dense.insert(1, _lerp(dense[0], dense[1], .5))
    return dense


def _route_geometry(path, max_speed):
    points = _densify(path)
    distances = [0.]
    for a, b in zip(points, points[1:]):
        distances.append(distances[-1] + _distance(a, b))
    yaws = []
    for i in range(len(points)):
        a = points[max(0, i - 1)]
        b = points[min(len(points) - 1, i + 1)]
        yaw = _heading(a, b)
        yaws.append(yaws[-1] + _angle_delta(yaws[-1], yaw) if yaws else yaw)
    limits = [max_speed] * len(points)
    for i in range(1, len(points)):
        change = abs(yaws[i] - yaws[i - 1])
        if change > 1e-10:
            cap = YAW_RATE * (distances[i] - distances[i - 1]) / change
            limits[i - 1] = min(limits[i - 1], cap)
            limits[i] = min(limits[i], cap)
    return {'points': points, 'distance': distances, 'yaw': yaws,
            'limits': limits, 'length': distances[-1]}


def _fast_profile(geometry, start_speed, end_speed):
    distances, limits = geometry['distance'], geometry['limits']
    speeds = list(limits)
    if start_speed > limits[0] + 1e-6 or end_speed > limits[-1] + 1e-6:
        raise ValueError('连续移动速度超过了转角的安全速度')
    speeds[0] = start_speed
    for i in range(1, len(speeds)):
        speeds[i] = min(speeds[i], math.sqrt(speeds[i - 1] ** 2 +
                                           2 * ACCELERATION * (distances[i] - distances[i - 1])))
    if speeds[-1] + 1e-6 < end_speed:
        raise ValueError('移动距离不足以达到下一段的连续速度')
    speeds[-1] = end_speed
    for i in range(len(speeds) - 2, -1, -1):
        speeds[i] = min(speeds[i], math.sqrt(speeds[i + 1] ** 2 +
                                           2 * ACCELERATION * (distances[i + 1] - distances[i])))
    if speeds[0] + 1e-6 < start_speed:
        raise ValueError('移动距离不足以安全减速或转弯')
    return _with_times(geometry, speeds)


def _with_times(geometry, speeds):
    times = [0.]
    for i in range(1, len(speeds)):
        distance = geometry['distance'][i] - geometry['distance'][i - 1]
        velocity_sum = speeds[i - 1] + speeds[i]
        if velocity_sum <= 1e-12:
            raise ValueError('非零移动路径不能使用全零速度')
        times.append(times[-1] + 2 * distance / velocity_sum)
    return {**geometry, 'speed': speeds, 'time': times, 'duration': times[-1]}


def _profile_duration(geometry, start_speed, end_speed, wanted, gait):
    fastest = _fast_profile(geometry, start_speed, end_speed)
    if wanted < fastest['duration'] - 1e-6:
        raise ValueError(f'移动时段太短，当前路径至少需要{fastest["duration"]:.2f}秒')
    floor = [max(math.sqrt(max(0., start_speed ** 2 - 2 * ACCELERATION * distance)),
                 math.sqrt(max(0., end_speed ** 2 - 2 * ACCELERATION * (geometry['length'] - distance))))
             for distance in geometry['distance']]
    def scaled(scale):
        # Lower the cruise cap while retaining the normal acceleration and
        # braking ramps.  Scaling every speed instead would stretch the ramps
        # and disguise an almost stationary gait as a very long "run".
        cap = max(fastest['speed']) * scale
        speeds = [max(low, min(fast, cap)) for low, fast in zip(floor, fastest['speed'])]
        speeds[0], speeds[-1] = start_speed, end_speed
        return _with_times(geometry, speeds)
    lower = 1e-5
    if gait == 'run':
        peak = max(fastest['speed'])
        if peak < 1.1 - 1e-6:
            raise ValueError('距离或转角不足以形成真实跑步，请增加跑步距离或使用步行')
        # Do not disguise a very slow walk as run.  Extra time becomes a final
        # standing pause only when the route has already braked to zero.
        lower = min(1., 1.1 / peak)
    slowest = scaled(lower)
    if wanted > slowest['duration'] + 1e-5:
        if end_speed > 1e-6:
            raise ValueError('连续移动时段过长、距离过短；请缩短该时段或增加距离')
        return slowest, wanted - slowest['duration']
    low, high = lower, 1.
    for _ in range(55):
        middle = (low + high) / 2
        trial = scaled(middle)
        if trial['duration'] > wanted:
            low = middle
        else:
            high = middle
    return scaled((low + high) / 2), 0.


def _sample_profile(profile, time):
    if time <= 0:
        return profile['points'][0], profile['yaw'][0], profile['speed'][0], 0.
    if time >= profile['duration']:
        return profile['points'][-1], profile['yaw'][-1], profile['speed'][-1], profile['length']
    i = min(len(profile['time']) - 2, bisect_right(profile['time'], time) - 1)
    elapsed = time - profile['time'][i]
    span = profile['time'][i + 1] - profile['time'][i]
    v0, v1 = profile['speed'][i:i + 2]
    acceleration = (v1 - v0) / span
    distance = v0 * elapsed + .5 * acceleration * elapsed ** 2
    interval = profile['distance'][i + 1] - profile['distance'][i]
    fraction = max(0., min(1., distance / interval))
    return (_lerp(profile['points'][i], profile['points'][i + 1], fraction),
            profile['yaw'][i] + (profile['yaw'][i + 1] - profile['yaw'][i]) * fraction,
            v0 + acceleration * elapsed, profile['distance'][i] + distance)


def _prepare_move(segment, blocked, bounds):
    waypoints = [_xy(point) for point in segment['waypoints']]
    free = _predicate(blocked, bounds)
    route = [waypoints[0]]
    for a, b in zip(waypoints, waypoints[1:]):
        route.extend(plan_route(a, b, blocked, bounds)[1:])
    route = _deduplicate(route)
    if len(route) < 2:
        raise ValueError('walk/run需要实际水平移动，原地站立应使用idle')
    rounded, stops = _rounded_route(route, free)
    if not all(_line_free(a, b, free) for a, b in zip(rounded, rounded[1:])):
        raise ValueError('平滑后的路径没有足够障碍物净空')
    pieces = []
    first = 0
    for stop in stops + [len(rounded) - 1]:
        if stop > first:
            geometry = _route_geometry(rounded[first:stop + 1],
                                       WALK_SPEED if segment['gait'] == 'walk' else RUN_SPEED)
            pieces.append(geometry)
        first = stop
    return {**segment, 'route': rounded, 'pieces': pieces,
            'length': sum(piece['length'] for piece in pieces),
            'initial_yaw': pieces[0]['yaw'][0], 'final_yaw': pieces[-1]['yaw'][-1]}


def compile_timeline(locomotion, duration, blocked=None, bounds=None, fps=30):
    """Compile natural-language-derived movement segments without changing times.

    Position and yaw are continuous.  Adjacent, aligned move segments share a
    nonzero endpoint speed (including walk -> run); gaps and explicit idle stop
    movement.  Implicit turns consume the requesting move segment's own time.
    A request that cannot meet speed, acceleration, yaw-rate or route limits is
    rejected instead of being silently retimed or sliding sideways.
    """
    if not isinstance(locomotion, dict) or locomotion.get('version') != 1:
        raise ValueError('移动计划需要version=1')
    duration, fps = float(duration), int(fps)
    if not math.isfinite(duration) or duration <= 0 or fps <= 0:
        raise ValueError('移动时长和帧率需要为正数')
    initial = _xy(locomotion['start_position'])
    initial_yaw = math.radians(float(locomotion.get('start_yaw_deg', 0)))
    if not math.isfinite(initial_yaw):
        raise ValueError('移动起始朝向需要有限数值')
    free = _predicate(blocked, bounds)
    if not free(initial):
        raise ValueError('移动起点被障碍物或房间边界挡住')
    raw_segments = locomotion.get('segments')
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ValueError('移动计划需要至少一个segment')
    prepared, position, previous_end = [], initial, 0.
    frame_count = max(1, round(duration * fps))
    last_frame_time = (frame_count - 1) / fps
    for index, raw in enumerate(raw_segments):
        start, end = float(raw['start']), float(raw['end'])
        gait = raw.get('gait')
        if not all(math.isfinite(value) for value in (start, end)) or not 0 <= start < end <= duration + 1e-8:
            raise ValueError('移动segment时段必须位于总时长内且end大于start')
        if start < previous_end - 1e-8:
            raise ValueError('移动segment需要按时间排序且不能重叠')
        if gait not in ('walk', 'run', 'idle', 'turn'):
            raise ValueError('移动gait支持walk、run、idle或turn')
        segment = {**raw, 'index': index, 'start': start, 'end': end,
                   'effective_end': end,
                   'position': position}
        if segment['effective_end'] <= start:
            raise ValueError('移动时段短于一个可显示帧')
        if gait in ('walk', 'run'):
            points = raw.get('waypoints')
            if not isinstance(points, list) or not 2 <= len(points) <= 8:
                raise ValueError('walk/run需要2至8个waypoints')
            if _distance(_xy(points[0]), position) > 1e-4:
                raise ValueError('下一移动段的首个waypoint必须等于当前人物位置')
            segment = _prepare_move(segment, blocked, bounds)
            position = _xy(points[-1])
        elif gait == 'turn':
            target = math.radians(float(raw['end_yaw_deg']))
            if not math.isfinite(target):
                raise ValueError('转身终点朝向需要有限数值')
            segment['target_yaw'] = target
        prepared.append(segment)
        previous_end = end
    # Reserving one final frame reaches the target but does not lower the last
    # swinging foot.  Count existing trailing idle/gaps and reserve only the
    # missing recovery inside the final activity's original requested span.
    terminal_index = next((i for i in range(len(prepared) - 1, -1, -1)
                           if prepared[i]['gait'] != 'idle'), None)
    reserved_settle = 0.
    if terminal_index is not None:
        terminal = prepared[terminal_index]
        reserved_settle = max(0., FINAL_SETTLE_SECONDS - (last_frame_time - terminal['end']))
        terminal['effective_end'] = terminal['end'] - reserved_settle
        if terminal['effective_end'] <= terminal['start'] + 1e-9:
            raise ValueError('末端动作时段不足以完成动作并预留0.4秒收脚站稳，请延长动作时段')
    boundaries = [0.] * (len(prepared) + 1)
    # Sub-tolerance heading differences preserve the incoming yaw branch.  The
    # small residual can accumulate over several segments, so compare the next
    # route with the predicted *actual* yaw, not just the previous route tangent.
    final_headings, predicted_yaw = [], initial_yaw
    for segment in prepared:
        if segment['gait'] in ('walk', 'run'):
            delta = _angle_delta(predicted_yaw, segment['initial_yaw'])
            if len(segment['pieces']) == 1 and abs(delta) <= CONTINUOUS_HEADING_TOLERANCE:
                predicted_yaw += segment['final_yaw'] - segment['initial_yaw']
            else:
                predicted_yaw = segment['final_yaw']
        elif segment['gait'] == 'turn':
            predicted_yaw += _angle_delta(predicted_yaw, segment['target_yaw'])
        final_headings.append(predicted_yaw)
    for i in range(len(prepared) - 1):
        a, b = prepared[i:i + 2]
        if a['gait'] in ('walk', 'run') and b['gait'] in ('walk', 'run') and \
           abs(a['end'] - b['start']) < 1e-8 and \
           abs(_angle_delta(final_headings[i], b['initial_yaw'])) <= CONTINUOUS_HEADING_TOLERANCE:
            cap = min(WALK_SPEED if a['gait'] == 'walk' else RUN_SPEED,
                      WALK_SPEED if b['gait'] == 'walk' else RUN_SPEED)
            average = min(a['length'] / (a['effective_end'] - a['start']),
                          b['length'] / (b['effective_end'] - b['start']))
            boundaries[i + 1] = min(cap * .7, average * 1.25)
    intervals, metadata, warnings = [], [], []
    if reserved_settle > 1e-9:
        warnings.append(f'最后动作在原时段内提前{reserved_settle:.2f}秒完成，'
                        f'为最后可见帧预留{FINAL_SETTLE_SECONDS:.2f}秒收脚站稳；总时长保持不变')
    cursor, position, yaw, cumulative, turn_cumulative = 0., initial, initial_yaw, 0., 0.
    def hold(start, end, index=None):
        if end > start + 1e-10:
            intervals.append({'kind': 'idle', 'start': start, 'end': end,
                              'position': position, 'yaw': yaw, 'distance': cumulative,
                              'turn_distance': turn_cumulative, 'index': index})
    def turn(start, end, target, index):
        nonlocal yaw, turn_cumulative
        delta = _angle_delta(yaw, target)
        minimum = 1.5 * abs(delta) / YAW_RATE
        if end - start + 1e-6 < minimum:
            raise ValueError(f'转身时段太短，当前角度至少需要{minimum:.2f}秒')
        intervals.append({'kind': 'turn', 'start': start, 'end': end,
                          'position': position, 'yaw': yaw, 'delta': delta,
                          'distance': cumulative, 'turn_distance': turn_cumulative,
                          'index': index})
        yaw += delta
        turn_cumulative += TURN_STEP_RADIUS * abs(delta)
    for i, segment in enumerate(prepared):
        start, end, gait = segment['start'], segment['effective_end'], segment['gait']
        hold(cursor, start)
        if gait == 'idle':
            hold(start, end, i)
            metadata.append({'segment_index': i, 'gait': gait, 'start': start,
                             'end': segment['end'], 'executed_end': end, 'length_m': 0.})
        elif gait == 'turn':
            minimum = 1.5 * abs(_angle_delta(yaw, segment['target_yaw'])) / YAW_RATE
            turn(start, end, segment['target_yaw'], i)
            metadata.append({'segment_index': i, 'gait': gait, 'start': start,
                             'end': segment['end'], 'executed_end': end,
                             'minimum_seconds': minimum, 'length_m': 0.})
        else:
            pieces = segment['pieces']
            turns = []
            probe_yaw = yaw
            for piece in pieces:
                delta = _angle_delta(probe_yaw, piece['yaw'][0])
                seconds = 1.5 * abs(delta) / YAW_RATE if abs(delta) > CONTINUOUS_HEADING_TOLERANCE else 0.
                turns.append((delta, seconds))
                probe_yaw = piece['yaw'][-1]
            fast = [_fast_profile(piece, boundaries[i] if j == 0 else 0.,
                                  boundaries[i + 1] if j == len(pieces) - 1 else 0.)
                    for j, piece in enumerate(pieces)]
            minimum = sum(profile['duration'] for profile in fast) + sum(t[1] for t in turns)
            available = end - start
            if available + 1e-6 < minimum:
                raise ValueError(f'第{i + 1}段移动与安全转身至少需要{minimum:.2f}秒，给定{available:.2f}秒')
            slack = available - minimum
            local_cursor = start
            actual_turns = []
            last_arrival = start
            for j, piece in enumerate(pieces):
                delta, turn_seconds = turns[j]
                if turn_seconds:
                    if j == 0 and boundaries[i] > 1e-6:
                        raise ValueError('连续移动交界需要减速转身，不能保持速度侧向滑行')
                    turn(local_cursor, local_cursor + turn_seconds, piece['yaw'][0], i)
                    actual_turns.append({'start': local_cursor, 'end': local_cursor + turn_seconds,
                                         'angle_deg': math.degrees(delta)})
                    local_cursor += turn_seconds
                # Use the same unwrapped heading branch as the incoming actor.
                offset = yaw - piece['yaw'][0]
                aligned = {**piece, 'yaw': [value + offset for value in piece['yaw']]}
                requested = fast[j]['duration'] + slack * piece['length'] / segment['length']
                profile, pause = _profile_duration(aligned,
                    boundaries[i] if j == 0 else 0.,
                    boundaries[i + 1] if j == len(pieces) - 1 else 0., requested, gait)
                intervals.append({'kind': gait, 'start': local_cursor,
                                  'end': local_cursor + profile['duration'], 'profile': profile,
                                  'distance': cumulative, 'turn_distance': turn_cumulative,
                                  'index': i})
                local_cursor += profile['duration']
                last_arrival = local_cursor
                cumulative += piece['length']
                position, yaw = piece['points'][-1], aligned['yaw'][-1]
                if pause:
                    hold(local_cursor, local_cursor + pause, i)
                    warnings.append(f'第{i + 1}段跑步已完成，剩余{pause:.2f}秒在终点站稳，以保留真实跑速')
                    local_cursor += pause
            metadata.append({'segment_index': i, 'gait': gait, 'start': start,
                             'end': segment['end'], 'executed_end': end,
                             'minimum_seconds': minimum, 'length_m': segment['length'],
                             'route': [list(p) for p in segment['route']],
                             'requested_waypoints': segment['waypoints'],
                             'start_speed_m_s': boundaries[i], 'end_speed_m_s': boundaries[i + 1],
                             'turns': actual_turns, 'arrival_time': last_arrival})
        entry = metadata[-1]
        entry.update({'requested_start': segment['start'], 'requested_end': segment['end'],
                      'settle_seconds': 0., 'settle_reserved_in_segment_seconds': 0.})
        if i == terminal_index:
            arrival = entry.get('arrival_time', end)
            entry.update({'arrival_time': arrival, 'settle_start': arrival,
                          'settle_end': last_frame_time,
                          'settle_seconds': last_frame_time - arrival,
                          'settle_reserved_in_segment_seconds': reserved_settle})
        cursor = end
    hold(cursor, duration)
    frames, interval_index = [], 0
    for frame in range(1, frame_count + 1):
        seconds = (frame - 1) / fps
        while interval_index < len(intervals) - 1 and seconds >= intervals[interval_index]['end'] - 1e-10:
            interval_index += 1
        interval = intervals[interval_index]
        phase = max(0., min(1., (seconds - interval['start']) / max(1e-10, interval['end'] - interval['start'])))
        gait = interval['kind']
        speed = 0.
        distance, turn_distance = interval['distance'], interval['turn_distance']
        if gait in ('walk', 'run'):
            point, heading, speed, traveled = _sample_profile(interval['profile'], seconds - interval['start'])
            distance += traveled
            phase = traveled / interval['profile']['length']
        elif gait == 'turn':
            point = interval['position']
            heading = interval['yaw'] + interval['delta'] * _smooth(phase)
            turn_distance += TURN_STEP_RADIUS * abs(interval['delta']) * _smooth(phase)
        else:
            point, heading = interval['position'], interval['yaw']
        frames.append({'frame': frame, 'seconds': seconds, 'position': [*point, 0.],
                       'yaw': heading, 'speed': max(0., speed), 'distance': distance,
                       'turn_distance': turn_distance, 'gait': gait,
                       'segment_index': interval['index'], 'phase': phase})
    # Blender uses linear keys between rendered frames.  Verify those chords,
    # too, rather than accepting only the finer mathematical route samples.
    if not all(_line_free(a['position'][:2], b['position'][:2], free)
               for a, b in zip(frames, frames[1:])):
        raise ValueError('帧间插值会切过障碍物，请增加绕行净空或延长该移动时段')
    return {'frames': frames, 'segments': metadata, 'warnings': warnings,
            'limits': {'walk_speed_m_s': WALK_SPEED, 'run_speed_m_s': RUN_SPEED,
                       'acceleration_m_s2': ACCELERATION, 'yaw_rate_deg_s': 120,
                       'collision_sampling_step_m': CHECK_STEP},
            'distance_m': cumulative, 'turn_distance_m': turn_cumulative,
            'final_settle_seconds': 0. if terminal_index is None else metadata[terminal_index]['settle_seconds']}
