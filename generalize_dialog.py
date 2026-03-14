from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSlider, QPushButton, QMessageBox
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes, QgsTask, QgsApplication, QgsMessageLog, Qgis

from .api import generalize_polygon_layer, _make_layer

# Keep strong Python references to running tasks so the GC does not collect
# them before finished() is called (the C++ task manager only holds a C++ ref).
_active_tasks = []


class _GeneralizeTask(QgsTask):
    def __init__(self, layer, percentage, iface):
        super().__init__(
            f"Generalizing '{layer.name()}' ({percentage}% reduction)",
            QgsTask.CanCancel,
        )
        self.input_layer = layer
        self.percentage = percentage
        self.iface = iface
        # Capture layer metadata on the main thread before the task starts.
        self.crs_authid = layer.crs().authid()
        self.output_name = layer.name() + '_generalized'
        self.fields = layer.fields()
        # Results set by run(), consumed by finished().
        self.features = None
        self.original_count = 0
        self.new_count = 0
        self.exception = None

    def run(self):
        def progress_callback(pct):
            self.setProgress(pct)
            return self.isCanceled()

        try:
            result = generalize_polygon_layer(
                self.input_layer,
                self.percentage,
                progress_callback=progress_callback,
                add_to_project=False,
            )
        except Exception as e:
            self.exception = e
            return False

        features, original_count, new_count = result
        if features is None:
            return False  # cancelled

        self.features = features
        self.original_count = original_count
        self.new_count = new_count
        return True

    def finished(self, result):
        if self.isCanceled():
            self.iface.messageBar().pushInfo("Generalize", "Generalization cancelled.")
            return

        if not result:
            msg = str(self.exception) if self.exception else "Unknown error"
            QgsMessageLog.logMessage(f"Generalization failed: {msg}", "Generalize", Qgis.Critical)
            self.iface.messageBar().pushCritical("Generalize", f"Failed: {msg}")
            return

        # Create the layer on the main thread to avoid Qt thread-affinity issues.
        new_layer = QgsVectorLayer(
            f'Polygon?crs={self.crs_authid}',
            self.output_name,
            'memory',
        )
        new_layer.dataProvider().addAttributes(self.fields)
        new_layer.updateFields()
        new_layer.dataProvider().addFeatures(self.features)
        QgsProject.instance().addMapLayer(new_layer)

        if self.new_count < self.original_count:
            self.iface.messageBar().pushWarning(
                "Generalize",
                f"{self.original_count - self.new_count} feature(s) collapsed and were removed.",
            )


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
        task = _GeneralizeTask(layer, percentage, self.iface)
        _active_tasks.append(task)

        def _cleanup():
            if task in _active_tasks:
                _active_tasks.remove(task)

        task.taskCompleted.connect(_cleanup)
        task.taskTerminated.connect(_cleanup)

        QgsApplication.taskManager().addTask(task)
        super().accept()
