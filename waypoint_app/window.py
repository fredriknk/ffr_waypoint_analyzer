"""Native Qt application window for reviewing and editing robot routes."""
from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
    QToolBar, QVBoxLayout, QWidget, QAbstractItemView, QInputDialog,
    QDialog,
)

from .geo import Projection, TileSet
from .map_view import COLORS, MapView
from .model import Document, SIDES, TYPES, Waypoint

ROOT = Path(__file__).resolve().parent.parent

STYLE = """
QMainWindow, QWidget { background: #111c24; color: #dce8ee; font: 10pt 'Segoe UI'; }
QToolBar { background: #172630; spacing: 7px; padding: 9px; border: none; }
QToolBar::separator { background: #334650; width: 1px; margin: 3px 7px; }
QToolButton, QPushButton { background: #233743; border: 1px solid #3b525f;
    border-radius: 5px; padding: 7px 11px; color: #e8f0f5; }
QToolButton:hover, QPushButton:hover { background: #304955; border-color: #62e7b5; }
QToolButton:checked, QPushButton:checked, QPushButton#primary { background: #25634f;
    border-color: #62e7b5; color: #ffffff; }
QPushButton#danger { color: #ff9db0; }
QToolButton:disabled, QPushButton:disabled { color: #657c89; border-color: #293b44; }
QLineEdit, QComboBox, QDoubleSpinBox { background: #1b2d38; border: 1px solid #39505e;
    border-radius: 4px; padding: 6px; selection-background-color: #286c55; }
QComboBox QAbstractItemView { background: #1b2d38; selection-background-color: #286c55; }
QGroupBox { border: 1px solid #314550; border-radius: 6px; margin-top: 14px;
    padding: 12px 9px 9px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QTableWidget { background: #111c24; alternate-background-color: #172630;
    border: none; gridline-color: #263b47; selection-background-color: #255346; }
QTableWidget::item { padding: 5px; }
QHeaderView::section { background: #1b2d38; color: #94aebc; border: none; padding: 7px; }
QSplitter::handle { background: #304550; width: 2px; }
QStatusBar { background: #172630; color: #9bb4c2; }
QLabel#brand { font: 600 12pt 'Segoe UI'; color: #62e7b5; padding-right: 12px; }
QLabel#title { font: 600 14pt 'Segoe UI'; color: #ffffff; }
QLabel#muted { color: #9bb4c2; font: 9pt 'Segoe UI'; }
QLabel#stats { color: #62e7b5; font: 600 10pt 'Segoe UI'; padding: 4px 0; }
QScrollArea { border: none; }
QScrollBar:vertical { width: 10px; background: #13222c; }
QScrollBar::handle:vertical { background: #47616f; min-height: 25px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { background: #233743; color: #ffffff; border: 1px solid #62e7b5; }
"""


def muted(text):
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


