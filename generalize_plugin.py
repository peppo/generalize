from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication
from .generalize_dialog import GeneralizeDialog
import os.path


class GeneralizePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

    def initGui(self):
        self.action = QAction('Generalize Polygons', self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu('&Generalize', self.action)

    def unload(self):
        self.iface.removePluginMenu('&Generalize', self.action)

    def run(self):
        dialog = GeneralizeDialog(self.iface)
        dialog.exec_()