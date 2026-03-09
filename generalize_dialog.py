from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSlider, QPushButton, QMessageBox, QProgressDialog
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.core import QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsWkbTypes, QgsPointXY
from .visvalingam import simplify_polygon
import numpy as np


class GeneralizeDialog(QDialog):
    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self.setWindowTitle('Generalize Polygons')
        self.setModal(True)
        self.layout = QVBoxLayout()

        # Layer selection
        self.layer_label = QLabel('Select Polygon Layer:')
        self.layer_combo = QComboBox()
        self.populate_layers()
        self.layout.addWidget(self.layer_label)
        self.layout.addWidget(self.layer_combo)

        # Percentage slider
        self.slider_label = QLabel('Reduction Percentage: 50%')
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(100)
        self.slider.setValue(50)
        self.slider.valueChanged.connect(self.update_label)
        self.layout.addWidget(self.slider_label)
        self.layout.addWidget(self.slider)

        # Buttons
        self.button_layout = QHBoxLayout()
        self.ok_button = QPushButton('OK')
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton('Cancel')
        self.cancel_button.clicked.connect(self.reject)
        self.button_layout.addWidget(self.ok_button)
        self.button_layout.addWidget(self.cancel_button)
        self.layout.addLayout(self.button_layout)

        self.setLayout(self.layout)

    def populate_layers(self):
        layers = QgsProject.instance().mapLayers().values()
        for layer in layers:
            if isinstance(layer, QgsVectorLayer) and layer.geometryType() == QgsWkbTypes.PolygonGeometry:
                self.layer_combo.addItem(layer.name(), layer)

    def update_label(self, value):
        self.slider_label.setText(f'Reduction Percentage: {value}%')

    def accept(self):
        layer = self.layer_combo.currentData()
        if not layer:
            QMessageBox.warning(self, 'Error', 'No layer selected.')
            return

        percentage = self.slider.value()
        self.generalize_layer(layer, percentage)
        super().accept()

    def generalize_layer(self, layer, percentage):
        # Create new in-memory layer
        new_layer = QgsVectorLayer('Polygon?crs=' + layer.crs().authid(), f'{layer.name()}_generalized', 'memory')
        new_layer.setCrs(layer.crs())
        new_layer.dataProvider().addAttributes(layer.fields())
        new_layer.updateFields()

        features = list(layer.getFeatures())
        total_features = len(features)
        original_count = total_features
        new_count = 0

        # Progress dialog
        progress = QProgressDialog("Generalizing polygons...", "Cancel", 0, total_features, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        for i, feature in enumerate(features):
            if progress.wasCanceled():
                break
            progress.setValue(i)
            progress.setLabelText(f"Processing feature {i+1} of {total_features}")

            geom = feature.geometry()
            if geom.isMultipart():
                # Handle multipart polygons
                parts = []
                for part in geom.asMultiPolygon():
                    simplified = self.simplify_geometry(part, percentage)
                    if simplified:
                        parts.append(simplified)
                if parts:
                    new_geom = QgsGeometry.fromMultiPolygonXY(parts)
                else:
                    continue  # Skip if no valid parts
            else:
                simplified = self.simplify_geometry(geom.asPolygon(), percentage)
                if simplified:
                    new_geom = QgsGeometry.fromPolygonXY(simplified)
                else:
                    continue

            new_feature = QgsFeature()
            new_feature.setGeometry(new_geom)
            new_feature.setAttributes(feature.attributes())
            new_layer.dataProvider().addFeature(new_feature)
            new_count += 1

        progress.setValue(total_features)

        if progress.wasCanceled():
            QMessageBox.information(self, 'Cancelled', 'Generalization was cancelled.')
            return

        if new_count < original_count:
            QMessageBox.warning(self, 'Warning', f'Some geometries were lost during generalization. Original: {original_count}, Generalized: {new_count}')

        QgsProject.instance().addMapLayer(new_layer)

    def simplify_geometry(self, polygon, percentage):
        if not polygon:
            return None
        # Assume polygon is list of rings, first is outer
        outer_ring = polygon[0]
        if len(outer_ring) < 4:  # Need at least 4 points for a valid polygon
            return polygon

        # Convert to numpy arrays
        coords = np.array([(p.x(), p.y()) for p in outer_ring])
        simplified_coords = simplify_polygon(coords, percentage)
        if len(simplified_coords) < 4:
            return None  # Invalid polygon
        simplified_ring = [QgsPointXY(x, y) for x, y in simplified_coords]
        return [simplified_ring] + polygon[1:]  # Keep inner rings if any