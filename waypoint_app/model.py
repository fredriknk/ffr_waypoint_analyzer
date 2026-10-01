"""CSV format and edit history, independent of the user interface."""
from __future__ import annotations

import csv
import math
import os
import re
import tempfile
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

TYPES = ("Measure", "DriveThrough", "TurningPoint", "Stop")
SIDES = ("both", "left", "right", "none")
PREFIXES = dict(zip(TYPES, ("Plot", "DrThr", "TurningPoint", "Stop")))


@dataclass
class Waypoint:
    x: float
    y: float
    z: float
    angle: float
    kind: str
    side: str
    name: str
    uid: str = field(default_factory=lambda: uuid4().hex)
    original: tuple[str, ...] | None = field(default=None, repr=False)

    def fields(self) -> list[str]:
        values = (self.x, self.y, self.z, self.angle)
        # An untouched coordinate keeps its original precision and representation.
        numbers = [self.original[i] if self.original and float(self.original[i]) == v
                   else repr(v) for i, v in enumerate(values)]
        return numbers + [self.kind, self.side, self.name]

    def signature(self) -> tuple:
        return self.x, self.y, self.z, self.angle, self.kind, self.side, self.name

    def validate(self) -> None:
        if not all(math.isfinite(v) for v in (self.x, self.y, self.z, self.angle)):
            raise ValueError("Coordinates and heading must be finite numbers.")
        if self.kind not in TYPES:
            raise ValueError(f"Unknown waypoint type: {self.kind!r}.")
        if self.side not in SIDES:
            raise ValueError(f"Unknown measurement side: {self.side!r}.")
        if not self.name.strip():
            raise ValueError("Waypoint name cannot be empty.")


def read_csv(path: Path) -> list[Waypoint]:
    result = []
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        for number, row in enumerate(csv.reader(stream), 1):
            if not row or not any(v.strip() for v in row):
                continue
            if len(row) != 7:
                raise ValueError(f"Line {number}: expected 7 columns, found {len(row)}.")
            fields = tuple(v.strip() for v in row)
            try:
                wp = Waypoint(*map(float, fields[:4]), *fields[4:], original=fields)
                wp.validate()
            except ValueError as error:
                raise ValueError(f"Line {number}: {error}") from error
            result.append(wp)
    if not result:
        raise ValueError("This file contains no waypoints.")
    return result


def write_csv(path: Path, points: list[Waypoint]) -> None:
    """Replace a destination atomically, so a failed write cannot truncate it."""
    for point in points:
        point.validate()
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            csv.writer(stream, lineterminator="\n").writerows(p.fields() for p in points)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def numbered(name: str) -> tuple[str, int] | None:
    match = re.fullmatch(r"(.+)_([0-9]+)", name)
    return (match[1], int(match[2])) if match else None


class Document:
    def __init__(self) -> None:
        self.points: list[Waypoint] = []
        self.source: Path | None = None
        self.saved_path: Path | None = None
        self.saved_signature: tuple = ()
        self.undo_entries: list[tuple[str, list[Waypoint], list[Waypoint]]] = []
        self.redo_entries: list[tuple[str, list[Waypoint], list[Waypoint]]] = []

    def signature(self) -> tuple:
        return tuple(p.signature() for p in self.points)

    @property
    def dirty(self) -> bool:
        return self.signature() != self.saved_signature

    def load(self, path: Path) -> None:
        points = read_csv(path)
        self.points = points
        self.source = Path(path).resolve()
        self.saved_path = None
        self.saved_signature = self.signature()
        self.undo_entries.clear()
        self.redo_entries.clear()

    def snapshot(self) -> list[Waypoint]:
        return deepcopy(self.points)

    def record(self, label: str, before: list[Waypoint]) -> None:
        if before == self.points:
            return
        self.undo_entries.append((label, before, self.snapshot()))
        self.undo_entries = self.undo_entries[-100:]
        self.redo_entries.clear()

    def undo(self) -> None:
        if self.undo_entries:
            entry = self.undo_entries.pop()
            self.points = deepcopy(entry[1])
            self.redo_entries.append(entry)

    def redo(self) -> None:
        if self.redo_entries:
            entry = self.redo_entries.pop()
            self.points = deepcopy(entry[2])
            self.undo_entries.append(entry)

    def index(self, uid: str | None) -> int | None:
        return next((i for i, p in enumerate(self.points) if p.uid == uid), None)

    def _style(self, kind: str) -> str:
        for point in self.points:
            if point.kind == kind and (parts := numbered(point.name)):
                return parts[0]
        return PREFIXES[kind]

    def _next_number(self, kind: str, index: int) -> int:
        for point in reversed(self.points[:index]):
            if point.kind == kind and (parts := numbered(point.name)):
                return parts[1] + 1
        for point in self.points[index:]:
            if point.kind == kind and (parts := numbered(point.name)):
                return parts[1]
        return 1

    def _renumber_tail(self, kind: str, index: int, number: int, prefix: str) -> None:
        # Earlier names and unrelated waypoint families remain intact. Insertion
        # shifts the following family; deletion closes its numbering gap.
        for point in self.points[index:]:
            if point.kind == kind:
                point.name = f"{prefix}_{number}"
                number += 1

    def insert(self, index: int, point: Waypoint) -> None:
        if not 0 <= index <= len(self.points):
            raise ValueError("Insertion position is outside the route.")
        prefix = self._style(point.kind)
        number = self._next_number(point.kind, index)
        point.name = f"{prefix}_{number}"
        point.validate()
        self.points.insert(index, point)
        self._renumber_tail(point.kind, index, number, prefix)

    def delete(self, index: int) -> Waypoint:
        removed = self.points.pop(index)
        if parts := numbered(removed.name):
            self._renumber_tail(removed.kind, index, parts[1], parts[0])
        return removed

    def change_type(self, index: int, kind: str) -> None:
        if kind not in TYPES:
            raise ValueError(f"Unknown waypoint type: {kind!r}.")
        if self.points[index].kind == kind:
            return
        point = self.delete(index)
        point.kind = kind
        self.insert(index, point)

    def save_as(self, path: Path) -> None:
        path = Path(path).resolve()
        if self.source and (path == self.source or
                            (path.exists() and os.path.samefile(path, self.source))):
            raise ValueError("Choose a new filename to keep the original waypoint file intact.")
        write_csv(path, self.points)
        self.saved_path = path
        self.saved_signature = self.signature()
