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


def glyphs(points, positions, vectors):
    # Complete-link screen clusters avoid joining an entire row through a chain
    # of close neighbours. Sizes depend on route order, never selection order.
    groups = []
    for index, position in enumerate(positions):
        for group in groups:
            if all(math.hypot(position.x() - positions[j].x(), position.y() - positions[j].y()) <= 12 for j in group):
                group.append(index)
                break
        else:
            groups.append([index])
    result = [None] * len(points)
    for group in groups:
        count = len(group)
        for rank, index in enumerate(group):
            layer = count - rank - 1
            radius = 6.5 + layer * min(6., 42. / max(1, count - 1))
            length = 26 + layer * min(12., 84. / max(1, count - 1))
            result[index] = Glyph(points[index], index, positions[index], radius,
                                  positions[index] + vectors[index] * length,
                                  2 + min(2, layer * .4), 7 + min(5, layer * 1.5))
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


def label_rects(items, metrics, viewport):
    """Place tags near their markers, then use free screen space if necessary.

    Every returned rectangle is fully visible and disjoint from other tags,
    marker/arrow bounds and the map HUD. Crowded views omit tags that cannot fit
    instead of covering another tag; zooming exposes more space.
    """
    available = viewport.adjusted(5, 5, -5, -5)
    index = RectIndex()
    index.add(QRectF(12, 12, 177, 33))
    index.add(QRectF(12, viewport.height() - 53, 134, 48))
    visible = []
    for item in items:
        p, r = item.position, item.radius
        if not viewport.contains(p):
            continue
        visible.append(item)
        index.add(QRectF(p.x() - r - 4, p.y() - r - 4, (r + 4) * 2, (r + 4) * 2))
        arrow = QRectF(p, item.arrow_end).normalized().adjusted(-item.head_size, -item.head_size,
                                                                item.head_size, item.head_size)
        index.add(arrow)
        vector = item.arrow_end - p
        length = math.hypot(vector.x(), vector.y())
        handle = p + vector / length * handle_distance(items, p) if length else p
        index.add(QRectF(handle.x() - 11, handle.y() - 11, 22, 22))
    result = {}
    height = max(22, metrics.height() + 7)
    for item in visible:
        text = f"{item.index + 1} · {item.point.name}"
        width = metrics.horizontalAdvance(text) + 14
        p, r = item.position, item.radius
        def candidates():
            for band in range(7):
                gap = r + 10 + band * 24
                for shift in (0, -1, 1, -2, 2, -3, 3, -4, 4):
                    y = p.y() - height / 2 + shift * (height + 5)
                    yield QRectF(p.x() + gap, y, width, height)
                    yield QRectF(p.x() - gap - width, y, width, height)
                yield QRectF(p.x() - width / 2, p.y() - gap - height, width, height)
                yield QRectF(p.x() - width / 2, p.y() + gap, width, height)
        box = next((candidate for candidate in candidates()
                    if available.contains(candidate) and not index.intersects(candidate.adjusted(-2, -2, 2, 2))), None)
        if box is None:
            # A finite grid fallback keeps arbitrarily crowded views bounded.
            slots = [QRectF(x, y, width, height)
                     for y in range(6, max(6, int(viewport.height() - height - 5)), height + 5)
                     for x in range(6, max(6, int(viewport.width() - width - 5)), 20)]
            slots.sort(key=lambda candidate: math.hypot(candidate.center().x() - p.x(), candidate.center().y() - p.y()))
            box = next((candidate for candidate in slots if not index.intersects(candidate.adjusted(-2, -2, 2, 2))), None)
        if box is not None:
            result[item.point.uid] = box
            index.add(box.adjusted(-2, -2, 2, 2))
    return result


def segment_distance(position, start, end):
    delta = end - start
    squared = delta.x() ** 2 + delta.y() ** 2
    fraction = max(0., min(1., ((position.x() - start.x()) * delta.x() +
                              (position.y() - start.y()) * delta.y()) / squared)) if squared else 0.
    closest = start + delta * fraction
    return math.hypot(position.x() - closest.x(), position.y() - closest.y())
