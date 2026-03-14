# Generalize Polygons — QGIS Plugin

Topology-preserving polygon simplification using the weighted Visvalingam-Whyatt algorithm. Reduces vertex count while guaranteeing no slivers, gaps, or overlaps between adjacent polygons.

## Features

- **Topology-preserving** — shared borders between adjacent polygons are simplified exactly once, so both neighbours always receive the same simplified edge. No slivers, no gaps.
- **Weighted Visvalingam-Whyatt algorithm** — the same algorithm used by [MapShaper](https://github.com/mbloch/mapshaper). Triangle areas are weighted by the angle at the centre vertex, so nearly-collinear points are removed first while geometrically significant vertices are protected.
- **Geometry validity check** — the input layer is checked for invalid geometries before processing. Invalid features are rejected with a clear error message.
- **Optional geometry repair** — if the output contains invalid geometries, the built-in QGIS *Fix Geometries* tool is applied automatically and the repaired layer is added to the project alongside the generalized one.
- **Background processing** — runs as a QGIS background task so the interface stays responsive. A progress bar tracks the three internal phases (topology build, simplification, reconstruction) and the task can be cancelled at any time.

## Installation

### From ZIP (recommended)

1. Download the latest `generalize-<version>.zip` from the [Releases](https://github.com/peppo/generalize/releases) page
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**
3. Select the downloaded zip and click *Install Plugin*

### From source (development)

1. Clone this repository into your QGIS plugin folder:
   ```
   cd %APPDATA%\QGIS\QGIS3\profiles\default\python\plugins
   git clone https://github.com/peppo/generalize.git
   ```
2. Restart QGIS and enable the plugin in the Plugin Manager

## Usage

1. Load a polygon layer in QGIS
2. Go to **Vector → Generalize Polygons…**
3. Select the layer and set the reduction percentage (0–100 %)
4. Optionally check *Repair geometry if necessary*
5. Click **OK** — the generalized layer is added to the project when done

## API

The generalization function can also be called from the QGIS Python console or another plugin:

```python
from generalize.api import generalize_polygon_layer

layer = iface.activeLayer()  # or any polygon vector layer
generalized_layer, orig_count, new_count = generalize_polygon_layer(layer, 50)
```

## Development & Testing

Run the integration tests (requires QGIS 3.40 installed at the default path):

```
"C:\Program Files\QGIS 3.40.15\apps\Python312\python.exe" generalize/tests/test_topology.py
```

**Test data** is not included in this repository due to file size. Download one of the following and place it at `generalize/test_data/verwaltungsgrenzen_vermessung/VerwaltungsEinheit.shp`:

- **Verwaltungsgebiete 1:25 000** (BKG, Germany-wide):
  https://gdz.bkg.bund.de/index.php/default/digitale-geodaten/verwaltungsgebiete/verwaltungsgebiete-1-25-000-stand-31-12-vg25.html
- **Verwaltung Bayern OpenData** (Bavaria only):
  https://geodaten.bayern.de/opengeodata/OpenDataDetail.html?pn=verwaltung

## Contributing

Bug reports and pull requests are welcome on the [issue tracker](https://github.com/peppo/generalize/issues).

## License

[GNU General Public License v3.0](LICENSE)
