import os

from qgis.PyQt.QtCore import QTranslator, QCoreApplication
from qgis.core import QgsApplication

_translator = None   # module-level reference prevents garbage collection


def classFactory(iface):
    global _translator
    locale = QgsApplication.locale()[:2]   # 'de', 'en', 'fr', …
    qm = os.path.join(os.path.dirname(__file__), 'i18n', f'generalize_{locale}.qm')
    _translator = QTranslator()
    if _translator.load(qm):
        QCoreApplication.installTranslator(_translator)
    from .generalize_plugin import GeneralizePlugin
    return GeneralizePlugin(iface)
