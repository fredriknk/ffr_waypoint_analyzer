# Waypoint Studio

A native Python / Qt editor for robot waypoint routes, with offline satellite imagery.
The original experimental reviewer remains available in `analyze_coords.py`.

![Waypoint Studio showing the supplied route on local satellite imagery](docs/editor_preview.png)

## Run

From this project directory in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python waypoint_editor.py
```

You can also run it without activating the environment:

```powershell
.\.venv\Scripts\python.exe waypoint_editor.py
```

The editor opens `Waypoints/capture_long_ny_gps.csv` initially. Use **Open CSV** or
the example route dropdown to choose another file. Optional launch settings:

```powershell
python waypoint_editor.py Waypoints/Soil2Milk_hel.csv --crs EPSG:32632 --tiles static/Mapnik
```

## Review and edit

- **Navigate:** scroll to zoom around the cursor; drag empty map space or right-drag to pan.
  **Fit route** (`F`) shows the whole route. Double-click a route row to focus a waypoint.
- **Select:** click a waypoint on the map or a row in the route list. The list shows
  the actual robot driving order. Search filters the list without changing the route.
- **Move:** drag a waypoint with the **Move** tool, or enter X and Y in the inspector.
- **Rotate:** drag the white handle extending from the selected waypoint. With the
  **Rotate** tool, drag from a waypoint toward its desired heading. The inspector also
  accepts a heading in degrees. Zero is grid east; positive rotation is counterclockwise
  toward grid north. The exported CSV still stores radians.
- **Change properties:** choose type or measurement side, or edit coordinates/Z/heading.
  Numeric edits apply on Enter or when you leave the field. Types are `Measure`,
  `DriveThrough`, `TurningPoint`, and `Stop`.
- **Add:** choose type, side, and **Append to end**, **Insert before selected**, or
  **Insert after selected**. Click **Place waypoint on map**, then click its location.
  For insertion, you can instead use **Insert at segment midpoint**. A new waypoint
  inherits Z and heading from the selected waypoint; a midpoint uses the adjacent
  points’ average Z and circular mean heading. Set the desired properties in the inspector.
- **Delete:** use the inspector’s Delete button or press Delete while the map/list has focus.
- **Undo/redo:** `Ctrl+Z`, `Ctrl+Y`, or `Ctrl+Shift+Z`. A complete drag is one undo step.
  `Esc` cancels placement or restores the waypoint before a drag.
- **Save:** **Save As** (`Ctrl+S`) exports a new CSV, initially suggesting `_edited.csv`.
  The app refuses to overwrite the CSV you originally opened. Existing export files
  use the normal Save As overwrite confirmation. Closing or opening another route
  prompts you to save unsaved changes.

Structural edits automatically adjust names of the affected types. For example,
inserting a Measure after `Plot_15` creates `Plot_16`, and subsequent plots become
`Plot_17`, `Plot_18`, etc. Drive-throughs and turning points have independent counters.
Existing prefix spelling (`DrThr_`, including alternative spellings in imported files)
and names before the insertion remain intact. Deletion closes the subsequent gap;
changing type updates both families. Loading, moving, rotating, or saving alone does
not renumber anything. Names after a structural edit become consecutive even if the
original route had gaps.

## Coordinates and imagery

The supplied files match **WGS84 / UTM zone 32N (`EPSG:32632`)**. X/Y and Z remain in
the original coordinate system; the map display transforms them to Web Mercator.
The **Map setup** panel accepts another projected CRS in metres when needed. Changing
that setting changes how coordinates are interpreted, rather than rewriting the CSV.
Angles are interpreted relative to the coordinate system’s grid axes.

Local imagery is read directly from `static/Mapnik/{zoom}/{x}/{y}.jpg` (XYZ convention,
not TMS). PNG and JPEG tiles are also supported. Tiles are loaded only for the visible
map and cached; coarser images fill missing detailed tiles. No online map service or
browser is required. Areas outside the download show a dark background and a message.

Optional measurement chamber markers use the original geometry: 0.2 metres ahead
of the robot and 2 metres to each selected side. Map names are automatically spaced
to reduce overlap; the selected waypoint’s name is always shown.

## CSV format

No header, seven columns, in driving order:

```text
X,Y,Z,Angle,Type,MeasureSide,Name
599204.292028,6615298.6725,0.0,-1.16016834102,Measure,both,Plot_1
```

The first line above documents the columns; it is **not** written to exported files.
Measurement sides are `both`, `left`, `right`, or `none`. Unchanged numeric fields keep
their original textual precision. Blank lines in inputs are ignored. Invalid rows,
nonfinite numbers, and unsupported types/sides produce an error with the line number.
Exports use an atomic file replacement to avoid a truncated route if saving fails.

## Checks

```powershell
python -m unittest discover -s tests -v
```

These cover insertion/deletion/type naming, undo/redo and saved state, source protection,
CSV round trips through every supplied example, invalid input, projection round trips,
imagery coverage, and tile fallback. Headless Qt interaction checks also exercise dragging,
rotation, cancellation, zoom anchoring, inspector edits, map insertion/deletion, filtered
selection, and export with pending edits. Runtime dependencies for the original reviewer
are also kept in `requirements.txt`.
