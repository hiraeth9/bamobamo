"""Impact timing and persistent recoil: common controls, not paired animations."""
import math


def smooth(value):
    value = max(0.0, min(1.0, value))
    return value * value * (3 - 2 * value)


def strike_reach(event, seconds, limb, fps=30):
    # Align acceleration with the same sampled contact frame used by reaction and QA.
    peak = round(event['peak'] * fps) / fps
    # A sweeping hook needs a longer acceleration window than a straight punch.
    launch_seconds = .24 if limb == 'foot' or event.get('motion') == 'hook' else .18
    launch = max(event['start'] + .08, peak - launch_seconds)
    if seconds < launch:
        return 0.0
    if seconds <= peak:
        # Accelerate into contact instead of easing to zero speed at impact.
        return min(1.0, ((seconds - launch) / (peak - launch)) ** 1.65)
    hold = .055 if limb == 'foot' else .035
    if seconds <= peak + hold:
        return 1.0
    return 1.0 - smooth((seconds - peak - hold) / max(.08, event['end'] - peak - hold))


def reaction_kind(event):
    return event.get('reaction', 'stagger' if event['intensity'] >= .78 else 'recoil')


def response_amount(dt):
    if dt <= 0:
        return 0.0
    # Torso/head jerk within two frames, then settle; no long sine-wave wobble.
    return smooth(dt / .065) * math.exp(-max(0.0, dt - .065) / .20)


def step_distance(event):
    kind = reaction_kind(event)
    if kind == 'recoil':
        return 0.0
    intensity = event['intensity']
    gain = 1.15 if event['motion'] == 'kick' else 1.0
    return (.14 + .37 * intensity) * gain if kind == 'stagger' else .18 + .16 * intensity


def recoil_step_progress(event, seconds, fps=30):
    contact = round(event['peak'] * fps) / fps
    start = contact + .045
    duration = max(.12, min(.48, event['end'] - start))
    return smooth((seconds - start) / duration)
