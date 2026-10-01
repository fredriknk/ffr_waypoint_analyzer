"""Headless interaction checks through real Qt mouse and keyboard events."""
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from waypoint_app.model import read_csv
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


if __name__ == "__main__":
    unittest.main()
