"""Ground-coordinate group transforms, route headings and chamber geometry."""
from __future__ import annotations

import math
from dataclasses import dataclass

EPSILON = 1e-7
AHEAD = .2
SIDE_OFFSET = 2.0


@dataclass(frozen=True)
class Chamber:
    uid: str
    side: str
    x: float
    y: float


def chambers(points):
    result = []
    for point in points:
        if point.kind != "Measure":
            continue
        for side, sign in (("left", -1), ("right", 1)):
            if point.side in (side, "both"):
                result.append(Chamber(point.uid, side,
                                      point.x + AHEAD * math.cos(point.angle) + sign * SIDE_OFFSET * math.sin(point.angle),
                                      point.y + AHEAD * math.sin(point.angle) - sign * SIDE_OFFSET * math.cos(point.angle)))
    return result


def centre(points):
    if not points:
        raise ValueError("Select at least one waypoint.")
    return sum(p.x for p in points) / len(points), sum(p.y for p in points) / len(points)


def transform(points, uids, dx=0., dy=0., angle=0., pivot=None, baseline=None):
    """Rigid transform from a stable baseline, avoiding cumulative drag error."""
    selected = [p for p in (baseline if baseline is not None else points) if p.uid in uids]
    if not selected:
        return
    originals = {p.uid: (p.x, p.y, p.angle) for p in selected}
    cx, cy = pivot if pivot is not None else centre(selected)
    c, s = math.cos(angle), math.sin(angle)
    for point in points:
        if point.uid in originals:
            x, y, heading = originals[point.uid]
            if angle == 0:
                point.x, point.y, point.angle = x + dx, y + dy, heading
            else:
                point.x = cx + (x - cx) * c - (y - cy) * s + dx
                point.y = cy + (x - cx) * s + (y - cy) * c + dy
                point.angle = heading + angle


def autorotate(points, uids):
    """Use incoming travel for DriveThrough, outgoing travel for plots/turns."""
    changed = 0
    for index, point in enumerate(points):
        if point.uid not in uids or point.kind not in ("DriveThrough", "Measure", "TurningPoint"):
            continue
        incoming = point.kind == "DriveThrough"
        preferred = range(index - 1, -1, -1) if incoming else range(index + 1, len(points))
        fallback = range(index + 1, len(points)) if incoming else range(index - 1, -1, -1)
        for sequence in (preferred, fallback):
            for other_index in sequence:
                other = points[other_index]
                # Both directions describe travel along route order, never reverse driving.
                dx, dy = (point.x - other.x, point.y - other.y) if other_index < index else (other.x - point.x, other.y - point.y)
                if math.hypot(dx, dy) > EPSILON:
                    heading = math.atan2(dy, dx)
                    if abs(math.atan2(math.sin(heading - point.angle), math.cos(heading - point.angle))) > 1e-12:
                        point.angle = heading
                        changed += 1
                    break
            else:
                continue
            break
    return changed


def snap_positions(points, uids, distance=None):
    """Keep all route rows; bring selected positions to their group average."""
    selected = [p for p in points if p.uid in uids]
    if len(selected) < 2:
        return 0
    if distance is not None and (not math.isfinite(distance) or distance <= 0):
        raise ValueError("Snap distance must be positive.")
    groups = []
    remaining = set(range(len(selected)))
    while remaining:
        first = min(remaining)
        remaining.remove(first)
        group = [first]
        if distance is None:
            group.extend(sorted(remaining))
            remaining.clear()
        else:
            # Complete-link groups prevent a chain of close points from collapsing
            # a long row whose endpoints are farther apart than the snap distance.
            for index in sorted(remaining):
                point = selected[index]
                if all(math.hypot(point.x - selected[j].x, point.y - selected[j].y) <= distance for j in group):
                    group.append(index)
            remaining.difference_update(group)
        groups.append([selected[i] for i in group])
    count = 0
    for group in groups:
        if len(group) < 2:
            continue
        cx, cy = centre(group)
        for point in group:
            point.x, point.y = cx, cy
        count += len(group)
    return count


