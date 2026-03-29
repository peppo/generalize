import os

from qgis.core import (
    QgsApplication,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsProcessingException,
    QgsProcessingProvider,
    QgsProcessing,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon

from .api import generalize_polygon_layer

_PLUGIN_DIR = os.path.dirname(__file__)


class GeneralizeAlgorithm(QgsProcessingAlgorithm):

    INPUT             = 'INPUT'
    PERCENTAGE        = 'PERCENTAGE'
    DISSOLVE_SMALL    = 'DISSOLVE_SMALL'
    REPAIR_INVERSIONS = 'REPAIR_INVERSIONS'
    OUTPUT            = 'OUTPUT'

    def tr(self, string):
        return QCoreApplication.translate('GeneralizeAlgorithm', string)

    def name(self):
        return 'generalizepolygons'

    def displayName(self):
        return self.tr('Generalize polygons (topology-aware)')

    def group(self):
        return ''

    def groupId(self):
        return ''

    def shortHelpString(self):
        return self.tr(
            'Simplifies polygon boundaries using the topology-aware Visvalingam '
            'algorithm. Shared edges between adjacent polygons are simplified '
            'exactly once, so no slivers or gaps are introduced between neighbours.\n\n'
            '<b>Reduction percentage</b>: how aggressively to simplify. '
            '90 % removes 90 % of the vertices. Higher values produce coarser '
            'output; lower values stay closer to the original shape.\n\n'
            '<b>Dissolve small parts and holes</b>: after simplification, removes '
            'polygon parts and holes whose area falls below an automatic threshold '
            '(2 \u00d7 average segment length\u00b2). Useful for cleaning up tiny slivers '
            'or island artefacts. At least one part per feature is always kept.\n\n'
            '<b>Repair ring inversions</b>: if aggressive simplification causes a '
            'ring to self-intersect (fold over itself), this option detects and '
            'corrects the inversion by restoring a small number of original '
            'vertices. Adds processing time on large datasets.'
        )

    def icon(self):
        return QIcon(os.path.join(_PLUGIN_DIR, 'icon.svg'))

    def createInstance(self):
        return GeneralizeAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT, self.tr('Input layer'),
            types=[QgsProcessing.TypeVectorPolygon],
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.PERCENTAGE, self.tr('Reduction percentage (%)'),
            type=QgsProcessingParameterNumber.Double,
            minValue=0.0, maxValue=99.9, defaultValue=90.0,
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.DISSOLVE_SMALL, self.tr('Dissolve small parts and holes'),
            defaultValue=False,
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.REPAIR_INVERSIONS, self.tr('Repair ring inversions'),
            defaultValue=False,
        ))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr('Generalized'),
            type=QgsProcessing.TypeVectorPolygon,
        ))

    def processAlgorithm(self, parameters, context, feedback):
        layer      = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        percentage = self.parameterAsDouble(parameters, self.PERCENTAGE, context)
        dissolve   = self.parameterAsBool(parameters, self.DISSOLVE_SMALL, context)
        repair_inv = self.parameterAsBool(parameters, self.REPAIR_INVERSIONS, context)

        if layer is None:
            raise QgsProcessingException(
                self.invalidSourceError(parameters, self.INPUT)
            )

        def progress_callback(pct):
            feedback.setProgress(pct)
            return feedback.isCanceled()

        try:
            result = generalize_polygon_layer(
                layer, percentage,
                progress_callback=progress_callback,
                add_to_project=False,
                dissolve_small=dissolve,
                repair_inversions=repair_inv,
            )
        except ValueError as e:
            raise QgsProcessingException(str(e))

        features, orig_count, new_count = result

        if features is None:   # cancelled
            return {}

        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            layer.fields(), QgsWkbTypes.MultiPolygon, layer.crs(),
        )
        if sink is None:
            raise QgsProcessingException(
                self.invalidSinkError(parameters, self.OUTPUT)
            )

        for feat in features:
            sink.addFeature(feat)

        collapsed = orig_count - new_count
        if collapsed:
            feedback.pushWarning(
                f'{collapsed} feature(s) collapsed to empty geometry and were removed.'
            )

        return {self.OUTPUT: dest_id}


class GeneralizeProvider(QgsProcessingProvider):

    def tr(self, string):
        return QCoreApplication.translate('GeneralizeProvider', string)

    def id(self):
        return 'generalize'

    def name(self):
        return self.tr('Generalize')

    def longName(self):
        return self.tr('Topology-aware polygon generalisation')

    def icon(self):
        return QIcon(os.path.join(_PLUGIN_DIR, 'icon.svg'))

    def loadAlgorithms(self):
        self.addAlgorithm(GeneralizeAlgorithm())
