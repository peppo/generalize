# Generalize Polygons QGIS Plugin

A QGIS plugin that generalizes polygon layers by reducing vertices using the Visvalingam algorithm, preserving shape while reducing storage.

## Features

- Simplifies polygon geometries using the weighted Visvalingam algorithm
- Preserves shape and avoids gaps or overlaps
- Interactive dialog to select layers and set reduction percentage
- Outputs to a new in-memory layer
- Progress bar with cancel option for long operations

## Installation

1. Download or clone this repository
2. Copy the `generalize` folder to your QGIS plugins directory (e.g., `C:\Users\<username>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`)
3. Restart QGIS or reload plugins
4. Enable the plugin in the Plugin Manager

## Usage

1. Load a polygon layer in QGIS
2. Go to Plugins > Generalize > Generalize Polygons
3. Select the layer and set the reduction percentage (0-100%)
4. Click OK to create a generalized in-memory layer

## Algorithm

Based on the Visvalingam algorithm from [MapShaper](https://github.com/mbloch/mapshaper), implemented in Python.

## Testing

Test data is included in `test_data/nad27/popctr_state1970.shp`. Run `test_generalize.py` to test the algorithm.

## License

[MIT License](LICENSE) - Add a LICENSE file if needed.

## Contributing

Feel free to open issues or pull requests.
