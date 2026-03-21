"""
Performance measurement for generalize_polygon_layer() on gemeinden_bayern.

Run directly (no pytest):
    "C:\\Program Files\\QGIS 3.40.15\\apps\\Python312\\python.exe" tests/measure_performance.py

Prints a phase-by-phase timing breakdown for both constrained=False and
constrained=True at 50% reduction.
"""
import os
import sys
import time

_HERE      = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT  = os.path.dirname(_HERE)
_WORKSPACE = os.path.dirname(_PKG_ROOT)

if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)
if _WORKSPACE not in sys.path:
    sys.path.insert(0, _WORKSPACE)

import qgis_init  # noqa: E402

from processing.core.Processing import Processing
Processing.initialize()

from qgis.core import QgsVectorLayer

_GEMEINDEN_BAYERN = os.path.join(
    _PKG_ROOT, 'test_data', 'gemeinden_bayern', 'VerwaltungsEinheit.shp'
)

PERCENTAGE = 50


def _load_layer(path):
    layer = QgsVectorLayer(path, os.path.basename(path), 'ogr')
    if not layer.isValid():
        raise RuntimeError(f'Could not load: {path}')
    return layer


def run_and_report(label, constrained):
    import generalize.api as _api

    log_entries = []
    _api._log = lambda msg: log_entries.append(msg)

    from generalize.api import generalize_polygon_layer
    layer = _load_layer(_GEMEINDEN_BAYERN)

    t0 = time.perf_counter()
    generalize_polygon_layer(
        layer,
        percentage=PERCENTAGE,
        add_to_project=False,
        constrained=constrained,
    )
    total = time.perf_counter() - t0

    print(f'\n{"=" * 60}')
    print(f'  {label}  (constrained={constrained}, {PERCENTAGE}% reduction)')
    print(f'{"=" * 60}')
    for msg in log_entries:
        print(f'  {msg}'.encode('ascii', errors='replace').decode('ascii'))
    print(f'  -- total wall time: {total:.1f}s')


def run_subset_and_report(label, constrained, n_features=200):
    """Run on the first n_features from gemeinden_bayern for quick iteration."""
    import generalize.api as _api
    from qgis.core import QgsVectorLayer

    log_entries = []
    _api._log = lambda msg: log_entries.append(msg)

    full_layer = _load_layer(_GEMEINDEN_BAYERN)
    subset = QgsVectorLayer(
        f'Polygon?crs={full_layer.crs().authid()}', '_subset', 'memory'
    )
    subset.dataProvider().addAttributes(full_layer.fields())
    subset.updateFields()
    feats = []
    for i, f in enumerate(full_layer.getFeatures()):
        if i >= n_features:
            break
        feats.append(f)
    subset.dataProvider().addFeatures(feats)

    from generalize.api import generalize_polygon_layer
    t0 = time.perf_counter()
    generalize_polygon_layer(
        subset, percentage=PERCENTAGE, add_to_project=False, constrained=constrained,
    )
    total = time.perf_counter() - t0

    print(f'\n{"=" * 60}')
    print(f'  {label}  n={n_features}, constrained={constrained}')
    print(f'{"=" * 60}')
    for msg in log_entries:
        print(f'  {msg}'.encode('ascii', errors='replace').decode('ascii'))
    print(f'  -- total wall time: {total:.1f}s')


if __name__ == '__main__':
    print('\n--- SUBSET (first 200 features) ---')
    run_subset_and_report('FAST (unconstrained)', constrained=False)
    run_subset_and_report('CONSTRAINED',          constrained=True)

    print('\n--- FULL DATASET ---')
    run_and_report('FAST (unconstrained)', constrained=False)
    run_and_report('CONSTRAINED',          constrained=True)
