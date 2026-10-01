"""Coordinate transforms and local XYZ tile metadata."""
from __future__ import annotations

import math
from pathlib import Path

from pyproj import CRS, Transformer

WORLD = 40075016.68557849
HALF_WORLD = WORLD / 2


class Projection:
    def __init__(self, crs: str = "EPSG:32632"):
        self.crs = CRS.from_user_input(crs)
        if not self.crs.is_projected or any(a.unit_name != "metre" for a in self.crs.axis_info[:2]):
            raise ValueError("Choose a projected coordinate system in metres, such as EPSG:32632.")
        self.to_map = Transformer.from_crs(self.crs, 3857, always_xy=True)
        self.from_map = Transformer.from_crs(3857, self.crs, always_xy=True)
        self.to_gps = Transformer.from_crs(self.crs, 4326, always_xy=True)

    def scene(self, x: float, y: float) -> tuple[float, float]:
        mx, my = self.to_map.transform(x, y, errcheck=True)
        if not math.isfinite(mx + my) or abs(mx) > HALF_WORLD or abs(my) > HALF_WORLD:
            raise ValueError("Coordinates lie outside the supported map extent. Check the CRS.")
        return mx, -my

    def coordinates(self, mx: float, sy: float) -> tuple[float, float]:
        return self.from_map.transform(mx, -sy, errcheck=True)

    def heading(self, point, length: float = 5) -> tuple[float, float]:
        return self.scene(point.x + math.cos(point.angle) * length,
                          point.y + math.sin(point.angle) * length)


class TileSet:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.levels = sorted(int(p.name) for p in self.root.iterdir()
                             if p.is_dir() and p.name.isdigit() and 0 <= int(p.name) <= 24)
        if not self.levels:
            raise ValueError("No XYZ tiles found. Select the folder containing zoom folders (17, 18, …).")

    def bounds(self) -> tuple[float, float, float, float]:
        z = self.levels[0]
        positions = []
        for folder in (self.root / str(z)).iterdir():
            if folder.is_dir() and folder.name.isdigit():
                positions.extend((int(folder.name), int(p.stem)) for p in folder.iterdir()
                                 if p.stem.isdigit() and p.suffix.lower() in (".jpg", ".png", ".jpeg"))
        if not positions:
            raise ValueError("The lowest zoom folder contains no supported image tiles.")
        size = WORLD / 2**z
        xs, ys = zip(*positions)
        return (-HALF_WORLD + min(xs) * size, -HALF_WORLD + min(ys) * size,
                (max(xs) - min(xs) + 1) * size, (max(ys) - min(ys) + 1) * size)

    def find(self, z: int, x: int, y: int):
        # Coarser imagery fills holes or zoom levels missing from the download.
        for level in reversed(self.levels):
            if level > z:
                continue
            factor = 2 ** (z - level)
            for suffix in (".jpg", ".png", ".jpeg"):
                path = self.root / str(level) / str(x // factor) / f"{y // factor}{suffix}"
                if path.is_file():
                    return path, factor, x % factor, y % factor
        return None

    def covers(self, mx: float, scene_y: float) -> bool:
        z = self.levels[-1]
        size = WORLD / 2**z
        x = math.floor((mx + HALF_WORLD) / size)
        y = math.floor((scene_y + HALF_WORLD) / size)
        return self.find(z, x, y) is not None
