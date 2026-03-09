from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSlider, QPushButton, QMessageBox, QProgressDialog
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes
from .api import generalize_polygon_layer


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
        # Progress dialog
        progress = QProgressDialog("Generalizing polygons...", "Cancel", 0, layer.featureCount(), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        def progress_callback(current, total):
            progress.setValue(current)
            progress.setLabelText(f"Processing feature {current+1} of {total}")
            return progress.wasCanceled()

        # Use the API
        new_layer, original_count, new_count = generalize_polygon_layer(layer, percentage, progress_callback=progress_callback)

        progress.setValue(layer.featureCount())

        if progress.wasCanceled():
            QMessageBox.information(self, 'Cancelled', 'Generalization was cancelled.')
            return

        if new_count < original_count:
            QMessageBox.warning(self, 'Warning', f'Some geometries were lost during generalization. Original: {original_count}, Generalized: {new_count}')