def snap_chambers(points, uids, distance, side="closest"):
    """Fit matched chamber bars to a common centre and average orientation.

    Two both-sided waypoints align all four chambers, retaining the fixed physical
    spacing. Cross-side matches preserve opposite driving directions. A single-side
    match aligns the active pair at its midpoint with the same orientation rule.
    """
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError("Snap distance must be positive.")
    if side not in ("closest", "left", "right", "opposite"):
        raise ValueError("Unknown chamber pairing mode.")
    candidates = chambers([p for p in points if p.uid in uids])
    if side in ("left", "right"):
        candidates = [c for c in candidates if c.side == side]
    pairs = []
    for index, a in enumerate(candidates):
        for b in candidates[index + 1:]:
            if a.uid == b.uid or (side == "opposite" and a.side == b.side):
                continue
            gap = math.hypot(a.x - b.x, a.y - b.y)
            if gap <= distance:
                pairs.append((gap, index, a, b))
    pairs.sort(key=lambda pair: (pair[0], pair[1]))
    by_id = {p.uid: p for p in points}
    used = set()
    matched = []
    for _, _, a, b in pairs:
        if a.uid in used or b.uid in used:
            continue
        first, second = by_id[a.uid], by_id[b.uid]
        opposite = a.side != b.side
        corresponding_angle = second.angle + (math.pi if opposite else 0)
        difference = math.atan2(math.sin(corresponding_angle - first.angle),
                                math.cos(corresponding_angle - first.angle))
        mean_angle = first.angle + difference / 2
        fitted_angles = (mean_angle, mean_angle - (math.pi if opposite else 0))
        if first.side == second.side == "both":
            # The mean of the two chamber-bar centres, before either pose changes.
            mx = (first.x + AHEAD * math.cos(first.angle) + second.x + AHEAD * math.cos(second.angle)) / 2
            my = (first.y + AHEAD * math.sin(first.angle) + second.y + AHEAD * math.sin(second.angle)) / 2
            for point, angle in zip((first, second), fitted_angles):
                point.angle = math.atan2(math.sin(angle), math.cos(angle))
                point.x = mx - AHEAD * math.cos(point.angle)
                point.y = my - AHEAD * math.sin(point.angle)
            sign = -1 if a.side == "left" else 1
            target = (mx + sign * SIDE_OFFSET * math.sin(first.angle),
                      my - sign * SIDE_OFFSET * math.cos(first.angle))
        else:
            mx, my = (a.x + b.x) / 2, (a.y + b.y) / 2
            for point, chamber, angle in zip((first, second), (a, b), fitted_angles):
                point.angle = math.atan2(math.sin(angle), math.cos(angle))
                sign = -1 if chamber.side == "left" else 1
                point.x = mx - AHEAD * math.cos(point.angle) - sign * SIDE_OFFSET * math.sin(point.angle)
                point.y = my - AHEAD * math.sin(point.angle) + sign * SIDE_OFFSET * math.cos(point.angle)
            target = mx, my
        used.update((a.uid, b.uid))
        matched.append((a, b, target))
    return matched


def field_angle(coords):
    """Orientation of the minimum-area bounding rectangle, like the original reviewer."""
    vertices = sorted(set(coords))
    if len(vertices) < 2:
        return 0.
    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    lower, upper = [], []
    for point in vertices:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    for point in reversed(vertices):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    hull = lower[:-1] + upper[:-1]
    best = (math.inf, 0.)
    ox, oy = vertices[0]
    local = [(x - ox, y - oy) for x, y in hull]
    for a, b in zip(hull, hull[1:] + hull[:1]):
        angle = math.atan2(b[1] - a[1], b[0] - a[0])
        c, s = math.cos(angle), math.sin(angle)
        xs = [x * c + y * s for x, y in local]
        ys = [-x * s + y * c for x, y in local]
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if area < best[0]:
            best = area, angle
    return best[1]


def measurement_squares(points, fixed_size=None):
    """Squares around active chambers, in metres, not screen marker boxes."""
    if fixed_size is not None and (not math.isfinite(fixed_size) or fixed_size <= 0):
        raise ValueError("Square size must be positive.")
    items = chambers(points)
    coords = [(ch.x, ch.y) for ch in items]
    angle = field_angle(coords)
    c, s = math.cos(angle), math.sin(angle)
    result = []
    for index, chamber in enumerate(items):
        distances = [math.hypot(chamber.x - other.x, chamber.y - other.y)
                     for j, other in enumerate(items) if j != index]
        distances = [d for d in distances if d > EPSILON]
        half = fixed_size / 2 if fixed_size is not None else min(distances) / 2 if distances else 1.
        corners = [(chamber.x + dx * c - dy * s, chamber.y + dx * s + dy * c)
                   for dx, dy in ((-half, -half), (half, -half), (half, half), (-half, half))]
        result.append((chamber, corners))
    return result
