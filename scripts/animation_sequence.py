"""Build an uninterrupted Mixamo action sequence with overlapping NLA tracks."""


def sequence_actions(armature, actions, frame_start, frame_end, blend_frames=12):
    """Loop actions in equal segments and crossfade directly between their poses.

    Each incoming action occupies a higher NLA track and fades in over the
    outgoing action at full influence. Fading both strips independently would
    introduce the armature's rest pose into the result. The overlap is centered
    on each segment boundary and does not increase the requested video length.
    """
    total = frame_end - frame_start + 1
    if not actions or total < len(actions):
        raise ValueError("动画帧数必须至少覆盖每个动作一帧")
    if blend_frames < 0:
        raise ValueError("过渡帧数不能为负数")

    ad = armature.animation_data_create()
    ad.action = None
    ad.use_nla = True
    for track in list(ad.nla_tracks):
        ad.nla_tracks.remove(track)

    boundaries = [frame_start + total * i // len(actions)
                  for i in range(len(actions) + 1)]
    shortest_segment = min(b - a for a, b in zip(boundaries, boundaries[1:]))
    blend = min(int(blend_frames), shortest_segment // 2)
    before = blend // 2
    after = blend - before
    strips = []

    for i, action in enumerate(actions):
        start = boundaries[i] - (before if i else 0)
        end = boundaries[i + 1] + (after if i < len(actions) - 1 else 0)
        track = ad.nla_tracks.new()
        track.name = f"Sequence_{i + 1:02d}"
        strip = track.strips.new(name=action.name, start=start, action=action)
        # Imported actions may retain a slot named after a deleted duplicate
        # armature. Explicitly bind the sole Mixamo slot to the surviving rig.
        if len(action.slots) == 1:
            strip.action_slot = action.slots[0]

        action_start, action_end = action.frame_range
        if action_end <= action_start:
            action_end = action_start + 1
        strip.action_frame_start = action_start
        strip.action_frame_end = action_end
        strip.scale = 1.0
        # Blender computes frame_end from the actual action duration, which is
        # end - start, not the number of inclusive samples. Partial loops also
        # let long clips fit a short segment without colliding with the next.
        strip.repeat = (end - start) / (action_end - action_start)
        strip.extrapolation = "NOTHING"
        strip.blend_type = "REPLACE"
        strip.influence = 1.0
        strip.use_auto_blend = False
        strip.blend_in = blend if i else 0
        strip.blend_out = 0
        strips.append(strip)

    return strips
