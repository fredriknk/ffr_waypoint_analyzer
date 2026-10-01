"""An offline map canvas with screen-sized waypoint editing handles."""
from __future__ import annotations

import math
from collections import OrderedDict
from types import SimpleNamespace

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from .geo import HALF_WORLD, WORLD, Projection, TileSet
from .geometry import centre, chambers, measurement_footprints
from .map_layout import glyphs, handle_distance, label_rects, segment_distance

COLORS = {"Measure": "#62e7b5", "DriveThrough": "#69b7ff",
          "TurningPoint": "#ffbd69", "Stop": "#ff7e99"}


class MapView(QGraphicsView):
    selected = Signal(str)
    selection_requested = Signal(str, str)
    box_selected = Signal(list, bool)
    add_requested = Signal(float, float)
    gesture_started = Signal(str)
    moved = Signal(str, float, float)
    rotated = Signal(str, float)
    gesture_finished = Signal()
    gesture_cancelled = Signal()
    cursor_coordinates = Signal(float, float)

    def __init__(self, projection: Projection, tiles: TileSet | None, parent=None):
        super().__init__(parent)
        self.projection = projection
        self.tiles = tiles
        self.points = []
        self.selected_uid = None
        self.selected_uids = set()
        self.show_labels = True
        self.show_route = True
        self.show_chambers = False
        self.show_headings = True
        self.show_squares = False
        self.square_size = None
        self.rectangle_size = None
        self.allow_heading_edit = True
        self.allow_group_rotation = True
        self.add_mode = False
        self.edit_mode = "move"
        self._gesture = None
        self._pan = None
        self._rubber = None
        self._squares_key = None
        self._squares = []
        self._layout_key = None
        self._glyphs = []
        self._labels = {}
        self._label_font = QFont("Segoe UI", 9)
        self._tile_cache = OrderedDict()
        self._location_cache = OrderedDict()
        self._drawn_tiles = 0
        self.setScene(QGraphicsScene(self))
        self.setSceneRect(-HALF_WORLD, -HALF_WORLD, WORLD, WORLD)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scale(.7, .7)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if tiles:
            self.fitInView(QRectF(*tiles.bounds()), Qt.AspectRatioMode.KeepAspectRatio)

    def set_tiles(self, tiles: TileSet):
        self.tiles = tiles
        self._tile_cache.clear()
        self._location_cache.clear()
        self.viewport().update()

    def screen(self, x, y):
        return self.viewportTransform().map(QPointF(*self.projection.scene(x, y)))

    def heading_vector(self, point):
        origin = self.screen(point.x, point.y)
        end = self.viewportTransform().map(QPointF(*self.projection.heading(point)))
        dx, dy = end.x() - origin.x(), end.y() - origin.y()
        length = math.hypot(dx, dy)
        if length < .1:
            # Use projected coordinates if the screen-rounded points coincide.
            a = self.projection.scene(point.x, point.y)
            b = self.projection.heading(point)
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
        return QPointF(dx / length, dy / length) if length else QPointF(1, 0)

    def handle(self, point):
        self._ensure_layout()
        pivot = self.rotation_point(point)
        origin, vector = self.screen(pivot.x, pivot.y), self.heading_vector(pivot)
        distance = handle_distance(self._glyphs, origin)
        # A group's centre may lie between its members' labels. Keep its
        # rotation control clear of every visible tag as well as stacked arrows.
        for _ in range(20):
            position = origin + vector * distance
            bounds = QRectF(position.x() - 11, position.y() - 11, 22, 22)
            if not any(bounds.intersects(box) for uid, box in self._labels.items()
                       if self.show_labels or uid in self.selected_uids or uid == self.selected_uid):
                return position
            distance += 22
        return origin + vector * distance

    def selected_points(self):
        return [p for p in self.points if p.uid in self.selected_uids or p.uid == self.selected_uid]

    def rotation_point(self, primary):
        selected = self.selected_points()
        if len(selected) > 1:
            x, y = centre(selected)
            return SimpleNamespace(x=x, y=y, angle=primary.angle)
        return primary

    def can_rotate(self):
        return self.allow_heading_edit or (self.allow_group_rotation and len(self.selected_points()) > 1)

    def fit_selection(self):
        selected = self.selected_points()
        if len(selected) == 1:
            self.focus_waypoint(selected[0])
        elif selected:
            coords = [self.projection.scene(p.x, p.y) for p in selected]
            xs, ys = zip(*coords)
            margin = max(8, max(max(xs) - min(xs), max(ys) - min(ys)) * .15)
            self.fitInView(QRectF(min(xs) - margin, min(ys) - margin,
                                 max(xs) - min(xs) + margin * 2, max(ys) - min(ys) + margin * 2),
                           Qt.AspectRatioMode.KeepAspectRatio)

    def fit_route(self):
        if not self.points:
            if self.tiles:
                self.fitInView(QRectF(*self.tiles.bounds()), Qt.AspectRatioMode.KeepAspectRatio)
            return
        coords = [self.projection.scene(p.x, p.y) for p in self.points]
        xs, ys = zip(*coords)
        rect = QRectF(min(xs), min(ys), max(20, max(xs) - min(xs)), max(20, max(ys) - min(ys)))
        padding = max(12, max(rect.width(), rect.height()) * .12)
        self.fitInView(rect.adjusted(-padding, -padding, padding, padding), Qt.AspectRatioMode.KeepAspectRatio)
        self.viewport().update()

    def focus_waypoint(self, point):
        self.centerOn(QPointF(*self.projection.scene(point.x, point.y)))
        if self.transform().m11() < 3:
            self.scale(3 / self.transform().m11(), 3 / self.transform().m11())

    def zoom(self, factor, anchor=None):
        anchor = anchor or self.viewport().rect().center()
        before = self.mapToScene(anchor)
        scale = self.transform().m11()
        minimum = 256 * 2**((self.tiles.levels[0] if self.tiles else 15) - 2) / WORLD
        maximum = 256 * 2**((self.tiles.levels[-1] if self.tiles else 21) + 3) / WORLD
        target = max(minimum, min(maximum, scale * factor))
        self.scale(target / scale, target / scale)
        delta = self.mapToScene(anchor) - before
        self.centerOn(self.mapToScene(self.viewport().rect().center()) - delta)
        self.viewport().update()

    def wheelEvent(self, event):
        self.zoom(1.25 ** (event.angleDelta().y() / 120), event.position().toPoint())
        event.accept()

    def _tile(self, z, x, y):
        key = (z, x, y)
        if key not in self._location_cache:
            self._location_cache[key] = self.tiles.find(z, x, y)
            if len(self._location_cache) > 8192:
                self._location_cache.popitem(last=False)
        else:
            self._location_cache.move_to_end(key)
        found = self._location_cache[key]
        if not found:
            return None
        path, factor, sx, sy = found
        if path not in self._tile_cache:
            self._tile_cache[path] = QPixmap(str(path))
            if len(self._tile_cache) > 384:
                self._tile_cache.popitem(last=False)
        else:
            self._tile_cache.move_to_end(path)
        pixmap = self._tile_cache[path]
        if pixmap.isNull():
            return None
        return pixmap, QRectF(sx * pixmap.width() / factor, sy * pixmap.height() / factor,
                             pixmap.width() / factor, pixmap.height() / factor)

    def drawBackground(self, painter, rect):
        painter.fillRect(rect, QColor("#17242b"))
        self._drawn_tiles = 0
        if not self.tiles:
            return
        ideal = math.ceil(math.log2(self.transform().m11() * WORLD / 256))
        z = max(self.tiles.levels[0], min(self.tiles.levels[-1], ideal))
        size = WORLD / 2**z
        xmin = max(0, math.floor((rect.left() + HALF_WORLD) / size))
        xmax = min(2**z - 1, math.floor((rect.right() + HALF_WORLD) / size))
        ymin = max(0, math.floor((rect.top() + HALF_WORLD) / size))
        ymax = min(2**z - 1, math.floor((rect.bottom() + HALF_WORLD) / size))
        for x in range(xmin, xmax + 1):
            for y in range(ymin, ymax + 1):
                tile = self._tile(z, x, y)
                if tile:
                    painter.drawPixmap(QRectF(x * size - HALF_WORLD, y * size - HALF_WORLD, size, size), *tile)
                    self._drawn_tiles += 1

    @staticmethod
    def _arrow(painter, start, end, color, width=2, head_size=7):
        painter.setPen(QPen(QColor(color), width))
        painter.drawLine(start, end)
        d = end - start
        length = math.hypot(d.x(), d.y())
        if length < 1:
            return
        d /= length
        normal = QPointF(-d.y(), d.x())
        painter.setBrush(QColor(color))
        painter.drawPolygon(QPolygonF([end, end - d * head_size + normal * head_size / 2,
                                      end - d * head_size - normal * head_size / 2]))

    def _ensure_layout(self):
        transform = self.viewportTransform()
        key = (self.viewport().width(), self.viewport().height(), transform.m11(),
               transform.dx(), transform.dy(), id(self.projection),
               tuple((id(p), p.uid, p.x, p.y, p.angle, p.name) for p in self.points),
               frozenset(self.selected_uids), self.selected_uid)
        if key != self._layout_key:
            # Use a fixed ground anchor, so panning cannot affect the zoom
            # detail level through the projection's varying local scale.
            a = (QPointF(*self.projection.scene(self.points[0].x, self.points[0].y)) if self.points
                 else self.mapToScene(self.viewport().rect().center()))
            b = a + QPointF(100 / transform.m11(), 0)
            ax, ay = self.projection.coordinates(a.x(), a.y())
            bx, by = self.projection.coordinates(b.x(), b.y())
            pixels_per_metre = 100 / max(1e-9, math.hypot(bx - ax, by - ay))
            # Compact overview, gradually revealing separate overlapping handles
            # only when the ground scale is suitable for individual pose editing.
            detail = max(0., min(1., (pixels_per_metre - 14) / 14))
            self._glyphs = glyphs(self.points, [self.screen(p.x, p.y) for p in self.points],
                                  [self.heading_vector(p) for p in self.points], detail)
            self._labels = label_rects(self._glyphs, QFontMetrics(self._label_font),
                                      QRectF(self.viewport().rect()), self.selected_uids | {self.selected_uid})
            self._layout_key = key

    def _label_visible(self, point):
        return self.show_labels or point.uid in self.selected_uids or point.uid == self.selected_uid

    def drawForeground(self, painter, rect):
        painter.save()
        painter.resetTransform()
        painter.setFont(self._label_font)
        self._ensure_layout()
        positions = [item.position for item in self._glyphs]
        viewport = QRectF(self.viewport().rect()).adjusted(-120, -120, 120, 120)
        if self.show_squares:
            key = (self.square_size, self.rectangle_size,
                   tuple((p.x, p.y, p.angle, p.kind, p.side) for p in self.points))
            if key != self._squares_key:
                self._squares = measurement_footprints(self.points, self.square_size, self.rectangle_size)
                self._squares_key = key
            painter.setBrush(QColor(98, 231, 181, 16))
            painter.setPen(QPen(QColor(98, 231, 181, 210), 1.3, Qt.PenStyle.DashLine))
            for chamber, corners in self._squares:
                polygon = QPolygonF([self.screen(x, y) for x, y in corners])
                painter.drawPolygon(polygon)
        if self.show_route and len(positions) > 1:
            path = QPainterPath(positions[0])
            for position in positions[1:]:
                path.lineTo(position)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 160), 5))
            painter.drawPath(path)
            painter.setPen(QPen(QColor(205, 233, 249, 185), 2))
            painter.drawPath(path)
            for a, b in zip(positions, positions[1:]):
                delta = b - a
                length = math.hypot(delta.x(), delta.y())
                if length > 65:
                    mid = (a + b) / 2
                    self._arrow(painter, mid - delta / length * 5, mid + delta / length * 5, "#d2e8ef", 1)
        # Chambers are a separate layer. Reusing the marker loop variable here
        # previously replaced a Glyph with a Chamber and aborted the redraw.
        if self.show_chambers:
            painter.setPen(QPen(QColor(COLORS["Measure"]), 1))
            painter.setBrush(QColor(98, 231, 181, 110))
            for chamber in chambers(self.points):
                position = self.screen(chamber.x, chamber.y)
                if viewport.contains(position):
                    painter.drawRect(QRectF(position.x() - 3, position.y() - 3, 6, 6))
        # Leader lines make displaced, collision-free tags unambiguous.
        for item in self._glyphs:
            box = self._labels.get(item.point.uid)
            if box is not None and self._label_visible(item.point):
                end = QPointF(max(box.left(), min(box.right(), item.position.x())),
                              max(box.top(), min(box.bottom(), item.position.y())))
                painter.setPen(QPen(QColor(COLORS[item.point.kind]), 1))
                painter.drawLine(item.position, end)
        # Route order is also paint order: larger early markers sit underneath
        # the smaller later markers, leaving separate rings and arrow tips.
        for item in self._glyphs:
            point, position = item.point, item.position
            if not viewport.contains(position):
                continue
            selected = point.uid in self.selected_uids or point.uid == self.selected_uid
            color = COLORS[point.kind]
            if selected:
                painter.setPen(QPen(QColor("#ffffff"), 2))
                painter.setBrush(QColor(255, 255, 255, 35))
                painter.drawEllipse(position, item.radius + 4, item.radius + 4)
            if self.show_headings or selected:
                self._arrow(painter, position, item.arrow_end, color, item.arrow_width, item.head_size)
            painter.setPen(QPen(QColor("#0a1720"), 2))
            painter.setBrush(QColor(color))
            painter.drawEllipse(position, item.radius, item.radius)
        for item in self._glyphs:
            point = item.point
            box = self._labels.get(point.uid)
            if box is None or not self._label_visible(point):
                continue
            selected = point.uid in self.selected_uids or point.uid == self.selected_uid
            painter.setPen(QPen(QColor("#ffffff" if selected else COLORS[point.kind]), 1))
            painter.setBrush(QColor(12, 23, 30, 235))
            painter.drawRoundedRect(box, 4, 4)
            painter.setPen(QColor("#ffffff" if selected else "#e3ebed"))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, f"{item.index + 1} · {point.name}")
        primary = next((p for p in self.points if p.uid == self.selected_uid), None)
        if primary and self.can_rotate():
            pivot = self.rotation_point(primary)
            origin, handle = self.screen(pivot.x, pivot.y), self.handle(primary)
            painter.setPen(QPen(QColor("#ffffff"), 1.5, Qt.PenStyle.DashLine))
            painter.drawLine(origin, handle)
            painter.setBrush(QColor("#182a33"))
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.drawEllipse(handle, 5, 5)
            if len(self.selected_points()) > 1:
                painter.drawLine(origin - QPointF(5, 0), origin + QPointF(5, 0))
                painter.drawLine(origin - QPointF(0, 5), origin + QPointF(0, 5))
        if self._rubber:
            start, end, _ = self._rubber
            painter.setPen(QPen(QColor("#62e7b5"), 1, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(98, 231, 181, 35))
            painter.drawRect(QRectF(start, end).normalized())
        painter.setPen(QColor("#eff8ff"))
        painter.setBrush(QColor(12, 23, 30, 215))
        painter.drawRoundedRect(QRectF(14, 14, 172, 29), 5, 5)
        painter.drawText(QRectF(14, 14, 172, 29), Qt.AlignmentFlag.AlignCenter, "↑ N   ·   LOCAL IMAGERY")
        if not self._drawn_tiles:
            message = "No local imagery here · check the map folder or coordinate system"
            painter.drawText(QRectF(0, 55, self.viewport().width(), 30), Qt.AlignmentFlag.AlignCenter, message)
        if self.add_mode:
            painter.fillRect(QRectF(0, 0, self.viewport().width(), 4), QColor("#62e7b5"))
        self._scale_bar(painter)
        painter.restore()

    def _scale_bar(self, painter):
        c = self.viewport().rect().center()
        a, b = self.mapToScene(c), self.mapToScene(c + QPoint(100, 0))
        # Projecting two nearby screen points gives scale in ground metres.
        try:
            ax, ay = self.projection.coordinates(a.x(), a.y())
            bx, by = self.projection.coordinates(b.x(), b.y())
            metres = math.hypot(bx - ax, by - ay)
            if metres <= 0 or not math.isfinite(metres):
                return
            magnitude = 10 ** math.floor(math.log10(metres))
            distance = max(v * magnitude for v in (1, 2, 5) if v * magnitude <= metres)
            width = distance / metres * 100
            y = self.viewport().height() - 23
            painter.setBrush(QColor(12, 23, 30, 220))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(14, y - 25, max(110, width + 24), 43), 5, 5)
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.drawLine(QPointF(25, y), QPointF(25 + width, y))
            painter.drawLine(QPointF(25, y - 4), QPointF(25, y + 4))
            painter.drawLine(QPointF(25 + width, y - 4), QPointF(25 + width, y + 4))
            painter.drawText(QPointF(25, y - 8), f"{distance:g} m")
        except (ValueError, RuntimeError):
            pass

    def _hit_label(self, pos):
        self._ensure_layout()
        return next((item.point for item in self._glyphs
                     if self._label_visible(item.point) and item.point.uid in self._labels
                     and self._labels[item.point.uid].contains(pos)), None)

    def _hit(self, pos):
        label = self._hit_label(pos)
        if label:
            return label
        # Match the visible topmost shape, including exposed outer rings and
        # arrowheads, rather than only measuring distance to a shared centre.
        for item in reversed(self._glyphs):
            delta = pos - item.position
            if math.hypot(delta.x(), delta.y()) <= item.radius + 1:
                return item.point
            point = item.point
            if self.show_headings or point.uid in self.selected_uids or point.uid == self.selected_uid:
                if segment_distance(pos, item.position, item.arrow_end) <= item.arrow_width / 2 + 1.5:
                    return point
                direction = item.arrow_end - item.position
                direction /= math.hypot(direction.x(), direction.y())
                normal = QPointF(-direction.y(), direction.x())
                head = QPolygonF([item.arrow_end,
                                  item.arrow_end - direction * item.head_size + normal * item.head_size / 2,
                                  item.arrow_end - direction * item.head_size - normal * item.head_size / 2])
                if head.containsPoint(pos, Qt.FillRule.OddEvenFill):
                    return point
        return None

    def mousePressEvent(self, event):
        self.setFocus()
        pos = event.position()
        if event.button() == Qt.MouseButton.RightButton:
            self._pan = pos.toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.button() == Qt.MouseButton.LeftButton:
            if self.add_mode:
                scene = self.mapToScene(pos.toPoint())
                x, y = self.projection.coordinates(scene.x(), scene.y())
                self.add_requested.emit(x, y)
                return
            selected = next((p for p in self.points if p.uid == self.selected_uid), None)
            label = self._hit_label(pos)
            handle_hit = selected and self.can_rotate() and math.hypot((self.handle(selected) - pos).x(), (self.handle(selected) - pos).y()) < 10
            point = label or (selected if handle_hit else self._hit(pos))
            if point:
                modifiers = event.modifiers()
                if modifiers & Qt.KeyboardModifier.ControlModifier:
                    self.selection_requested.emit(point.uid, "toggle")
                    return
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    self.selection_requested.emit(point.uid, "range")
                    return
                self.selection_requested.emit(point.uid, "replace" if label else "preserve")
                if label:
                    return
                if self.edit_mode == "select":
                    return
                if self.edit_mode == "rotate" and not self.can_rotate():
                    return
                self.gesture_started.emit(point.uid)
                scene = self.mapToScene(pos.toPoint())
                original = QPointF(*self.projection.scene(point.x, point.y))
                pivot = self.rotation_point(point)
                x, y = self.projection.coordinates(scene.x(), scene.y())
                self._gesture = {"mode": "rotate" if handle_hit or self.edit_mode == "rotate" else "move",
                                 "uid": point.uid, "offset": scene - original, "pivot": (pivot.x, pivot.y),
                                 "heading": point.angle, "start_angle": math.atan2(y - pivot.y, x - pivot.x),
                                 "relative_rotation": len(self.selected_points()) > 1 and not handle_hit}
                self.setCursor(Qt.CursorShape.CrossCursor if self._gesture["mode"] == "rotate" else Qt.CursorShape.SizeAllCursor)
            else:
                if self.edit_mode == "select" or event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self._rubber = (pos, pos, bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
                    self.setCursor(Qt.CursorShape.CrossCursor)
                else:
                    self._pan = pos.toPoint()
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        scene = self.mapToScene(event.position().toPoint())
        try:
            x, y = self.projection.coordinates(scene.x(), scene.y())
            self.cursor_coordinates.emit(x, y)
            if self._gesture:
                mode, uid, offset = (self._gesture[key] for key in ("mode", "uid", "offset"))
                if mode == "move":
                    location = scene - offset
                    x, y = self.projection.coordinates(location.x(), location.y())
                    self.moved.emit(uid, x, y)
                else:
                    cx, cy = self._gesture["pivot"]
                    if math.hypot(x - cx, y - cy) > .01:
                        angle = math.atan2(y - cy, x - cx)
                        if self._gesture["relative_rotation"]:
                            delta = angle - self._gesture["start_angle"]
                            angle = self._gesture["heading"] + math.atan2(math.sin(delta), math.cos(delta))
                        self.rotated.emit(uid, angle)
            elif self._rubber:
                self._rubber = (self._rubber[0], event.position(), self._rubber[2])
                self.viewport().update()
            elif self._pan is not None:
                delta = self.mapToScene(self._pan) - scene
                self.centerOn(self.mapToScene(self.viewport().rect().center()) + delta)
                self._pan = event.position().toPoint()
            else:
                selected = next((p for p in self.points if p.uid == self.selected_uid), None)
                handle_hit = selected and self.can_rotate() and math.hypot((self.handle(selected) - event.position()).x(),
                                                     (self.handle(selected) - event.position()).y()) < 10
                self.setCursor(Qt.CursorShape.CrossCursor if self.add_mode or handle_hit else
                               Qt.CursorShape.PointingHandCursor if self._hit_label(event.position()) else
                               Qt.CursorShape.SizeAllCursor if self._hit(event.position()) else Qt.CursorShape.OpenHandCursor)
        except (ValueError, RuntimeError):
            pass
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._rubber:
            start, end, additive = self._rubber
            rect = QRectF(start, end).normalized()
            uids = [p.uid for p in self.points if rect.contains(self.screen(p.x, p.y))]
            self._rubber = None
            self.box_selected.emit(uids, additive)
            self.viewport().update()
        if self._gesture:
            self._gesture = None
            self.gesture_finished.emit()
        self._pan = None
        self.setCursor(Qt.CursorShape.CrossCursor if self.add_mode else Qt.CursorShape.OpenHandCursor)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        point = self._hit(event.position())
        if point:
            self.selected.emit(point.uid)
            self.focus_waypoint(point)
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self._gesture:
                self._gesture = None
                self.gesture_cancelled.emit()
            self._rubber = None
            self.viewport().update()
            event.accept()
        else:
            super().keyPressEvent(event)
