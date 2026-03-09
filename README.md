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

## API

You can use the generalization function programmatically:

```python
from generalize.api import generalize_polygon_layer

layer = iface.activeLayer()  # or any polygon layer
generalized_layer, orig_count, new_count = generalize_polygon_layer(layer, 50)
```

## Testing

Test data is included in `test_data/nad27/popctr_state1970.shp`. Run `test_generalize.py` to test the algorithm.

## License

[GNU General Public License v3.0](LICENSE)

## Initial Prompt

This should become a QGIS plugin in Python that generalizes a polygon layer. The vertices of the polygons are reduced so that less memory is used. The shape should be preserved as much as possible and there should be no gaps or overlaps between the polygons.

There is a JavaScript project at https://github.com/mbloch/mapshaper  
MapShaper uses the "Visvalingam/weighted area" algorithm which produces very good results. I want to make this algorithm available in QGIS as a plugin so that it can be used and called there. The algorithm is in mapshaper\src\simplify\mapshaper-visvalingam.mjs and should be rebuilt in Python.

When the plugin is called from the interface, a dialog should open. There you can select a layer loaded in QGIS. There is also a slider to set by how much percent (100%-0%) the number of points should be reduced.

In the directory workspace/verwaltungsgrenzen there are sample data that I want to generalize. These can be used as test data. Smaller test data is also under mapshaper\test\data\shapefile\nad27\popctr_state1970.shp

Steps:  
1. Create a minimal QGIS plugin  
2. Create the dialog  
3. Load a dropdown with the layers  
4. Write code for preparing the data from the layer and subsequently applying the Visvalingam algorithm  
5. The output occurs in a new in-memory layer in QGIS  

The new plugin should be created in the generalize directory. The mapshaper directory is only a template. Feel free to look around there.  
The steps are a suggestion, but decide yourself. Let's talk about it before you start.

## License

[MIT License](LICENSE) - Add a LICENSE file if needed.

## Contributing

Feel free to open issues or pull requests.
