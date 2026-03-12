"""
Bootstrap QGIS for use outside of the QGIS application.

Usage
-----
Import this module ONCE at the very top of any standalone script or test
before importing anything from qgis.*:

    import qgis_init          # sets paths and starts QgsApplication
    from qgis.core import ...  # safe to use from here on

The module is idempotent: subsequent imports are no-ops.
"""
import os
import sys

# ---------------------------------------------------------------------------
# Paths  (edit QGIS_ROOT if your installation differs)
# ---------------------------------------------------------------------------
QGIS_ROOT = r'C:\Program Files\QGIS 3.40.15'
_APP      = os.path.join(QGIS_ROOT, 'apps', 'qgis-ltr')
_PYTHON   = os.path.join(QGIS_ROOT, 'apps', 'Python312')

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------
_qgs_instance = None   # must stay alive for the lifetime of the process


def _bootstrap():
    """Add paths and env-vars; call once before any qgis.* import."""

    # --- sys.path ---
    for p in [
        os.path.join(_APP, 'python'),                       # qgis bindings
        os.path.join(_PYTHON, 'Lib', 'site-packages'),      # numpy etc.
    ]:
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)

    # --- DLL search dirs (Windows Python 3.8+ ignores PATH for DLLs) ---
    if sys.platform == 'win32':
        for d in [
            os.path.join(QGIS_ROOT, 'bin'),
            os.path.join(_APP, 'bin'),
            os.path.join(QGIS_ROOT, 'apps', 'qt5', 'bin'),
            os.path.join(QGIS_ROOT, 'apps', 'gdal', 'bin'),
        ]:
            if os.path.isdir(d):
                os.add_dll_directory(d)

    # --- environment variables ---
    _setenv('QGIS_PREFIX_PATH', _APP)
    _setenv('GDAL_DATA',  os.path.join(QGIS_ROOT, 'apps', 'gdal', 'share', 'gdal'))
    _setenv('PROJ_LIB',   os.path.join(QGIS_ROOT, 'share', 'proj'))
    _setenv('QT_QPA_PLATFORM', 'offscreen')   # headless – no display needed


def _setenv(key, value):
    if key not in os.environ:
        os.environ[key] = value


def _start_qgis():
    global _qgs_instance
    if _qgs_instance is not None:
        return

    from qgis.core import QgsApplication   # noqa: PLC0415 (import not at top)
    _qgs_instance = QgsApplication([], False)
    QgsApplication.setPrefixPath(_APP, True)
    _qgs_instance.initQgis()


# ---------------------------------------------------------------------------
# Run on import
# ---------------------------------------------------------------------------
_bootstrap()
_start_qgis()
