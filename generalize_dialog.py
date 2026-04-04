import processing

from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSlider, QDoubleSpinBox, QPushButton, QMessageBox, QCheckBox
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes, QgsTask, QgsApplication, QgsMessageLog, Qgis

# PyQt6 moved orientation/geometry enums into sub-namespaces; PyQt5 kept them
# on the top-level class.  Resolve once at import time so the rest of the file
# is version-agnostic.
try:
    _Qt_Horizontal = Qt.Orientation.Horizontal          # PyQt6
except AttributeError:
    _Qt_Horizontal = Qt.Horizontal                      # PyQt5

try:
    _PolygonGeometry = QgsWkbTypes.GeometryType.PolygonGeometry   # QGIS 4
except AttributeError:
    _PolygonGeometry = QgsWkbTypes.PolygonGeometry                # QGIS 3


from .api import generalize_polygon_layer, _make_layer

# Keep strong Python references to running tasks so the GC does not collect
# them before finished() is called (the C++ task manager only holds a C++ ref).
_active_tasks = []


class _GeneralizeTask(QgsTask):
    def __init__(self, layer, percentage, iface, repair=True, constrained=False, dissolve_small=False, repair_inversions=False):
        super().__init__(
            f"Generalizing '{layer.name()}' ({percentage}% reduction)",  # task manager label, not translated
            QgsTask.CanCancel,
        )
        self.input_layer = layer
        self.percentage = percentage
        self.iface = iface
        self.repair = repair
        self.dissolve_small = dissolve_small
        self.repair_inversions = repair_inversions
        # Capture layer metadata on the main thread before the task starts.
        self.crs_authid = layer.crs().authid()
        suffixes = ['generalized', f'{percentage:.3g}']
        if dissolve_small:
            suffixes.append('island')
        if repair_inversions:
            suffixes.append('inversion')
        self.output_name = layer.name() + '_' + '_'.join(suffixes)
        self.fields = layer.fields()
        # Results set by run(), consumed by finished().
        self.features = None
        self.original_count = 0
        self.new_count = 0
        self.exception = None
        self.repaired_features = None

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
                dissolve_small=self.dissolve_small,
                repair_inversions=self.repair_inversions,
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

        if self.repair:
            invalid_count = sum(1 for f in features if not f.geometry().isGeosValid())
            if invalid_count > 0:
                temp = QgsVectorLayer(f'Polygon?crs={self.crs_authid}', '_temp', 'memory')
                temp.dataProvider().addAttributes(self.fields)
                temp.updateFields()
                temp.dataProvider().addFeatures(features)
                res = processing.run('native:fixgeometries', {
                    'INPUT': temp,
                    'OUTPUT': 'memory:',
                })
                self.repaired_features = list(res['OUTPUT'].getFeatures())

        return True

    def finished(self, result):
        if self.isCanceled():
            self.iface.messageBar().pushInfo("Generalize", self.tr("Generalization cancelled."))
            return

        if not result:
            msg = str(self.exception) if self.exception else "Unknown error"
            QgsMessageLog.logMessage(f"Generalization failed: {msg}", "Generalize", Qgis.Critical)
            self.iface.messageBar().pushCritical("Generalize", self.tr("Failed: ") + msg)
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

        if self.repaired_features is not None:
            repaired_layer = QgsVectorLayer(
                f'Polygon?crs={self.crs_authid}',
                self.output_name + '_repaired',
                'memory',
            )
            repaired_layer.dataProvider().addAttributes(self.fields)
            repaired_layer.updateFields()
            repaired_layer.dataProvider().addFeatures(self.repaired_features)
            QgsProject.instance().addMapLayer(repaired_layer)

        if self.new_count < self.original_count:
            self.iface.messageBar().pushWarning(
                "Generalize",
                self.tr("{count} feature(s) collapsed and were removed.").format(
                    count=self.original_count - self.new_count
                ),
            )


