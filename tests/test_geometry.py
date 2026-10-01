import math
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from waypoint_app.geometry import (autorotate, centre, chambers, field_angle,
                                   measurement_squares, snap_chambers, snap_positions, transform)
from waypoint_app.model import Waypoint, read_csv, write_csv


def point(x, y, kind="Measure", side="both", angle=0):
    return Waypoint(x, y, 0, angle, kind, side, "Plot_1")


class GeometryTests(unittest.TestCase):
    def test_group_rotation_preserves_centre_distances_and_relative_headings(self):
        points = [point(10, 20, angle=.4), point(14, 26, angle=-1.3), point(100, 100)]
        ids = {p.uid for p in points[:2]}
        baseline = deepcopy(points)
        transform(points, ids, angle=math.pi / 2)
        self.assertEqual(centre(points[:2]), centre(baseline[:2]))
        self.assertAlmostEqual(points[0].x, 15)
        self.assertAlmostEqual(points[0].y, 21)
        self.assertAlmostEqual(points[1].x, 9)
        self.assertAlmostEqual(points[1].y, 25)
        self.assertAlmostEqual(points[0].angle - baseline[0].angle, math.pi / 2)
        self.assertEqual(points[2], baseline[2])
        transform(points, ids, dx=4, dy=-3, baseline=baseline)
        transform(points, ids, dx=5, dy=-2, baseline=baseline)
        self.assertEqual((points[0].x, points[0].y), (15, 18))
        self.assertEqual(points[0].angle, baseline[0].angle)
        transform(points, ids)
        self.assertEqual(points[0].x, 15)

    def test_path_headings_use_incoming_for_drive_and_outgoing_for_plot_turn(self):
        points = [point(0, 0, "Stop", angle=.7), point(10, 0, "DriveThrough", angle=2),
                  point(10, 10, "Measure", angle=2), point(20, 10, "TurningPoint", angle=2),
                  point(20, 20, "Stop", angle=-.4)]
        count = autorotate(points, {p.uid for p in points})
        self.assertEqual(count, 3)
        self.assertEqual(points[1].angle, 0)
        self.assertEqual(points[2].angle, 0)
        self.assertAlmostEqual(points[3].angle, math.pi / 2)
        self.assertEqual(points[0].angle, .7)
        self.assertEqual(points[4].angle, -.4)

    def test_path_endpoints_coincident_neighbors_and_selection_scope(self):
        points = [point(0, 0, "DriveThrough"), point(0, 0), point(0, 10), point(0, 10)]
        autorotate(points, {p.uid for p in points})
        self.assertTrue(all(abs(p.angle - math.pi / 2) < 1e-12 for p in points))
        single = point(0, 0, angle=.4)
        self.assertEqual(autorotate([single], {single.uid}), 0)
        self.assertEqual(single.angle, .4)
        points[0].angle = .8
        autorotate(points, {points[2].uid})
        self.assertEqual(points[0].angle, .8)

    def test_snap_waypoint_clusters_preserve_rows_and_do_not_chain_collapse(self):
        points = [point(0, 0), point(.8, 0), point(1.6, 0), point(10, 0)]
        ids = {p.uid for p in points}
        original_angles = [p.angle for p in points]
        self.assertEqual(snap_positions(points, ids, distance=1), 2)
        self.assertEqual([p.x for p in points], [.4, .4, 1.6, 10])
        self.assertEqual([p.angle for p in points], original_angles)
        snap_positions(points, ids)
        self.assertEqual(len(points), 4)
        self.assertTrue(all(p.x == 3.1 for p in points))

    def test_chamber_snap_aligns_chambers_not_robot_centres_and_round_trips(self):
        points = [point(599200, 6615300), point(599200.4, 6615304.4)]
        original = chambers(points)
        a = next(c for c in original if c.uid == points[0].uid and c.side == "left")
        b = next(c for c in original if c.uid == points[1].uid and c.side == "right")
        expected = ((a.x + b.x) / 2, (a.y + b.y) / 2)
        matched = snap_chambers(points, {p.uid for p in points}, 1.5)
        self.assertEqual(len(matched), 1)
        actual = {(c.uid, c.side): (c.x, c.y) for c in chambers(points)}
        for item in (a, b):
            self.assertAlmostEqual(actual[item.uid, item.side][0], expected[0], places=8)
            self.assertAlmostEqual(actual[item.uid, item.side][1], expected[1], places=8)
        self.assertAlmostEqual(math.hypot(points[0].x - points[1].x, points[0].y - points[1].y), 4)
        self.assertTrue(all(p.angle == 0 for p in points))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapped.csv"
            write_csv(path, points)
            self.assertEqual([(c.side, c.x, c.y) for c in chambers(read_csv(path))], [(c.side, c.x, c.y) for c in chambers(points)])

    def test_chamber_pairing_is_disjoint_and_respects_side_and_distance(self):
        points = [point(0, 0), point(.4, 4.4), point(20, 0), point(20.4, 4.4),
                  point(0, .1, side="none"), point(0, 0, kind="DriveThrough")]
        matched = snap_chambers(points, {p.uid for p in points}, 1.5, "opposite")
        self.assertEqual(len(matched), 2)
        used = [c.uid for pair in matched for c in pair[:2]]
        self.assertEqual(len(used), len(set(used)))
        self.assertEqual(snap_chambers(points, {p.uid for p in points}, .01, "left"), [])
        with self.assertRaises(ValueError):
            snap_chambers(points, {p.uid for p in points}, 0)

    def test_squares_have_ground_dimensions_and_handle_duplicate_chambers(self):
        points = [point(0, 0, side="left"), point(6, 0, side="left")]
        squares = measurement_squares(points)
        self.assertEqual(len(squares), 2)
        corners = squares[0][1]
        self.assertAlmostEqual(math.dist(corners[0], corners[1]), 6)
        fixed = measurement_squares(points, fixed_size=2.5)
        self.assertAlmostEqual(math.dist(fixed[0][1][0], fixed[0][1][1]), 2.5)
        duplicates = measurement_squares([point(0, 0), point(0, 0)])
        self.assertTrue(all(math.dist(poly[0], poly[1]) > 0 for _, poly in duplicates))
        self.assertEqual(measurement_squares([point(0, 0, kind="Stop")]), [])
        with self.assertRaises(ValueError):
            measurement_squares(points, fixed_size=0)

    def test_square_field_alignment_matches_rotated_rectangle(self):
        angle = .37
        coordinates = [(x * math.cos(angle) - y * math.sin(angle),
                        x * math.sin(angle) + y * math.cos(angle)) for x, y in ((0, 0), (10, 0), (10, 3), (0, 3))]
        actual = field_angle(coordinates)
        self.assertAlmostEqual(math.sin(4 * (actual - angle)), 0, places=10)


if __name__ == "__main__":
    unittest.main()
