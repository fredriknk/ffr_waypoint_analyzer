"""Headless interaction checks through real Qt mouse and keyboard events."""
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from waypoint_app.model import Waypoint, read_csv
from waypoint_app.geometry import centre, chambers
from waypoint_app.window import MainWindow, ROOT


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # Qt's Windows offscreen plugin does not discover installed fonts.
        for file in ("segoeui.ttf", "seguisb.ttf"):
            path = Path("C:/Windows/Fonts") / file
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))

    def setUp(self):
        self.window = MainWindow(ROOT / "static" / "Mapnik")
        self.window.show()
        self.app.processEvents()
        self.assertEqual(len(self.window.doc.points), 84)
        self.assertGreater(self.window.map._drawn_tiles, 0)
        self.window.error = lambda title, error: self.fail(f"{title}: {error}")

    def tearDown(self):
        self.window.doc.saved_signature = self.window.doc.signature()
        self.window.close()
        self.app.processEvents()

    def test_drag_rotate_and_undo_through_mouse_events(self):
        w = self.window
        point = w.doc.points[1]
        w.select(point.uid)
        w.map.focus_waypoint(point)
        self.app.processEvents()
        original = point.signature()
        start = w.map.screen(point.x, point.y).toPoint()
        end = start + QPoint(40, 25)
        QTest.mousePress(w.map.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(w.map.viewport(), end)
        QTest.mouseRelease(w.map.viewport(), Qt.MouseButton.LeftButton, pos=end)
        self.assertNotEqual(w.doc.points[1].signature(), original)
        self.assertEqual(len(w.doc.undo_entries), 1)
        w.history(False)
        self.assertEqual(w.doc.points[1].signature(), original)
        point = w.doc.points[1]
        w.select(point.uid)
        handle = w.map.handle(point).toPoint()
        aim = w.map.screen(point.x, point.y).toPoint() + QPoint(0, -70)
        QTest.mousePress(w.map.viewport(), Qt.MouseButton.LeftButton, pos=handle)
        QTest.mouseMove(w.map.viewport(), aim)
        QTest.mouseRelease(w.map.viewport(), Qt.MouseButton.LeftButton, pos=aim)
        self.assertAlmostEqual(w.doc.points[1].angle, math.pi / 2, delta=.06)
        self.assertEqual(len(w.doc.undo_entries), 1)
        w.history(False)
        self.assertEqual(w.doc.points[1].signature(), original)

    def test_map_insert_renumber_delete_and_export(self):
        w = self.window
        old16 = next(p for p in w.doc.points if p.name == "Plot_16")
        selected = next(p for p in w.doc.points if p.name == "Plot_15")
        w.select(selected.uid)
        w.placement.setCurrentIndex(2)
        w.add_button.setChecked(True)
        click = w.map.screen(selected.x, selected.y).toPoint() + QPoint(30, -30)
        QTest.mouseClick(w.map.viewport(), Qt.MouseButton.LeftButton, pos=click)
        self.assertEqual(len(w.doc.points), 85)
        self.assertEqual(w.selected_point().name, "Plot_16")
        self.assertEqual(old16.name, "Plot_17")
        self.assertFalse(w.map.add_mode)
        w.map.setFocus()
        QTest.keyClick(w.map, Qt.Key.Key_Delete)
        self.assertEqual(len(w.doc.points), 84)
        self.assertEqual(old16.name, "Plot_16")
        w.history(False)
        self.assertEqual(len(w.doc.points), 85)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "reviewed.csv"
            w.doc.save_as(target)
            self.assertEqual([p.signature() for p in read_csv(target)], [p.signature() for p in w.doc.points])

    def test_inspector_type_edit_and_unchanged_precision(self):
        w = self.window
        point = w.doc.points[1]
        original_numbers = point.signature()[:4]
        w.select(point.uid)
        w.type_field.setCurrentText("TurningPoint")
        w.apply_fields()
        self.assertEqual(point.kind, "TurningPoint")
        self.assertEqual(point.signature()[:4], original_numbers)
        w.numeric["angle"].setValue(45)
        QTest.keyClick(w.numeric["angle"], Qt.Key.Key_Return)
        self.assertAlmostEqual(point.angle, math.pi / 4)
        w.history(False)
        self.assertEqual(w.selected_point().signature()[:4], original_numbers)

    def test_escape_cancels_drag_and_zoom_keeps_cursor_anchor(self):
        w = self.window
        point = w.doc.points[1]
        w.select(point.uid)
        w.map.focus_waypoint(point)
        self.app.processEvents()
        original = point.signature()
        pos = w.map.screen(point.x, point.y).toPoint()
        before = w.map.mapToScene(pos)
        w.map.zoom(1.5, pos)
        after = w.map.mapToScene(pos)
        self.assertLess(math.hypot((before - after).x(), (before - after).y()), 1)
        start = w.map.screen(point.x, point.y).toPoint()
        QTest.mousePress(w.map.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(w.map.viewport(), start + QPoint(40, 0))
        QTest.keyClick(w.map, Qt.Key.Key_Escape)
        QTest.mouseRelease(w.map.viewport(), Qt.MouseButton.LeftButton, pos=start + QPoint(40, 0))
        self.assertEqual(w.doc.points[1].signature(), original)
        self.assertEqual(len(w.doc.undo_entries), 0)

    def test_save_dialog_exports_pending_property_edit(self):
        w = self.window
        point = w.selected_point()
        w.numeric["x"].setValue(point.x + .5)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "new_route.csv"
            with patch("waypoint_app.window.QFileDialog.exec", return_value=QDialog.DialogCode.Accepted), \
                 patch("waypoint_app.window.QFileDialog.selectedFiles", return_value=[str(target)]):
                self.assertTrue(w.save_dialog())
            self.assertEqual(read_csv(target)[0].x, point.x)
            self.assertFalse(w.doc.dirty)
            self.assertEqual(w.doc.saved_path, target.resolve())

    def test_select_filtered_out_waypoint_clears_old_row_highlight(self):
        w = self.window
        w.search.setText("Plot_1")
        w.select(next(p.uid for p in w.doc.points if p.name == "Plot_1"))
        self.assertGreater(len(w.table.selectedItems()), 0)
        w.select(w.doc.points[0].uid)
        self.assertEqual(w.table.selectedItems(), [])
        self.assertEqual(w.selected_point().name, "DrThr_1")

    def test_ctrl_map_selection_and_group_drag_is_one_undo_step(self):
        w = self.window
        first, second = w.doc.points[1:3]
        w.select(first.uid)
        before = w.doc.snapshot()
        QTest.mouseClick(w.map.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier,
                         pos=w.map.screen(second.x, second.y).toPoint())
        self.assertEqual(w.selected_uids, {first.uid, second.uid})
        self.assertEqual(len({item.data(Qt.ItemDataRole.UserRole) for item in w.table.selectedItems()}), 2)
        start = w.map.screen(first.x, first.y).toPoint()
        QTest.mousePress(w.map.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(w.map.viewport(), start + QPoint(15, 10))
        QTest.mouseMove(w.map.viewport(), start + QPoint(30, 20))
        QTest.mouseRelease(w.map.viewport(), Qt.MouseButton.LeftButton, pos=start + QPoint(30, 20))
        self.assertEqual(len(w.selected_uids), 2)
        self.assertEqual(len(w.doc.undo_entries), 1)
        self.assertAlmostEqual(first.x - before[1].x, second.x - before[2].x, places=7)
        self.assertAlmostEqual(first.y - before[1].y, second.y - before[2].y, places=7)
        self.assertNotEqual(first.x, before[1].x)
        self.assertEqual(w.doc.points[0], before[0])
        w.history(False)
        self.assertEqual(w.doc.points, before)
        self.assertEqual(len(w.selected_uids), 2)

    def test_group_rotation_handle_rotates_positions_and_headings(self):
        w = self.window
        first, second = w.doc.points[1:3]
        w.set_selection({first.uid, second.uid}, first.uid)
        before = w.doc.snapshot()
        cx, cy = centre(w.selected_points())
        initial_angle = first.angle
        target_angle = initial_angle + math.pi / 2
        start = w.map.handle(first).toPoint()
        target = w.map.screen(cx + 12 * math.cos(target_angle), cy + 12 * math.sin(target_angle)).toPoint()
        QTest.mousePress(w.map.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(w.map.viewport(), target)
        QTest.mouseRelease(w.map.viewport(), Qt.MouseButton.LeftButton, pos=target)
        self.assertAlmostEqual(first.angle - initial_angle, math.pi / 2, delta=.02)
        self.assertAlmostEqual(second.angle - before[2].angle, first.angle - initial_angle, places=10)
        self.assertAlmostEqual(centre(w.selected_points())[0], cx, places=7)
        self.assertAlmostEqual(centre(w.selected_points())[1], cy, places=7)
        self.assertAlmostEqual(math.hypot(first.x - second.x, first.y - second.y), math.hypot(before[1].x - before[2].x, before[1].y - before[2].y), places=7)
        self.assertEqual(len(w.doc.undo_entries), 1)
        w.history(False)
        self.assertEqual(w.doc.points, before)

    def test_shift_box_and_table_range_selection(self):
        w = self.window
        coords = [w.map.screen(p.x, p.y).toPoint() for p in w.doc.points[1:3]]
        start = QPoint(min(p.x() for p in coords) - 20, min(p.y() for p in coords) - 20)
        end = QPoint(max(p.x() for p in coords) + 20, max(p.y() for p in coords) + 20)
        rect = QRect(start, end).normalized()
        expected = {p.uid for p in w.doc.points if rect.contains(w.map.screen(p.x, p.y).toPoint())}
        QTest.mousePress(w.map.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, pos=start)
        QTest.mouseMove(w.map.viewport(), end)
        QTest.mouseRelease(w.map.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, pos=end)
        self.assertEqual(w.selected_uids, expected)
        row1 = w.table.visualItemRect(w.table.item(1, 0)).center()
        row5 = w.table.visualItemRect(w.table.item(5, 0)).center()
        QTest.mouseClick(w.table.viewport(), Qt.MouseButton.LeftButton, pos=row1)
        QTest.mouseClick(w.table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, pos=row5)
        self.assertEqual(w.selected_uids, {p.uid for p in w.doc.points[1:6]})
        self.assertEqual(len(w.doc.undo_entries), 0)

    def test_group_numeric_transform_autorotation_and_squares(self):
        w = self.window
        ids = {p.uid for p in w.doc.points[1:4]}
        w.set_selection(ids)
        before = w.doc.snapshot()
        w.group_numeric["dx"].setValue(1)
        w.group_numeric["dy"].setValue(2)
        w.apply_group_transform()
        for index in (1, 2, 3):
            self.assertEqual(w.doc.points[index].x, before[index].x + 1)
            self.assertEqual(w.doc.points[index].y, before[index].y + 2)
        w.auto_rotation.setChecked(True)
        for index in (1, 2, 3):
            p, next_point = w.doc.points[index:index + 2]
            self.assertAlmostEqual(p.angle, math.atan2(next_point.y - p.y, next_point.x - p.x))
        self.assertEqual(w.doc.points[0], before[0])
        w.squares_checkbox.setChecked(True)
        w.fixed_squares.setChecked(True)
        w.square_size.setValue(3.5)
        self.app.processEvents()
        self.assertTrue(w.map.show_squares)
        self.assertEqual(w.map.square_size, 3.5)
        self.assertGreater(len(w.map._squares), 0)
        self.assertAlmostEqual(math.dist(w.map._squares[0][1][0], w.map._squares[0][1][1]), 3.5, places=7)

    def test_snap_chambers_is_exact_undoable_and_does_not_merge_waypoints(self):
        w = self.window
        points = [Waypoint(599200, 6615300, 0, 0, "Measure", "both", "Plot_1"),
                  Waypoint(599200.4, 6615304.4, 0, 0, "Measure", "both", "Plot_2")]
        w.doc.points = points
        w.set_selection({p.uid for p in points}, points[0].uid)
        w.refresh()
        original = w.doc.snapshot()
        w.snap_selected_chambers()
        active = {(c.uid, c.side): c for c in chambers(w.doc.points)}
        a, b = active[points[0].uid, "left"], active[points[1].uid, "right"]
        self.assertAlmostEqual(a.x, b.x, places=8)
        self.assertAlmostEqual(a.y, b.y, places=8)
        self.assertAlmostEqual(math.hypot(points[0].x - points[1].x, points[0].y - points[1].y), 4)
        self.assertEqual(len(w.doc.undo_entries), 1)
        self.assertTrue(w.map.show_chambers)
        w.history(False)
        self.assertEqual(w.doc.points, original)
        w.snap_scope.setCurrentIndex(1)
        w.snap_selected_positions()
        self.assertEqual(w.doc.points[0].x, w.doc.points[1].x)
        self.assertEqual(w.doc.points[0].y, w.doc.points[1].y)
        self.assertEqual(len(w.doc.points), 2)

    def test_export_does_not_reautorotate_a_snapped_single_selection(self):
        w = self.window
        points = [Waypoint(599200, 6615300, 0, 0, "Measure", "both", "Plot_1"),
                  Waypoint(599200.4, 6615304.4, 0, 0, "Measure", "both", "Plot_2"),
                  Waypoint(599210, 6615304.4, 0, 0, "Stop", "both", "Stop_1")]
        w.doc.points = points
        w.set_selection({p.uid for p in points[:2]}, points[0].uid)
        w.refresh()
        w.auto_rotation.setChecked(True)
        w.snap_distance.setValue(5)
        w.snap_selected_chambers()
        aligned = [(c.uid, c.side, c.x, c.y) for c in chambers(w.doc.points)]
        snapshot = w.doc.snapshot()
        w.select(points[0].uid)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "chambers.csv"
            with patch("waypoint_app.window.QFileDialog.exec", return_value=QDialog.DialogCode.Accepted), \
                 patch("waypoint_app.window.QFileDialog.selectedFiles", return_value=[str(target)]):
                self.assertTrue(w.save_dialog())
            self.assertEqual(w.doc.points, snapshot)
            self.assertEqual([(c.uid, c.side, c.x, c.y) for c in chambers(w.doc.points)], aligned)
            self.assertEqual([p.signature() for p in read_csv(target)], [p.signature() for p in w.doc.points])

    def test_heading_only_group_rotation_and_bulk_delete_undo(self):
        w = self.window
        w.set_selection({p.uid for p in w.doc.points[1:4]})
        before = w.doc.snapshot()
        w.rotate_positions.setChecked(False)
        w.group_numeric["angle"].setValue(30)
        w.apply_group_transform()
        for index in (1, 2, 3):
            self.assertEqual((w.doc.points[index].x, w.doc.points[index].y), (before[index].x, before[index].y))
            self.assertAlmostEqual(w.doc.points[index].angle - before[index].angle, math.pi / 6)
        w.history(False)
        self.assertEqual(w.doc.points, before)
        w.map.setFocus()
        QTest.keyClick(w.map, Qt.Key.Key_Delete)
        self.assertEqual(len(w.doc.points), len(before) - 3)
        self.assertEqual(next(p.name for p in w.doc.points if p.kind == "Measure"), "Plot_1")
        w.history(False)
        self.assertEqual(w.doc.points, before)


if __name__ == "__main__":
    unittest.main()