class GeneralizeDialog(QDialog):
    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self.setWindowTitle(self.tr('Generalize Polygons'))
        self.setModal(True)
        self.layout = QVBoxLayout()

        # Layer selection
        self.layer_label = QLabel(self.tr('Select Polygon Layer:'))
        self.layer_combo = QComboBox()
        self.populate_layers()
        self.layout.addWidget(self.layer_label)
        self.layout.addWidget(self.layer_combo)

        # Percentage slider + spinbox
        self.slider_label = QLabel(self.tr('Reduction Percentage:'))
        self.slider = QSlider(_Qt_Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(99)
        self.slider.setValue(90)

        self.pct_spinbox = QDoubleSpinBox()
        self.pct_spinbox.setMinimum(0.0)
        self.pct_spinbox.setMaximum(99.9)
        self.pct_spinbox.setDecimals(1)
        self.pct_spinbox.setSingleStep(0.1)
        self.pct_spinbox.setSuffix(' %')
        self.pct_spinbox.setValue(90.0)

        self.slider.valueChanged.connect(self._on_slider_changed)
        self.pct_spinbox.valueChanged.connect(self._on_spinbox_changed)

        self.label_row = QHBoxLayout()
        self.label_row.addWidget(self.slider_label)
        self.label_row.addStretch()
        self.label_row.addWidget(self.pct_spinbox)

        self.layout.addLayout(self.label_row)
        self.layout.addWidget(self.slider)

        # Repair geometry checkbox
        self.repair_checkbox = QCheckBox(self.tr('Repair geometry if necessary'))
        self.repair_checkbox.setChecked(True)
        self.layout.addWidget(self.repair_checkbox)

        # Dissolve small parts/holes checkbox
        self.dissolve_small_checkbox = QCheckBox(self.tr('Dissolve small parts and holes'))
        self.dissolve_small_checkbox.setChecked(False)
        self.layout.addWidget(self.dissolve_small_checkbox)

        # Repair ring inversions checkbox
        self.repair_inversions_checkbox = QCheckBox(self.tr('Repair ring inversions'))
        self.repair_inversions_checkbox.setChecked(False)
        self.layout.addWidget(self.repair_inversions_checkbox)

        # Buttons
        self.button_layout = QHBoxLayout()
        self.ok_button = QPushButton(self.tr('OK'))
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton(self.tr('Cancel'))
        self.cancel_button.clicked.connect(self.reject)
        self.button_layout.addWidget(self.ok_button)
        self.button_layout.addWidget(self.cancel_button)
        self.layout.addLayout(self.button_layout)

        self.setLayout(self.layout)

    def populate_layers(self):
        active = self.iface.activeLayer()
        layers = QgsProject.instance().mapLayers().values()
        for layer in layers:
            if isinstance(layer, QgsVectorLayer) and layer.geometryType() == _PolygonGeometry:
                self.layer_combo.addItem(layer.name(), layer)
                if layer == active:
                    self.layer_combo.setCurrentIndex(self.layer_combo.count() - 1)

    def _on_slider_changed(self, value):
        self.pct_spinbox.blockSignals(True)
        self.pct_spinbox.setValue(99.9 if value == 99 else float(value))
        self.pct_spinbox.blockSignals(False)

    def _on_spinbox_changed(self, value):
        self.slider.blockSignals(True)
        self.slider.setValue(99 if value >= 99.0 else int(round(value)))
        self.slider.blockSignals(False)

    def accept(self):
        layer = self.layer_combo.currentData()
        if not layer:
            QMessageBox.warning(self, self.tr('Error'), self.tr('No layer selected.'))
            return

        percentage = self.pct_spinbox.value()
        repair = self.repair_checkbox.isChecked()
        dissolve_small = self.dissolve_small_checkbox.isChecked()
        repair_inversions = self.repair_inversions_checkbox.isChecked()
        task = _GeneralizeTask(layer, percentage, self.iface, repair=repair, dissolve_small=dissolve_small, repair_inversions=repair_inversions)
        _active_tasks.append(task)

        def _cleanup():
            if task in _active_tasks:
                _active_tasks.remove(task)

        task.taskCompleted.connect(_cleanup)
        task.taskTerminated.connect(_cleanup)

        QgsApplication.taskManager().addTask(task)
        super().accept()
