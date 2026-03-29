from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from .generalize_dialog import GeneralizeDialog
import os.path


class GeneralizePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

    def initGui(self):
        icon = QIcon(os.path.join(self.plugin_dir, 'icon.svg'))
        self.action = QAction(icon, QCoreApplication.translate('GeneralizePlugin', 'Generalize Polygons…'), self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.vectorMenu().addAction(self.action)

        from .processing_provider import GeneralizeProvider
        from qgis.core import QgsApplication
        self._provider = GeneralizeProvider()
        QgsApplication.processingRegistry().addProvider(self._provider)

    def unload(self):
        from qgis.core import QgsApplication
        QgsApplication.processingRegistry().removeProvider(self._provider)
        self.iface.vectorMenu().removeAction(self.action)

    def run(self):
        dialog = GeneralizeDialog(self.iface)
        dialog.exec()