"""Screen geometry for stacked waypoint markers and non-overlapping name tags."""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF


@dataclass
class Glyph:
    point: object
    index: int
    position: QPointF
    radius: float
    arrow_end: QPointF
    arrow_width: float
    head_size: float
    cluster: int


def glyphs(points, positions, vectors, detail=1.):
    # Complete-link screen clusters avoid joining an entire row through a chain
    # of close neighbours. Sizes depend on route order, never selection order.
    groups = []
    for index, position in enumerate(positions):
        for group in groups:
            if detail > 0 and all(math.hypot(position.x() - positions[j].x(), position.y() - positions[j].y()) <= 12 for j in group):
                group.append(index)
                break
        else:
            groups.append([index])
    result = [None] * len(points)
    for cluster, group in enumerate(groups):
        count = len(group)
        for rank, index in enumerate(group):
            layer = count - rank - 1
            radius = 5.5 + detail * (1 + layer * min(3.5, 10.5 / max(1, count - 1)))
            length = 18 + detail * (8 + layer * min(8., 24. / max(1, count - 1)))
            result[index] = Glyph(points[index], index, positions[index], radius,
                                  positions[index] + vectors[index] * length,
                                  2 + detail * min(1, layer * .2), 6 + detail * (1 + min(1.5, layer * .5)),
                                  cluster)
    return result


class RectIndex:
    def __init__(self):
        self.cells = {}

    @staticmethod
    def keys(rect):
        for x in range(math.floor(rect.left() / 32), math.floor(rect.right() / 32) + 1):
            for y in range(math.floor(rect.top() / 32), math.floor(rect.bottom() / 32) + 1):
                yield x, y

    def add(self, rect):
        for key in self.keys(rect):
            self.cells.setdefault(key, []).append(rect)

    def intersects(self, rect):
        return any(other.intersects(rect) for key in self.keys(rect) for other in self.cells.get(key, ()))


def handle_distance(items, position):
    lengths = [math.hypot((item.arrow_end - item.position).x(), (item.arrow_end - item.position).y())
               for item in items if math.hypot(item.position.x() - position.x(),
                                               item.position.y() - position.y()) <= 12]
    return max(43., max(lengths, default=26.) + 17)


def label_rects(items, metrics, viewport, preferred=()):
    """Right-side anchors determined by the route, independently of viewport edges.

    Pan translates tags with their points. Crowding hides tags instead of moving
    them around the map; close overlapping clusters have a short vertical stack.
    Selected tags take visibility priority. All collision decisions include the
    entire route, so entering/leaving the viewport cannot rearrange other tags.
    """
    markers = RectIndex()
    groups = {}
    for item in items:
        p, r = item.position, item.radius
        groups.setdefault(item.cluster, []).append(item)
        markers.add(QRectF(p.x() - r - 3, p.y() - r - 3, (r + 3) * 2, (r + 3) * 2))
        arrow = QRectF(p, item.arrow_end).normalized().adjusted(-item.head_size, -item.head_size,
                                                                item.head_size, item.head_size)
        markers.add(arrow)
    height = max(22, metrics.height() + 7)
    anchors = {}
    for group in groups.values():
        x = max(max(item.position.x() + item.radius + 9,
                    item.arrow_end.x() + item.head_size + 9) for item in group)
        cy = sum(item.position.y() for item in group) / len(group)
        for rank, item in enumerate(group):
            text = f"{item.index + 1} · {item.point.name}"
            y = cy + (rank - (len(group) - 1) / 2) * (height + 4) - height / 2
            anchors[item.point.uid] = QRectF(x, y, metrics.horizontalAdvance(text) + 14, height)
    occupied = RectIndex()
    result = {}
    available = viewport.adjusted(5, 5, -5, -5)
    hud = (QRectF(12, 12, 177, 33), QRectF(12, viewport.height() - 53, 134, 48))
    for item in sorted(items, key=lambda item: (item.point.uid not in preferred, item.index)):
        box = anchors[item.point.uid]
        # Selected points can use a modest extra right offset if another marker
        # covers their normal tag. Never move a tag left or seek distant slots.
        shifts = (0, 20, 40, 60) if item.point.uid in preferred else (0,)
        box = next((box.translated(shift, 0) for shift in shifts
                    if not markers.intersects(box.translated(shift, 0).adjusted(-2, -2, 2, 2))
                    and not occupied.intersects(box.translated(shift, 0).adjusted(-2, -2, 2, 2))), None)
        if box is None:
            continue
        occupied.add(box.adjusted(-2, -2, 2, 2))
        if available.contains(box) and not any(box.intersects(area) for area in hud):
            result[item.point.uid] = box
    return result


def segment_distance(position, start, end):
    delta = end - start
    squared = delta.x() ** 2 + delta.y() ** 2
    fraction = max(0., min(1., ((position.x() - start.x()) * delta.x() +
                              (position.y() - start.y()) * delta.y()) / squared)) if squared else 0.
    closest = start + delta * fraction
    return math.hypot(position.x() - closest.x(), position.y() - closest.y())
