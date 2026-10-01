import csv
import math
import tempfile
import unittest
from pathlib import Path

from waypoint_app.geo import Projection, TileSet
from waypoint_app.model import Document, Waypoint, read_csv, write_csv

ROOT = Path(__file__).resolve().parent.parent


def wp(name, kind="Measure", x=599200, y=6615300):
    return Waypoint(x, y, 0, 0, kind, "both", name)


class EditTests(unittest.TestCase):
    def test_insert_between_plot_15_and_16_in_mixed_route(self):
        doc = Document()
        doc.points = [wp("Plot_15"), wp("DrThr_7", "DriveThrough"), wp("Plot_16"), wp("Plot_17")]
        before = doc.snapshot()
        doc.insert(2, wp("New"))
        self.assertEqual([p.name for p in doc.points], ["Plot_15", "DrThr_7", "Plot_16", "Plot_17", "Plot_18"])
        doc.record("insert", before)
        doc.undo()
        self.assertEqual(doc.points, before)
        doc.redo()
        self.assertEqual(doc.points[2].name, "Plot_16")

    def test_append_and_insert_other_families_preserve_prefixes(self):
        for kind, prefix in (("DriveThrough", "DrThr"), ("DriveThrough", "DrTrh"),
                             ("TurningPoint", "TurningPoint"), ("Stop", "Stop")):
            with self.subTest(kind=kind, prefix=prefix):
                doc = Document()
                doc.points = [wp(f"{prefix}_2", kind), wp("Plot_1"), wp(f"{prefix}_3", kind)]
                doc.insert(2, wp("New", kind))
                self.assertEqual([p.name for p in doc.points], [f"{prefix}_2", "Plot_1", f"{prefix}_3", f"{prefix}_4"])
                doc.insert(len(doc.points), wp("New", kind))
                self.assertEqual(doc.points[-1].name, f"{prefix}_5")

    def test_insert_before_first_and_delete_close_numbering_gap(self):
        doc = Document()
        doc.points = [wp("Plot_1"), wp("Plot_2"), wp("Plot_3")]
        doc.insert(0, wp("New"))
        self.assertEqual([p.name for p in doc.points], ["Plot_1", "Plot_2", "Plot_3", "Plot_4"])
        doc.delete(1)
        self.assertEqual([p.name for p in doc.points], ["Plot_1", "Plot_2", "Plot_3"])

    def test_change_type_updates_both_families_without_reordering(self):
        doc = Document()
        doc.points = [wp("DrThr_1", "DriveThrough"), wp("Plot_1"), wp("Plot_2"), wp("DrThr_2", "DriveThrough")]
        identities = [p.uid for p in doc.points]
        doc.change_type(1, "DriveThrough")
        self.assertEqual([p.name for p in doc.points], ["DrThr_1", "DrThr_2", "Plot_1", "DrThr_3"])
        self.assertEqual(identities, [p.uid for p in doc.points])

    def test_all_real_files_round_trip_with_original_numeric_values(self):
        paths = list((ROOT / "Waypoints").glob("*.csv"))
        self.assertGreater(len(paths), 0)
        with tempfile.TemporaryDirectory() as directory:
            for path in paths:
                with self.subTest(file=path.name):
                    points = read_csv(path)
                    target = Path(directory) / path.name
                    write_csv(target, points)
                    with path.open(newline="", encoding="utf-8-sig") as stream:
                        expected = [[v.strip() for v in row] for row in csv.reader(stream) if row and any(v.strip() for v in row)]
                    with target.open(newline="") as stream:
                        self.assertEqual(list(csv.reader(stream)), expected)

    def test_validation_and_atomic_failure_leave_destination_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.csv"
            for data in ("1,2,3\n", "nan,2,0,0,Measure,both,Plot_1\n",
                         "1,2,0,0,Unrecognized,both,Plot_1\n", "\n"):
                path.write_text(data)
                with self.assertRaises(ValueError):
                    read_csv(path)
            path.write_text("keep me")
            with self.assertRaises(ValueError):
                write_csv(path, [wp("Invalid", x=math.inf)])
            self.assertEqual(path.read_text(), "keep me")

    def test_save_as_protects_source_and_history_tracks_saved_state(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "original.csv"
            write_csv(source, [wp("Plot_1")])
            doc = Document()
            doc.load(source)
            before = doc.snapshot()
            doc.points[0].x += 1.25
            doc.record("move", before)
            self.assertTrue(doc.dirty)
            with self.assertRaises(ValueError):
                doc.save_as(source)
            target = Path(directory) / "edited.csv"
            doc.save_as(target)
            self.assertFalse(doc.dirty)
            doc.undo()
            self.assertTrue(doc.dirty)
            doc.redo()
            self.assertFalse(doc.dirty)
            self.assertEqual(read_csv(source)[0].x, 599200)
            self.assertEqual(read_csv(target)[0].x, 599201.25)


class GeoTests(unittest.TestCase):
    def test_real_waypoints_projection_and_available_imagery(self):
        projection = Projection()
        tiles = TileSet(ROOT / "static" / "Mapnik")
        covered, total = 0, 0
        for path in (ROOT / "Waypoints").glob("*.csv"):
            for point in read_csv(path):
                mx, sy = projection.scene(point.x, point.y)
                covered += tiles.covers(mx, sy)
                total += 1
                px, py = projection.coordinates(mx, sy)
                self.assertAlmostEqual(px, point.x, places=6)
                self.assertAlmostEqual(py, point.y, places=6)
        # Some supplied routes extend north of the tile download. Those points
        # remain editable; their coverage warning is intentional.
        self.assertGreater(covered / total, .9)

    def test_geographic_crs_rejected_and_tile_parent_fallback(self):
        with self.assertRaises(ValueError):
            Projection("EPSG:4326")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "17" / "69452").mkdir(parents=True)
            parent = root / "17" / "69452" / "38306.jpg"
            parent.touch()
            found = TileSet(root).find(18, 138905, 76613)
            self.assertEqual(found, (parent, 2, 1, 1))


if __name__ == "__main__":
    unittest.main()
