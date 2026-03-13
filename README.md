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
2. Link the folder `C:\Users\<username>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\` to your workspace 
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

Run the integration tests (QGIS must be installed at `C:\Program Files\QGIS 3.40.15`):

```
"C:\Program Files\QGIS 3.40.15\apps\Python312\python.exe" generalize/test_topology.py
```

**Test data** is not included in this repository due to file size.  Download one of the following and place it at `generalize/test_data/verwaltungsgrenzen_vermessung/VerwaltungsEinheit.shp`:

- **Verwaltungsgebiete 1:25 000** (BKG, Germany-wide):
  https://gdz.bkg.bund.de/index.php/default/digitale-geodaten/verwaltungsgebiete/verwaltungsgebiete-1-25-000-stand-31-12-vg25.html
- **Verwaltung Bayern OpenData** (Bavaria only):
  https://geodaten.bayern.de/opengeodata/OpenDataDetail.html?pn=verwaltung

## License

[GNU General Public License v3.0](LICENSE)

## Initial Prompt

This should become a QGIS plugin in Python that generalizes a polygon layer. The vertices of the polygons are reduced so that less memory is used. The shape should be preserved as much as possible and there should be no gaps or overlaps between the polygons.

There is a JavaScript project at https://github.com/mbloch/mapshaper  
MapShaper uses the "Visvalingam/weighted area" algorithm which produces very good results. I want to make this algorithm available in QGIS as a plugin so that it can be used and called there. The algorithm is in mapshaper\src\simplify\mapshaper-visvalingam.mjs and should be rebuilt in Python.

When the plugin is called from the interface, a dialog should open. There you can select a layer loaded in QGIS. There is also a slider to set by how much percent (100%-0%) the number of points should be reduced.

In the directory generalize/test_data/verwaltungsgrenzen there is sample data that I want to generalize. 

Steps:  
1. Create a minimal QGIS plugin  
2. Create the dialog  
3. Load a dropdown with the layers  
4. Write code for preparing the data from the layer and subsequently applying the Visvalingam algorithm  
5. The output occurs in a new in-memory layer in QGIS  

The new plugin should be created in the generalize directory. The mapshaper directory is only a template. Feel free to look around there.  
The steps are a suggestion, but decide yourself. Let's talk about it before you start.

## More Prompt
Lets continue to work on the generalize project. We need to do some refacting, because it produces slivers (gaps) between the polygons. When the layer is loaded, we want to read all the features and store them in a data class, which hold the polygons in a topological manner. When polygons share a border, there must be only one representation for the line with the common points. Create the datastructure which shall hold the topological polygons and explain what you did. Ask questions if unclear.

## License

GNU LICENSE

## Contributing

Feel free to open issues or pull requests.
