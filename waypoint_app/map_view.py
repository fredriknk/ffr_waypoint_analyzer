"""An offline map canvas with screen-sized waypoint editing handles."""
from __future__ import annotations

import math
from collections import OrderedDict

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from .geo import HALF_WORLD, WORLD, Projection, TileSet

COLORS = {"Measure": "#62e7b5", "DriveThrough": "#69b7ff",
          "TurningPoint": "#ffbd69", "Stop": "#ff7e99"}


class MapView(QGraphicsView):
    selected = Signal(str)
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
        self.show_labels = True
        self.show_route = True
        self.show_chambers = False
        self.show_headings = True
        self.add_mode = False
        self.edit_mode = "move"
        self._gesture = None
        self._pan = None
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
        return QPointF(self.mapFromScene(QPointF(*self.projection.scene(x, y))))

    def heading_vector(self, point):
        origin = self.screen(point.x, point.y)
        end = QPointF(self.mapFromScene(QPointF(*self.projection.heading(point))))
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
        return self.screen(point.x, point.y) + self.heading_vector(point) * 43

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
    def _arrow(painter, start, end, color, width=2):
        painter.setPen(QPen(QColor(color), width))
        painter.drawLine(start, end)
        d = end - start
        length = math.hypot(d.x(), d.y())
        if length < 1:
            return
        d /= length
        normal = QPointF(-d.y(), d.x())
        painter.setBrush(QColor(color))
        painter.drawPolygon(QPolygonF([end, end - d * 7 + normal * 3.5, end - d * 7 - normal * 3.5]))

    def drawForeground(self, painter, rect):
        painter.save()
        painter.resetTransform()
        painter.setFont(QFont("Segoe UI", 9))
        positions = [self.screen(p.x, p.y) for p in self.points]
        viewport = QRectF(self.viewport().rect()).adjusted(-60, -60, 60, 60)
        occupied_labels = []
        for point, position in zip(self.points, positions):
            if point.uid == self.selected_uid:
                text = f"{self.points.index(point) + 1} · {point.name}"
                width = painter.fontMetrics().boundingRect(text).width() + 12
                occupied_labels.append(QRectF(position.x() + 11, position.y() + 8, width, 21))
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
        for index, (point, position) in enumerate(zip(self.points, positions)):
            if not viewport.contains(position):
                continue
            selected = point.uid == self.selected_uid
            color = COLORS[point.kind]
            if self.show_chambers and point.kind == "Measure":
                for side in ("left", "right"):
                    if point.side not in (side, "both"):
                        continue
                    sign = 1 if side == "right" else -1
                    x = point.x + .2 * math.cos(point.angle) + sign * 2 * math.sin(point.angle)
                    y = point.y + .2 * math.sin(point.angle) - sign * 2 * math.cos(point.angle)
                    chamber = self.screen(x, y)
                    painter.setPen(QPen(QColor(color), 1))
                    painter.setBrush(QColor(98, 231, 181, 110))
                    painter.drawRect(QRectF(chamber.x() - 3, chamber.y() - 3, 6, 6))
            if selected:
                painter.setPen(QPen(QColor("#ffffff"), 2))
                painter.setBrush(QColor(255, 255, 255, 35))
                painter.drawEllipse(position, 13, 13)
            if self.show_headings or selected:
                self._arrow(painter, position, position + self.heading_vector(point) * 26, color)
            painter.setPen(QPen(QColor("#0a1720"), 2))
            painter.setBrush(QColor(color))
            painter.drawEllipse(position, 6.5, 6.5)
            if self.show_labels or selected:
                label = f"{index + 1} · {point.name}"
                label_rect = painter.fontMetrics().boundingRect(label)
                box = QRectF(position.x() + 11, position.y() + 8, label_rect.width() + 12, 21)
                if selected or not any(box.intersects(other) for other in occupied_labels):
                    occupied_labels.append(box)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(12, 23, 30, 215))
                    painter.drawRoundedRect(box, 4, 4)
                    painter.setPen(QColor("#ffffff" if selected else "#e3ebed"))
                    painter.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
            if selected:
                handle = self.handle(point)
                painter.setPen(QPen(QColor("#ffffff"), 1.5, Qt.PenStyle.DashLine))
                painter.drawLine(position, handle)
                painter.setBrush(QColor("#182a33"))
                painter.setPen(QPen(QColor("#ffffff"), 2))
                painter.drawEllipse(handle, 5, 5)
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

    def _hit(self, pos):
        hits = [(math.hypot((self.screen(p.x, p.y) - pos).x(), (self.screen(p.x, p.y) - pos).y()), p)
                for p in self.points]
        if not hits:
            return None
        distance, point = min(hits, key=lambda item: item[0])
        return point if distance <= 12 else None

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
            handle_hit = selected and math.hypot((self.handle(selected) - pos).x(), (self.handle(selected) - pos).y()) < 10
            point = selected if handle_hit else self._hit(pos)
            if point:
                self.selected.emit(point.uid)
                self.gesture_started.emit(point.uid)
                scene = self.mapToScene(pos.toPoint())
                original = QPointF(*self.projection.scene(point.x, point.y))
                self._gesture = ("rotate" if handle_hit or self.edit_mode == "rotate" else "move",
                                 point.uid, scene - original)
                self.setCursor(Qt.CursorShape.CrossCursor if self._gesture[0] == "rotate" else Qt.CursorShape.SizeAllCursor)
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
                mode, uid, offset = self._gesture
                if mode == "move":
                    location = scene - offset
                    x, y = self.projection.coordinates(location.x(), location.y())
                    self.moved.emit(uid, x, y)
                else:
                    point = next(p for p in self.points if p.uid == uid)
                    if math.hypot(x - point.x, y - point.y) > .01:
                        self.rotated.emit(uid, math.atan2(y - point.y, x - point.x))
            elif self._pan is not None:
                delta = self.mapToScene(self._pan) - scene
                self.centerOn(self.mapToScene(self.viewport().rect().center()) + delta)
                self._pan = event.position().toPoint()
            else:
                selected = next((p for p in self.points if p.uid == self.selected_uid), None)
                handle_hit = selected and math.hypot((self.handle(selected) - event.position()).x(),
                                                     (self.handle(selected) - event.position()).y()) < 10
                self.setCursor(Qt.CursorShape.CrossCursor if self.add_mode or handle_hit else
                               Qt.CursorShape.SizeAllCursor if self._hit(event.position()) else Qt.CursorShape.OpenHandCursor)
        except (ValueError, RuntimeError):
            pass
        event.accept()

    def mouseReleaseEvent(self, event):
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
        if event.key() == Qt.Key.Key_Escape and self._gesture:
            self._gesture = None
            self.gesture_cancelled.emit()
            event.accept()
        else:
            super().keyPressEvent(event)
