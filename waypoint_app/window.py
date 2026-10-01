"""Native Qt application window for reviewing and editing robot routes."""
from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QSignalBlocker, Qt, QTimer
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
from .geometry import autorotate, centre, snap_chambers, snap_positions, transform

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
        self.selected_uids = set()
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
        self.select_action = self.action("Select", lambda: self.set_edit_mode("select"), checkable=True)
        self.edit_group = QActionGroup(self)
        self.edit_group.setExclusive(True)
        self.edit_group.addAction(self.move_action)
        self.edit_group.addAction(self.rotate_action)
        self.edit_group.addAction(self.select_action)
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
        toolbar.addAction(self.select_action)
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
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(180)
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
        for widget in (self.map, self.table):
            select_all = QAction(widget)
            select_all.setShortcut(QKeySequence.StandardKey.SelectAll)
            select_all.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            select_all.triggered.connect(self.select_all)
            widget.addAction(select_all)
        layout.addWidget(self.table, 1)
        layout.addWidget(muted("Ctrl-click: multiple points · Shift-click: range. Shift-drag the map or use Select to box-select. Double-click to focus."))

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
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setMinimumWidth(280)
        left_scroll.setMaximumWidth(450)
        left_scroll.setWidget(left)
        splitter.addWidget(left_scroll)
        splitter.addWidget(self.map)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(265)
        scroll.setMaximumWidth(450)
        right = QWidget()
        scroll.setWidget(right)
        detail = QVBoxLayout(right)
        detail.setContentsMargins(12, 15, 12, 12)
        title = QLabel("Waypoint inspector")
        title.setObjectName("title")
        detail.addWidget(title)
        self.selection_label = muted("Select a waypoint on the map or in the route list.")
        self.selection_label.setTextFormat(Qt.TextFormat.PlainText)
        detail.addWidget(self.selection_label)
        self.auto_rotation = QCheckBox("Autorotation from path")
        self.auto_rotation.setToolTip("Align selected headings now and during edits. DriveThrough: previous → current. Measure/TurningPoint: current → next. Stop headings remain manual.")
        self.auto_rotation.toggled.connect(self.autorotation_toggled)
        detail.addWidget(self.auto_rotation)
        align_button = QPushButton("Align selected headings now")
        align_button.clicked.connect(self.align_selected)
        detail.addWidget(align_button)

        self.group_fields = QGroupBox("Transform selection")
        group_layout = QVBoxLayout(self.group_fields)
        group_form = QFormLayout()
        group_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.group_numeric = {}
        for key, label, step in (("dx", "Move X (m)", .1), ("dy", "Move Y (m)", .1), ("angle", "Rotate by (°)", 1)):
            spin = QDoubleSpinBox()
            spin.setDecimals(4)
            spin.setRange(-100000, 100000)
            spin.setSingleStep(step)
            self.group_numeric[key] = spin
            group_form.addRow(label, spin)
        group_layout.addLayout(group_form)
        self.rotate_positions = QCheckBox("Rotate positions too")
        self.rotate_positions.setToolTip("Rotate waypoint positions around the group centre as well as their headings.")
        self.rotate_positions.setChecked(True)
        self.rotate_positions.toggled.connect(self.rotation_options_changed)
        group_layout.addWidget(self.rotate_positions)
        apply_group = QPushButton("Apply group transform")
        apply_group.clicked.connect(self.apply_group_transform)
        group_layout.addWidget(apply_group)
        group_layout.addWidget(muted("Drag any selected point to move the group. Rotate with the group’s white handle. Uncheck above to rotate headings only."))
        detail.addWidget(self.group_fields)
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

        snapping = QGroupBox("Snap selected waypoints")
        snap_layout = QVBoxLayout(snapping)
        snap_form = QFormLayout()
        snap_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.snap_distance = QDoubleSpinBox()
        self.snap_distance.setRange(.001, 1000)
        self.snap_distance.setDecimals(3)
        self.snap_distance.setValue(1.5)
        self.snap_distance.setSuffix(" m")
        snap_form.addRow("Max distance", self.snap_distance)
        self.snap_scope = QComboBox()
        self.snap_scope.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.snap_scope.setMinimumContentsLength(12)
        self.snap_scope.addItems(["Nearby groups only", "All selected to one average"])
        snap_form.addRow("Waypoint snap", self.snap_scope)
        self.chamber_pairing = QComboBox()
        self.chamber_pairing.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.chamber_pairing.setMinimumContentsLength(12)
        self.chamber_pairing.addItems(["Closest active chambers", "Left chambers", "Right chambers", "Left ↔ right chambers"])
        snap_form.addRow("Chamber pairs", self.chamber_pairing)
        snap_layout.addLayout(snap_form)
        self.snap_points_button = QPushButton("Snap waypoint positions")
        self.snap_points_button.clicked.connect(self.snap_selected_positions)
        snap_layout.addWidget(self.snap_points_button)
        self.snap_chambers_button = QPushButton("Snap chamber pairs")
        self.snap_chambers_button.setToolTip("Move and rotate matched measurement waypoints to align their chamber bars at an average centre and orientation.")
        self.snap_chambers_button.clicked.connect(self.snap_selected_chambers)
        snap_layout.addWidget(self.snap_chambers_button)
        snap_layout.addWidget(muted("Chamber snapping moves and rotates each matched pair. With both sides active, all four chambers align at two shared positions. Left/right matches keep opposite driving directions."))
        detail.addWidget(snapping)
        display = QGroupBox("Map layers")
        layer_layout = QVBoxLayout(display)
        for text, attribute, default in (("Route and travel direction", "show_route", True),
                                          ("Waypoint names", "show_labels", True),
                                          ("Heading arrows", "show_headings", True),
                                          ("Measurement squares", "show_squares", False),
                                          ("Measurement chambers", "show_chambers", False)):
            checkbox = QCheckBox(text)
            checkbox.setChecked(default)
            checkbox.toggled.connect(lambda value, attr=attribute: self.layer_changed(attr, value))
            layer_layout.addWidget(checkbox)
            if attribute == "show_squares":
                self.squares_checkbox = checkbox
            if attribute == "show_chambers":
                self.chambers_checkbox = checkbox
        self.fixed_squares = QCheckBox("Use a fixed square size")
        self.square_size = QDoubleSpinBox()
        self.square_size.setRange(.01, 100)
        self.square_size.setDecimals(2)
        self.square_size.setValue(2)
        self.square_size.setSuffix(" m")
        self.fixed_squares.toggled.connect(self.square_size_changed)
        self.square_size.valueChanged.connect(self.square_size_changed)
        self.square_size.setEnabled(False)
        layer_layout.addWidget(self.fixed_squares)
        layer_layout.addWidget(self.square_size)
        layer_layout.addWidget(muted("Squares surround active measurement chambers, aligned to the field. Automatic size uses the nearest distinct chamber, as in the original reviewer."))
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
        self.map.selection_requested.connect(self.map_selection)
        self.map.box_selected.connect(self.box_selection)
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

    def selected_points(self):
        return [p for p in self.doc.points if p.uid in self.selected_uids]

    def sync_selection(self):
        self.map.selected_uid = self.selected_uid
        self.map.selected_uids = self.selected_uids.copy()
        self.map.allow_heading_edit = not self.auto_rotation.isChecked()
        self.map.allow_group_rotation = self.rotate_positions.isChecked()
        self.delete_action.setEnabled(bool(self.selected_uids))
        self.delete_button.setEnabled(bool(self.selected_uids))
        self.focus_action.setEnabled(bool(self.selected_uids))
        rotation_enabled = not self.auto_rotation.isChecked() or (len(self.selected_uids) > 1 and self.rotate_positions.isChecked())
        self.rotate_action.setEnabled(rotation_enabled)
        self.group_numeric["angle"].setEnabled(rotation_enabled)
        if not self.rotate_action.isEnabled() and self.rotate_action.isChecked():
            self.move_action.setChecked(True)
            self.map.edit_mode = "move"
        self.snap_points_button.setEnabled(len(self.selected_uids) > 1)
        self.snap_chambers_button.setEnabled(sum(p.kind == "Measure" and p.side != "none" for p in self.selected_points()) > 1)

    def refresh(self):
        self._updating = True
        self.selected_uids.intersection_update(p.uid for p in self.doc.points)
        if self.selected_uid not in self.selected_uids:
            self.selected_uid = next((p.uid for p in self.doc.points if p.uid in self.selected_uids), None)
        self.map.points = self.doc.points
        self.sync_selection()
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
            if point.uid in self.selected_uids:
                self.table.selectionModel().select(self.table.model().index(row, 0),
                                                  QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            if point.uid == self.selected_uid:
                self.table.selectionModel().setCurrentIndex(self.table.model().index(row, 0), QItemSelectionModel.SelectionFlag.NoUpdate)
        del blocker

    def refresh_inspector(self):
        self._updating = True
        point = self.selected_point()
        multiple = len(self.selected_uids) > 1
        self.fields_group.setEnabled(point is not None and not multiple)
        self.fields_group.setVisible(not multiple)
        self.group_fields.setVisible(multiple)
        self.numeric["angle"].setEnabled(not self.auto_rotation.isChecked())
        if point:
            self.selection_label.setText(f"{len(self.selected_uids)} waypoints selected · anchor: {point.name}" if multiple else
                                         f"Route position {self.doc.index(point.uid) + 1} of {len(self.doc.points)}")
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
            if multiple:
                cx, cy = centre(self.selected_points())
                self.gps_label.setText(f"Group centre X {cx:.3f} m\nGroup centre Y {cy:.3f} m")
            else:
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
        self.set_selection({uid}, uid)

    def set_selection(self, uids, primary=None, update_table=True):
        valid = {p.uid for p in self.doc.points}
        self.selected_uids = set(uids) & valid
        self.selected_uid = primary if primary in self.selected_uids else next((p.uid for p in self.doc.points if p.uid in self.selected_uids), None)
        self.sync_selection()
        if update_table:
            self.refresh_table()
        self.refresh_inspector()
        self.map.viewport().update()

    def map_selection(self, uid, mode):
        if mode == "toggle":
            self.set_selection(self.selected_uids ^ {uid}, uid)
        elif mode == "range" and self.selected_uid:
            a, b = self.doc.index(self.selected_uid), self.doc.index(uid)
            self.set_selection(self.selected_uids | {p.uid for p in self.doc.points[min(a, b):max(a, b) + 1]}, uid)
        elif mode == "preserve" and uid in self.selected_uids:
            self.set_selection(self.selected_uids, uid)
        else:
            self.select(uid)

    def box_selection(self, uids, additive):
        self.set_selection(self.selected_uids | set(uids) if additive else uids)

    def select_all(self):
        self.set_selection({p.uid for p in self.doc.points}, self.selected_uid)

    def table_selected(self):
        items = self.table.selectedItems()
        uids = {item.data(Qt.ItemDataRole.UserRole) for item in items}
        current = self.table.currentItem()
        primary = current.data(Qt.ItemDataRole.UserRole) if current else self.selected_uid
        self.set_selection(uids, primary, update_table=False)

    def focus_selected(self):
        self.map.fit_selection()

    def layer_changed(self, attr, value):
        setattr(self.map, attr, value)
        self.map.viewport().update()

    def square_size_changed(self, *_):
        fixed = self.fixed_squares.isChecked()
        self.square_size.setEnabled(fixed)
        self.map.square_size = self.square_size.value() if fixed else None
        self.map.viewport().update()

    def rotation_options_changed(self, *_):
        # Autorotation permits rotating the path geometry, while headings-only
        # controls are manual and cannot override the enabled path rule.
        self.sync_selection()
        self.map.viewport().update()

    def edit_selection(self, label, operation, follow_path=True):
        before = self.doc.snapshot()
        try:
            result = operation()
            if follow_path and self.auto_rotation.isChecked():
                autorotate(self.doc.points, self.selected_uids)
            for point in self.selected_points():
                point.validate()
                self.projection.scene(point.x, point.y)
        except (ValueError, RuntimeError) as error:
            self.doc.points = before
            self.error("Cannot edit selection", error)
            self.refresh()
            return None
        self.doc.record(label, before)
        self.refresh()
        return result

    def autorotation_toggled(self, checked):
        self.sync_selection()
        if checked:
            self.align_selected()
        else:
            self.refresh_inspector()
            self.map.viewport().update()

    def align_selected(self, *_):
        if not self.selected_uids:
            self.statusBar().showMessage("Select waypoints to align their headings.", 6000)
            return
        count = self.edit_selection("align headings to path", lambda: autorotate(self.doc.points, self.selected_uids), follow_path=False)
        if count is not None:
            self.statusBar().showMessage(f"Updated {count} headings using route travel direction. Stop headings are preserved.", 7000)

    def apply_group_transform(self):
        if len(self.selected_uids) < 2:
            return
        dx, dy = self.group_numeric["dx"].value(), self.group_numeric["dy"].value()
        angle = math.radians(self.group_numeric["angle"].value())
        if self.auto_rotation.isChecked() and not self.rotate_positions.isChecked():
            angle = 0.
        def operation():
            if self.rotate_positions.isChecked():
                transform(self.doc.points, self.selected_uids, dx=dx, dy=dy, angle=angle)
            else:
                for point in self.selected_points():
                    point.x += dx
                    point.y += dy
                    point.angle += angle
        self.edit_selection("transform selection", operation)
        for spin in self.group_numeric.values():
            spin.setValue(0)

    def snap_selected_positions(self):
        if len(self.selected_uids) < 2:
            return
        distance = self.snap_distance.value() if self.snap_scope.currentIndex() == 0 else None
        count = self.edit_selection("snap waypoint positions", lambda: snap_positions(self.doc.points, self.selected_uids, distance))
        if count is not None:
            self.statusBar().showMessage(f"Snapped {count} waypoint positions to their group averages. Route rows remain separate." if count else
                                         "No selected waypoint pairs within the snap distance. Increase the distance or choose All selected.", 10000)

    def snap_selected_chambers(self):
        if len(self.selected_uids) < 2:
            return
        side = ("closest", "left", "right", "opposite")[self.chamber_pairing.currentIndex()]
        matches = self.edit_selection("snap measurement chambers",
                                      lambda: snap_chambers(self.doc.points, self.selected_uids, self.snap_distance.value(), side),
                                      follow_path=False)
        if matches is not None:
            self.map.show_chambers = True
            self.chambers_checkbox.setChecked(True)
            self.statusBar().showMessage(f"Moved and rotated {len(matches)} measurement waypoint pairs to align their active chambers." if matches else
                                         "No eligible chamber pairs within the snap distance. Select measurement waypoints and check their active sides.", 11000)

    def apply_fields(self, *_):
        if self._updating or len(self.selected_uids) != 1 or not (point := self.selected_point()):
            return
        changed = {key: spin.value() for key, spin in self.numeric.items()
                   if spin.value() != self._editor_values.get(key)}
        if not changed and self.type_field.currentText() == point.kind and self.side_field.currentText() == point.side:
            return
        before = self.doc.snapshot()
        try:
            x, y = changed.get("x", point.x), changed.get("y", point.y)
            self.projection.scene(x, y)
            self.doc.change_type(self.doc.index(point.uid), self.type_field.currentText())
            point.side = self.side_field.currentText()
            for key, value in changed.items():
                setattr(point, key, math.radians(value) if key == "angle" else value)
            point.validate()
            if self.auto_rotation.isChecked():
                autorotate(self.doc.points, self.selected_uids)
        except (ValueError, RuntimeError) as error:
            self.doc.points = before
            self.error("Cannot edit waypoint", error)
        else:
            self.doc.record("edit waypoint", before)
        self.refresh()

    def begin_gesture(self, uid):
        self._gesture_before = self.doc.snapshot()

    def preview_move(self, uid, x, y):
        if self._gesture_before is None:
            return
        point = next(p for p in self._gesture_before if p.uid == uid)
        transform(self.doc.points, self.selected_uids, dx=x - point.x, dy=y - point.y, baseline=self._gesture_before)
        if self.auto_rotation.isChecked():
            autorotate(self.doc.points, self.selected_uids)
        self.refresh_inspector()
        self.map.viewport().update()

    def preview_rotation(self, uid, angle):
        if self._gesture_before is None:
            return
        primary = next(p for p in self._gesture_before if p.uid == uid)
        delta = angle - primary.angle
        if self.rotate_positions.isChecked():
            transform(self.doc.points, self.selected_uids, angle=delta, baseline=self._gesture_before)
        else:
            by_id = {p.uid: p for p in self._gesture_before}
            for point in self.selected_points():
                point.angle = by_id[point.uid].angle + delta
        if self.auto_rotation.isChecked():
            autorotate(self.doc.points, self.selected_uids)
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
        self.statusBar().showMessage({"move": "Drag any selected waypoint to move the selection.",
                                     "rotate": "Drag to rotate the selection around its centre.",
                                     "select": "Drag a box to select waypoints. Ctrl-click toggles; Shift-click selects a route range."}[mode])

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
        self.map._rubber = None
        self.map.viewport().update()
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
            if self.auto_rotation.isChecked():
                autorotate(self.doc.points, {point.uid} | self.selected_uids)
            self.doc.record("add waypoint", before)
        except (ValueError, RuntimeError) as error:
            self.error("Cannot add waypoint", error)
            return
        self.selected_uid = point.uid
        self.selected_uids = {point.uid}
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
        indices = [index for index, point in enumerate(self.doc.points) if point.uid in self.selected_uids]
        if not indices:
            return
        before = self.doc.snapshot()
        for index in reversed(indices):
            self.doc.delete(index)
        self.doc.record("delete waypoints", before)
        self.selected_uid = self.doc.points[min(indices[0], len(self.doc.points) - 1)].uid if self.doc.points else None
        self.selected_uids = {self.selected_uid} if self.selected_uid else set()
        self.refresh()
        self.statusBar().showMessage(f"Deleted {len(indices)} waypoints. Ctrl+Z restores them.", 6000)

    def history(self, redo):
        self.cancel_mode()
        index = self.doc.index(self.selected_uid)
        self.doc.redo() if redo else self.doc.undo()
        if self.doc.index(self.selected_uid) is None and self.doc.points and index is not None:
            self.selected_uid = self.doc.points[min(index, len(self.doc.points) - 1)].uid
            self.selected_uids.add(self.selected_uid)
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
        self.selected_uids = {self.selected_uid}
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
        self.selected_uids = set()
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
