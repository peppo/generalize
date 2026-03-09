import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from qgis.core import *
from qgis.PyQt.QtCore import *
from qgis.PyQt.QtWidgets import QApplication
import numpy as np

# Initialize QGIS
QgsApplication.setPrefixPath('/usr', True)  # Adjust for your QGIS installation
app = QApplication([])
QgsApplication.initQgis()

# Load test data
layer_path = os.path.join(os.path.dirname(__file__), 'test_data', 'nad27', 'popctr_state1970.shp')
layer = QgsVectorLayer(layer_path, 'test_layer', 'ogr')
if not layer.isValid():
    print("Layer failed to load!")
    sys.exit(1)

print(f"Loaded layer with {layer.featureCount()} features")

# Test simplification on first feature
for feature in layer.getFeatures():
    geom = feature.geometry()
    if geom.type() == QgsWkbTypes.PolygonGeometry:
        polygon = geom.asPolygon()[0]  # Outer ring
        coords = np.array([(p.x(), p.y()) for p in polygon])
        print(f"Original points: {len(coords)}")
        simplified = simplify_polygon(coords, 50)  # 50% reduction
        print(f"Simplified points: {len(simplified)}")
        break

QgsApplication.exitQgis()