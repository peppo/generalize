"""
Bootstrap QGIS for use outside of the QGIS application.

Usage
-----
Import this module ONCE at the very top of any standalone script or test
before importing anything from qgis.*:

    import qgis_init          # sets paths and starts QgsApplication
    from qgis.core import ...  # safe to use from here on

The module is idempotent: subsequent imports are no-ops.

QGIS version selection
----------------------
By default the first installation found in ``_CANDIDATES`` is used.
Override by setting the ``QGIS_ROOT`` environment variable before importing:

    set QGIS_ROOT=C:\\Program Files\\QGIS 4.0.0
"""
import glob
import os
import sys

# ---------------------------------------------------------------------------
# Paths — auto-detected; override with QGIS_ROOT env var
# ---------------------------------------------------------------------------

_CANDIDATES = [
    r'C:\Program Files\QGIS 3.40.15',
    r'C:\Program Files\QGIS 4.0.0',
]


def _detect_qgis_root() -> str:
    env = os.environ.get('QGIS_ROOT')
    if env and os.path.isdir(env):
        return env
    for c in _CANDIDATES:
        if os.path.isdir(c):
            return c
    raise RuntimeError(
        "QGIS installation not found. Set the QGIS_ROOT environment variable "
        "to the root of your QGIS installation (e.g. "
        r"C:\Program Files\QGIS 3.40.15)."
    )


QGIS_ROOT = _detect_qgis_root()

# QGIS 3.x ships qgis-ltr; QGIS 4.x ships qgis
_APP = (
    os.path.join(QGIS_ROOT, 'apps', 'qgis-ltr')
    if os.path.isdir(os.path.join(QGIS_ROOT, 'apps', 'qgis-ltr'))
    else os.path.join(QGIS_ROOT, 'apps', 'qgis')
)

# Detect bundled Python directory (Python312, Python313, …)
_python_dirs = sorted(glob.glob(os.path.join(QGIS_ROOT, 'apps', 'Python3*')))
_PYTHON = _python_dirs[-1] if _python_dirs else os.path.join(QGIS_ROOT, 'apps', 'Python312')

# Qt5 (QGIS 3.x) or Qt6 (QGIS 4.x)
_qt_bin = os.path.join(QGIS_ROOT, 'apps', 'qt5', 'bin')
if not os.path.isdir(_qt_bin):
    _qt_bin = os.path.join(QGIS_ROOT, 'apps', 'qt6', 'bin')

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------
_qgs_instance = None   # must stay alive for the lifetime of the process


def _bootstrap():
    """Add paths and env-vars; call once before any qgis.* import."""

    # --- sys.path ---
    for p in [
        os.path.join(_APP, 'python'),                       # qgis bindings
        os.path.join(_APP, 'python', 'plugins'),            # processing etc.
        os.path.join(_PYTHON, 'Lib', 'site-packages'),      # numpy etc.
    ]:
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)

    # --- DLL search dirs (Windows Python 3.8+ ignores PATH for DLLs) ---
    if sys.platform == 'win32':
        for d in [
            os.path.join(QGIS_ROOT, 'bin'),
            os.path.join(_APP, 'bin'),
            _qt_bin,
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

    # Set project CRS to EPSG:25832 so no on-the-fly reprojection happens
    # when loading layers that are already in that CRS.
    from qgis.core import QgsProject, QgsCoordinateReferenceSystem  # noqa: PLC0415
    QgsProject.instance().setCrs(QgsCoordinateReferenceSystem('EPSG:25832'))

    # Register native algorithms so that `import processing` works outside GUI.
    from qgis.analysis import QgsNativeAlgorithms  # noqa: PLC0415
    QgsApplication.processingRegistry().addProvider(QgsNativeAlgorithms())


# ---------------------------------------------------------------------------
# Run on import
# ---------------------------------------------------------------------------
_bootstrap()
_start_qgis()
