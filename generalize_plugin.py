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
        self.action = QAction(icon, 'Generalize Polygons…', self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.vectorMenu().addAction(self.action)

    def unload(self):
        self.iface.vectorMenu().removeAction(self.action)

    def run(self):
        dialog = GeneralizeDialog(self.iface)
        dialog.exec_()