"""Launch the offline waypoint editor: python waypoint_editor.py [route.csv]."""
import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Offline robot waypoint map editor")
    parser.add_argument("file", nargs="?", type=Path, help="Waypoint CSV to open")
    parser.add_argument("--tiles", type=Path, default=Path(__file__).resolve().parent / "static" / "Mapnik",
                        help="Local XYZ image tile directory")
    parser.add_argument("--crs", default="EPSG:32632", help="Waypoint projected CRS (default: WGS84 / UTM 32N)")
    args = parser.parse_args()
    try:
        from PySide6.QtWidgets import QApplication
        from waypoint_app.window import MainWindow
    except ImportError as error:
        parser.exit(1, f"Missing dependency: {error}\nRun: python -m pip install -r requirements.txt\n")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Waypoint Studio")
    app.setOrganizationName("Field Robotics")
    try:
        window = MainWindow(args.tiles, args.crs, args.file)
    except (ValueError, RuntimeError) as error:
        parser.exit(1, f"Cannot start editor: {error}\n")
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