class MainWindow(QMainWindow):
    def __init__(self, tiles_path: Path, crs: str = "EPSG:32632", initial_file: Path | None = None):
        super().__init__()
        self.doc = Document()
        self.projection = Projection(crs)
        self.selected_uid = None
        self._gesture_before = None
        self._updating = False
        self._editor_values = {}
        self.setWindowTitle("Waypoint Studio")
        self.resize(1500, 920)
        self.setMinimumSize(1050, 680)
        self.setStyleSheet(STYLE)
        tiles_error = None
        try:
            tiles = TileSet(tiles_path)
            tiles.bounds()
        except (OSError, ValueError) as error:
            tiles = None
            tiles_error = str(error)
        self.map = MapView(self.projection, tiles)
        self._actions()
        self._layout()
        self._menus()
        self._connect_map()
        self.refresh()
        if initial_file:
            QTimer.singleShot(0, lambda: self.open_file(initial_file))
        elif (default := ROOT / "Waypoints" / "capture_long_ny_gps.csv").exists():
            QTimer.singleShot(0, lambda: self.open_file(default))
        else:
            QTimer.singleShot(0, self.map.fit_route)
        if tiles_error:
            QTimer.singleShot(100, lambda: self.statusBar().showMessage(f"Map tiles: {tiles_error}", 15000))

    def action(self, text, callback, shortcut=None, checkable=False):
        action = QAction(text, self)
        action.setCheckable(checkable)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(callback)
        self.addAction(action)
        return action

    def _actions(self):
        self.open_action = self.action("Open CSV…", self.open_dialog, QKeySequence.StandardKey.Open)
        self.save_action = self.action("Save As…", self.save_dialog, QKeySequence.StandardKey.Save)
        self.new_action = self.action("New route", self.new_route, QKeySequence.StandardKey.New)
        self.undo_action = self.action("Undo", lambda: self.history(False), QKeySequence.StandardKey.Undo)
        self.redo_action = self.action("Redo", lambda: self.history(True), QKeySequence.StandardKey.Redo)
        self.redo_action.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self.delete_action = self.action("Delete waypoint", self.delete_selected)
        # Delete is a route shortcut only when the map or route list has focus.
        # Inside the inspector, the key belongs to the active text editor.
        for widget in (self.map,):
            shortcut = QAction(widget)
            shortcut.setShortcut(QKeySequence("Delete"))
            shortcut.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.triggered.connect(self.delete_selected)
            widget.addAction(shortcut)
        self.fit_action = self.action("Fit route", self.map.fit_route, QKeySequence("F"))
        self.focus_action = self.action("Focus selected", self.focus_selected)
        self.zoom_in_action = self.action("+", lambda: self.map.zoom(1.5), QKeySequence("Ctrl++"))
        self.zoom_out_action = self.action("−", lambda: self.map.zoom(1 / 1.5), QKeySequence("Ctrl+-"))
        self.escape_action = self.action("Cancel placement", self.cancel_mode, QKeySequence("Escape"))
        self.move_action = self.action("Move", lambda: self.set_edit_mode("move"), checkable=True)
        self.rotate_action = self.action("Rotate", lambda: self.set_edit_mode("rotate"), checkable=True)
        self.edit_group = QActionGroup(self)
        self.edit_group.setExclusive(True)
        self.edit_group.addAction(self.move_action)
        self.edit_group.addAction(self.rotate_action)
        self.move_action.setChecked(True)

    def _layout(self):
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        brand = QLabel("WAYPOINT STUDIO")
        brand.setObjectName("brand")
        toolbar.addWidget(brand)
        toolbar.addAction(self.open_action)
        toolbar.addAction(self.save_action)
        toolbar.addSeparator()
        toolbar.addAction(self.undo_action)
        toolbar.addAction(self.redo_action)
        toolbar.addSeparator()
        toolbar.addAction(self.move_action)
        toolbar.addAction(self.rotate_action)
        toolbar.addSeparator()
        toolbar.addAction(self.fit_action)
        toolbar.addAction(self.focus_action)
        toolbar.addAction(self.zoom_out_action)
        toolbar.addAction(self.zoom_in_action)

        splitter = QSplitter()
        self.setCentralWidget(splitter)
        left = QWidget()
        left.setMinimumWidth(265)
        left.setMaximumWidth(450)
        layout = QVBoxLayout(left)
        layout.setContentsMargins(14, 15, 14, 12)
        title = QLabel("Route")
        title.setObjectName("title")
        layout.addWidget(title)
        self.file_label = muted("Open a waypoint CSV to begin")
        self.file_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.file_label)
        self.samples = QComboBox()
        self.samples.addItem("Open an example route…", None)
        for path in sorted((ROOT / "Waypoints").glob("*.csv")):
            self.samples.addItem(path.name, path)
        self.samples.activated.connect(self.sample_selected)
        layout.addWidget(self.samples)
        self.stats = QLabel()
        self.stats.setObjectName("stats")
        layout.addWidget(self.stats)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a waypoint by name or type…")
        self.search.textChanged.connect(self.refresh_table)
        layout.addWidget(self.search)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["#", "Waypoint", "Type"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self.table_selected)
        self.table.itemDoubleClicked.connect(lambda _: self.focus_selected())
        table_delete = QAction(self.table)
        table_delete.setShortcut(QKeySequence("Delete"))
        table_delete.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        table_delete.triggered.connect(self.delete_selected)
        self.table.addAction(table_delete)
        layout.addWidget(self.table, 1)
        layout.addWidget(muted("List order is the robot’s driving order. Double-click a row to focus it on the map."))

        add = QGroupBox("Add a waypoint")
        add_layout = QVBoxLayout(add)
        form = QFormLayout()
        self.add_type = QComboBox()
        self.add_type.addItems(TYPES)
        self.add_side = QComboBox()
        self.add_side.addItems(SIDES)
        self.placement = QComboBox()
        self.placement.addItems(["Append to end", "Insert before selected", "Insert after selected"])
        self.placement.setToolTip("Insertion uses the selected row’s position in the complete route, including other waypoint types.")
        form.addRow("Type", self.add_type)
        form.addRow("Side", self.add_side)
        form.addRow("Route position", self.placement)
        add_layout.addLayout(form)
        self.add_button = QPushButton("Place waypoint on map")
        self.add_button.setObjectName("primary")
        self.add_button.setCheckable(True)
        self.add_button.toggled.connect(self.toggle_add)
        add_layout.addWidget(self.add_button)
        self.midpoint_button = QPushButton("Insert at segment midpoint")
        self.midpoint_button.setToolTip("Select a waypoint and insert before or after it. The new waypoint is placed halfway along that segment.")
        self.midpoint_button.clicked.connect(self.insert_midpoint)
        add_layout.addWidget(self.midpoint_button)
        add_layout.addWidget(muted("New points inherit the selected Z and heading. Following names of the same type are renumbered automatically."))
        layout.addWidget(add)
        splitter.addWidget(left)
        splitter.addWidget(self.map)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(265)
        scroll.setMaximumWidth(450)
        right = QWidget()
        scroll.setWidget(right)
        detail = QVBoxLayout(right)
        detail.setContentsMargins(16, 15, 16, 12)
        title = QLabel("Waypoint inspector")
        title.setObjectName("title")
        detail.addWidget(title)
        self.selection_label = muted("Select a waypoint on the map or in the route list.")
        self.selection_label.setTextFormat(Qt.TextFormat.PlainText)
        detail.addWidget(self.selection_label)
        self.fields_group = QGroupBox("Properties")
        fields = QFormLayout(self.fields_group)
        fields.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        fields.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.name_label = QLabel("—")
        self.name_label.setTextFormat(Qt.TextFormat.PlainText)
        self.name_label.setWordWrap(True)
        fields.addRow("Name", self.name_label)
        self.type_field = QComboBox()
        self.type_field.addItems(TYPES)
        fields.addRow("Type", self.type_field)
        self.side_field = QComboBox()
        self.side_field.addItems(SIDES)
        fields.addRow("Measure side", self.side_field)
        self.numeric = {}
        for key, label, decimals, minimum, maximum in (
            ("x", "X / easting (m)", 6, -100000000, 100000000),
            ("y", "Y / northing (m)", 6, -100000000, 100000000),
            ("z", "Z (m)", 6, -100000, 100000),
            ("angle", "Heading (°)", 4, -36000, 36000),
        ):
            spin = QDoubleSpinBox()
            spin.setDecimals(decimals)
            spin.setRange(minimum, maximum)
            spin.setSingleStep(1 if key == "angle" else .1)
            spin.setKeyboardTracking(False)
            spin.editingFinished.connect(self.apply_fields)
            self.numeric[key] = spin
            fields.addRow(label, spin)
        self.type_field.activated.connect(self.apply_fields)
        self.side_field.activated.connect(self.apply_fields)
        detail.addWidget(self.fields_group)
        detail.addWidget(muted("Values apply on Enter or when you leave a field. Heading: 0° = grid east, +90° = grid north. CSV headings remain in radians."))
        self.gps_label = muted("")
        detail.addWidget(self.gps_label)
        buttons = QHBoxLayout()
        focus = QPushButton("Focus")
        focus.clicked.connect(self.focus_selected)
        self.delete_button = QPushButton("Delete")
        self.delete_button.setObjectName("danger")
        self.delete_button.clicked.connect(self.delete_selected)
        buttons.addWidget(focus)
        buttons.addWidget(self.delete_button)
        detail.addLayout(buttons)
        display = QGroupBox("Map layers")
        layer_layout = QVBoxLayout(display)
        for text, attribute, default in (("Route and travel direction", "show_route", True),
                                          ("Waypoint names", "show_labels", True),
                                          ("Heading arrows", "show_headings", True),
                                          ("Measurement chamber positions", "show_chambers", False)):
            checkbox = QCheckBox(text)
            checkbox.setChecked(default)
            checkbox.toggled.connect(lambda value, attr=attribute: self.layer_changed(attr, value))
            layer_layout.addWidget(checkbox)
        layer_layout.addWidget(muted("Chambers use the original script’s geometry: 0.2 m ahead and 2 m to either side."))
        detail.addWidget(display)
        settings = QGroupBox("Map setup")
        settings_layout = QVBoxLayout(settings)
        self.crs_label = muted("")
        settings_layout.addWidget(self.crs_label)
        crs_button = QPushButton("Change coordinate system…")
        crs_button.clicked.connect(self.change_crs)
        settings_layout.addWidget(crs_button)
        self.tiles_label = muted("")
        self.tiles_label.setTextFormat(Qt.TextFormat.PlainText)
        settings_layout.addWidget(self.tiles_label)
        tile_button = QPushButton("Choose map tile folder…")
        tile_button.clicked.connect(self.change_tiles)
        settings_layout.addWidget(tile_button)
        detail.addWidget(settings)
        legend = QGroupBox("Waypoint types")
        legend_layout = QVBoxLayout(legend)
        for kind in TYPES:
            label = QLabel(f"●  {kind}")
            label.setStyleSheet(f"color: {COLORS[kind]};")
            legend_layout.addWidget(label)
        detail.addWidget(legend)
        detail.addWidget(muted("Wheel: zoom at cursor\nDrag empty map / right drag: pan\nDrag waypoint: move\nDrag white heading handle: rotate\nRotate tool: drag to aim\nDelete: remove selected\nCtrl+Z / Ctrl+Y: undo / redo\nF: fit route · Esc: cancel"))
        detail.addStretch(1)
        splitter.addWidget(scroll)
        splitter.setSizes([310, 900, 290])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        self.coordinate_status = QLabel("")
        self.statusBar().addPermanentWidget(self.coordinate_status)
        self.statusBar().showMessage("Open an example route or a waypoint CSV.")

    def _menus(self):
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(self.new_action)
        file_menu.addAction(self.open_action)
        file_menu.addAction(self.save_action)
        edit_menu = self.menuBar().addMenu("Edit")
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)
        edit_menu.addAction(self.delete_action)
        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(self.fit_action)
        view_menu.addAction(self.focus_action)

    def _connect_map(self):
        self.map.selected.connect(self.select)
        self.map.add_requested.connect(self.add_at)
        self.map.gesture_started.connect(self.begin_gesture)
        self.map.moved.connect(self.preview_move)
        self.map.rotated.connect(self.preview_rotation)
        self.map.gesture_finished.connect(self.end_gesture)
        self.map.gesture_cancelled.connect(self.cancel_gesture)
        self.map.cursor_coordinates.connect(lambda x, y: self.coordinate_status.setText(f"X {x:.3f} m   Y {y:.3f} m"))

    def selected_point(self):
        index = self.doc.index(self.selected_uid)
        return self.doc.points[index] if index is not None else None

    def refresh(self):
        self._updating = True
        if self.doc.index(self.selected_uid) is None:
            self.selected_uid = self.doc.points[0].uid if self.doc.points else None
        self.map.points = self.doc.points
        self.map.selected_uid = self.selected_uid
        path = self.doc.saved_path or self.doc.source
        self.file_label.setText(path.name if path else "New route")
        self.file_label.setToolTip(str(path) if path else "")
        counts = Counter(p.kind for p in self.doc.points)
        length = sum(math.hypot(b.x - a.x, b.y - a.y) for a, b in zip(self.doc.points, self.doc.points[1:]))
        self.stats.setText(f"{len(self.doc.points)} waypoints · {length:.1f} m\n{counts['Measure']} plots · {counts['DriveThrough']} drive-throughs")
        title = f"{path.name if path else 'New route'}{' •' if self.doc.dirty else ''} — Waypoint Studio"
        self.setWindowTitle(title)
        self.undo_action.setEnabled(bool(self.doc.undo_entries))
        self.undo_action.setText("Undo " + (self.doc.undo_entries[-1][0] if self.doc.undo_entries else ""))
        self.redo_action.setEnabled(bool(self.doc.redo_entries))
        self.redo_action.setText("Redo " + (self.doc.redo_entries[-1][0] if self.doc.redo_entries else ""))
        self.delete_action.setEnabled(self.selected_uid is not None)
        self.delete_button.setEnabled(self.selected_uid is not None)
        self.focus_action.setEnabled(self.selected_uid is not None)
        self.midpoint_button.setEnabled(len(self.doc.points) >= 2)
        self.crs_label.setText(f"{self.projection.crs.to_string()}\n{self.projection.crs.name}")
        self.tiles_label.setText(f"Local tiles: {self.map.tiles.root.name}\nZooms {self.map.tiles.levels[0]}–{self.map.tiles.levels[-1]}"
                                 if self.map.tiles else "No map folder loaded")
        self._updating = False
        self.refresh_table()
        self.refresh_inspector()
        self.map.viewport().update()

    def refresh_table(self):
        blocker = QSignalBlocker(self.table)
        self.table.setRowCount(0)
        search = self.search.text().casefold().strip()
        for index, point in enumerate(self.doc.points):
            if search and search not in f"{index + 1} {point.name} {point.kind}".casefold():
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, text in enumerate((str(index + 1), point.name, point.kind)):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, point.uid)
                if col == 2:
                    item.setForeground(QColor(COLORS[point.kind]))
                self.table.setItem(row, col, item)
            if point.uid == self.selected_uid:
                self.table.selectRow(row)
        del blocker

    def refresh_inspector(self):
        self._updating = True
        point = self.selected_point()
        self.fields_group.setEnabled(point is not None)
        if point:
            self.selection_label.setText(f"Route position {self.doc.index(point.uid) + 1} of {len(self.doc.points)}")
            self.name_label.setText(point.name)
            self.type_field.setCurrentText(point.kind)
            self.side_field.setCurrentText(point.side)
            self._editor_values = {}
            for key, spin in self.numeric.items():
                spin.setValue(math.degrees(point.angle) if key == "angle" else getattr(point, key))
                self._editor_values[key] = spin.value()
            lon, lat = self.projection.to_gps.transform(point.x, point.y)
            coverage = ""
            if self.map.tiles and not self.map.tiles.covers(*self.projection.scene(point.x, point.y)):
                coverage = "\nOutside downloaded imagery"
            self.gps_label.setText(f"Latitude {lat:.8f}°\nLongitude {lon:.8f}°{coverage}")
        else:
            self.selection_label.setText("Select a waypoint or add one to the route.")
            self.name_label.setText("—")
            self.gps_label.setText("")
            self._editor_values = {}
        self._updating = False

    def select(self, uid):
        if self._updating:
            return
        self.selected_uid = uid
        self.map.selected_uid = uid
        blocker = QSignalBlocker(self.table)
        self.table.clearSelection()
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == uid:
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
                break
        del blocker
        self.refresh_inspector()
        self.delete_action.setEnabled(True)
        self.delete_button.setEnabled(True)
        self.focus_action.setEnabled(True)
        self.map.viewport().update()

    def table_selected(self):
        items = self.table.selectedItems()
        if items:
            self.select(items[0].data(Qt.ItemDataRole.UserRole))

    def focus_selected(self):
        if point := self.selected_point():
            self.map.focus_waypoint(point)

    def layer_changed(self, attr, value):
        setattr(self.map, attr, value)
        self.map.viewport().update()

    def apply_fields(self, *_):
        if self._updating or not (point := self.selected_point()):
            return
        before = self.doc.snapshot()
        try:
            changed = {key: spin.value() for key, spin in self.numeric.items()
                       if spin.value() != self._editor_values.get(key)}
            x, y = changed.get("x", point.x), changed.get("y", point.y)
            self.projection.scene(x, y)
            self.doc.change_type(self.doc.index(point.uid), self.type_field.currentText())
            point.side = self.side_field.currentText()
            for key, value in changed.items():
                setattr(point, key, math.radians(value) if key == "angle" else value)
            point.validate()
        except (ValueError, RuntimeError) as error:
            self.doc.points = before
            self.error("Cannot edit waypoint", error)
        else:
            self.doc.record("edit waypoint", before)
        self.refresh()

    def begin_gesture(self, uid):
        self._gesture_before = self.doc.snapshot()

    def preview_move(self, uid, x, y):
        point = self.doc.points[self.doc.index(uid)]
        point.x, point.y = x, y
        self.refresh_inspector()
        self.map.viewport().update()

    def preview_rotation(self, uid, angle):
        self.doc.points[self.doc.index(uid)].angle = angle
        self.refresh_inspector()
        self.map.viewport().update()

    def end_gesture(self):
        if self._gesture_before is not None:
            self.doc.record("drag waypoint", self._gesture_before)
            self._gesture_before = None
            self.refresh()

    def cancel_gesture(self):
        if self._gesture_before is not None:
            self.doc.points = self._gesture_before
            self._gesture_before = None
            self.refresh()

    def set_edit_mode(self, mode):
        self.add_button.setChecked(False)
        self.map.edit_mode = mode
        self.statusBar().showMessage("Drag a waypoint to move it." if mode == "move" else "Drag from a waypoint toward the desired heading.")

    def toggle_add(self, enabled):
        if enabled and self.placement.currentIndex() != 0 and self.selected_point() is None:
            self.add_button.setChecked(False)
            self.statusBar().showMessage("Select a waypoint first, or choose Append to end.", 6000)
            return
        self.map.add_mode = enabled
        self.map.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.OpenHandCursor)
        self.add_button.setText("Cancel placement (Esc)" if enabled else "Place waypoint on map")
        self.map.viewport().update()
        self.statusBar().showMessage("Click the map to place the new waypoint. Esc cancels." if enabled else "Ready to review and edit.")

    def cancel_mode(self):
        if self.map._gesture:
            self.map._gesture = None
            self.cancel_gesture()
        self.add_button.setChecked(False)

    def insertion_index(self):
        mode = self.placement.currentIndex()
        if mode == 0:
            return len(self.doc.points)
        index = self.doc.index(self.selected_uid)
        if index is None:
            raise ValueError("Select the waypoint to insert before or after.")
        return index + (1 if mode == 2 else 0)

    def add_at(self, x, y, angle=None, z=None):
        try:
            index = self.insertion_index()
            self.projection.scene(x, y)
            selected = self.selected_point()
            point = Waypoint(x, y, z if z is not None else selected.z if selected else 0,
                             angle if angle is not None else selected.angle if selected else 0,
                             self.add_type.currentText(), self.add_side.currentText(), "New")
            before = self.doc.snapshot()
            self.doc.insert(index, point)
            self.doc.record("add waypoint", before)
        except (ValueError, RuntimeError) as error:
            self.error("Cannot add waypoint", error)
            return
        self.selected_uid = point.uid
        self.add_button.setChecked(False)
        self.refresh()
        self.statusBar().showMessage(f"Added {point.name} at route position {index + 1}. Drag it or edit its properties.", 8000)

    def insert_midpoint(self):
        index = self.doc.index(self.selected_uid)
        if index is None or self.placement.currentIndex() == 0:
            self.statusBar().showMessage("Select a waypoint and choose Insert before selected or Insert after selected.", 8000)
            return
        neighbor = index - 1 if self.placement.currentIndex() == 1 else index + 1
        if not 0 <= neighbor < len(self.doc.points):
            self.statusBar().showMessage("There is no adjacent segment on that side. Place a waypoint on the map instead.", 8000)
            return
        a, b = self.doc.points[index], self.doc.points[neighbor]
        heading = a.angle + math.atan2(math.sin(b.angle - a.angle), math.cos(b.angle - a.angle)) / 2
        self.add_at((a.x + b.x) / 2, (a.y + b.y) / 2, heading, (a.z + b.z) / 2)

    def delete_selected(self):
        # The Delete key must still edit text inside an active property field.
        focus = self.focusWidget()
        if isinstance(focus, QLineEdit):
            return
        index = self.doc.index(self.selected_uid)
        if index is None:
            return
        before = self.doc.snapshot()
        removed = self.doc.delete(index)
        self.doc.record("delete waypoint", before)
        self.selected_uid = self.doc.points[min(index, len(self.doc.points) - 1)].uid if self.doc.points else None
        self.refresh()
        self.statusBar().showMessage(f"Deleted {removed.name}. Ctrl+Z restores it.", 6000)

    def history(self, redo):
        self.cancel_mode()
        index = self.doc.index(self.selected_uid)
        self.doc.redo() if redo else self.doc.undo()
        if self.doc.index(self.selected_uid) is None and self.doc.points and index is not None:
            self.selected_uid = self.doc.points[min(index, len(self.doc.points) - 1)].uid
        self.refresh()

    def confirm_discard(self):
        if not self.doc.dirty:
            return True
        response = QMessageBox.question(self, "Unsaved route edits", "Save your edits to a new CSV before continuing?",
                                        QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                        QMessageBox.StandardButton.Save)
        if response == QMessageBox.StandardButton.Save:
            return self.save_dialog()
        return response == QMessageBox.StandardButton.Discard

    def open_dialog(self, *_):
        path, _ = QFileDialog.getOpenFileName(self, "Open robot waypoint CSV", str(ROOT / "Waypoints"), "Waypoint CSV (*.csv)")
        if path:
            self.open_file(Path(path))

    def open_file(self, path):
        if not self.confirm_discard():
            return
        try:
            # Parse and validate everything before replacing the current document.
            from .model import read_csv
            points = read_csv(Path(path))
            for point in points:
                self.projection.scene(point.x, point.y)
            self.doc.load(Path(path))
        except (OSError, ValueError, RuntimeError) as error:
            self.error("Cannot open waypoint file", error)
            return
        self.cancel_mode()
        self.selected_uid = self.doc.points[0].uid
        self.search.clear()
        self.refresh()
        self.map.fit_route()
        self.statusBar().showMessage(f"Loaded {len(self.doc.points)} waypoints. Original file is preserved when saving.", 8000)

    def sample_selected(self, index):
        if path := self.samples.itemData(index):
            self.open_file(path)
        self.samples.setCurrentIndex(0)

    def new_route(self, *_):
        if not self.confirm_discard():
            return
        self.cancel_mode()
        self.doc = Document()
        self.selected_uid = None
        self.search.clear()
        self.refresh()
        self.map.fit_route()

    def save_dialog(self, *_):
        # Finish an in-progress field edit before exporting.
        self.apply_fields()
        if not self.doc.points:
            self.statusBar().showMessage("Add at least one waypoint before saving.", 6000)
            return False
        if self.doc.saved_path:
            suggested = self.doc.saved_path
        elif self.doc.source:
            suggested = self.doc.source.with_name(self.doc.source.stem + "_edited.csv")
        else:
            suggested = ROOT / "Waypoints" / "new_route.csv"
        dialog = QFileDialog(self, "Save route as a new CSV", str(suggested), "Waypoint CSV (*.csv)")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        # Apply the suffix before Qt checks whether the destination exists.
        dialog.setDefaultSuffix("csv")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        destination = Path(dialog.selectedFiles()[0])
        try:
            self.doc.save_as(destination)
        except (OSError, ValueError) as error:
            self.error("Cannot save waypoint file", error)
            return False
        self.refresh()
        self.statusBar().showMessage(f"Saved {len(self.doc.points)} waypoints to {destination.name}", 10000)
        return True

    def change_tiles(self):
        path = QFileDialog.getExistingDirectory(self, "Choose the folder containing XYZ zoom folders", str(ROOT / "static"))
        if not path:
            return
        try:
            tiles = TileSet(Path(path))
            tiles.bounds()
        except (OSError, ValueError) as error:
            self.error("Cannot load map tiles", error)
            return
        self.map.set_tiles(tiles)
        self.refresh()
        self.map.fit_route()

    def change_crs(self):
        text, accepted = QInputDialog.getText(self, "Waypoint coordinate system", "Projected CRS in metres (e.g. EPSG:32632):",
                                              text=self.projection.crs.to_string())
        if not accepted:
            return
        try:
            projection = Projection(text.strip())
            for point in self.doc.points:
                projection.scene(point.x, point.y)
        except (ValueError, RuntimeError) as error:
            self.error("Invalid coordinate system", error)
            return
        self.projection = projection
        self.map.projection = projection
        self.refresh()
        self.map.fit_route()

    def error(self, title, error):
        QMessageBox.warning(self, title, str(error))

    def closeEvent(self, event):
        self.apply_fields()
        if self.confirm_discard():
            event.accept()
        else:
            event.ignore()
