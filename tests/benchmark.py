"""
Performance benchmark for the topological generalisation pipeline.

Purpose
-------
This script measures the wall-clock contribution of each pipeline phase
(topology build, simplification, reconstruction) on the gemeinden_bayern
dataset.  The resulting per-phase percentages are used to calibrate the
progress-bar weight constants W_TOPO / W_SIMP / W_REPAIR in api.py.

Run:
    "C:\\Program Files\\QGIS 3.40.15\\apps\\Python312\\python.exe" tests/benchmark.py

Note: this script does not run the repair passes (repair_ring_inversions /
repair_inter_polygon_crossings).  To time those, use measure_performance.py
which runs the full generalize_polygon_layer() pipeline including repair and
prints a phase-by-phase log.
"""
import os
import sys
import time

# ---------------------------------------------------------------------------
# Bootstrap QGIS
# ---------------------------------------------------------------------------
_HERE     = os.path.dirname(os.path.abspath(__file__))  # .../generalize/tests
_PKG_ROOT = os.path.dirname(_HERE)                       # .../generalize
_WORKSPACE = os.path.dirname(_PKG_ROOT)                  # .../workspace

if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)
if _WORKSPACE not in sys.path:
    sys.path.insert(0, _WORKSPACE)

import qgis_init  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hline():
    print('-' * 60)

def _tick(label, fn, *args, **kwargs):
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = time.perf_counter() - t0
    print(f'  {label:<40s} {elapsed:7.3f} s')
    return result, elapsed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    from qgis.core import QgsVectorLayer
    from generalize.topology_builder import build, to_qgs_features
    from generalize.visvalingam import simplify_arc, simplify_polygon

    shp = os.path.join(
        _PKG_ROOT, 'test_data', 'gemeinden_bayern', 'VerwaltungsEinheit.shp'
    )

    _hline()
    print('Topological generalisation – performance breakdown')
    _hline()

    # 1. Load layer
    layer, t_load = _tick('Load QgsVectorLayer', QgsVectorLayer, shp, 'bench', 'ogr')
    feat_count = layer.featureCount()
    print(f'    {feat_count} features')

    # 2. Build topology
    topo, t_build = _tick('build() – construct TopoLayer', build, layer)
    n_nodes  = len(topo.nodes)
    n_edges  = len(topo.edges)
    n_shared = topo.shared_edge_count
    n_polys  = len(topo.polygons)
    print(f'    nodes={n_nodes}  edges={n_edges}  shared={n_shared}  polygons={n_polys}')

    # Count total input vertices for context
    total_verts_in = sum(len(r['coords']) for r in _peek_rings(layer))
    print(f'    input ring coords = {total_verts_in}')

    # 3. Simplification
    PERCENTAGE = 50

    def _simplify_edges():
        for edge in topo.edges.values():
            is_loop = (edge.start_node == edge.end_node)
            if is_loop:
                edge.coords = simplify_polygon(edge.coords, PERCENTAGE)
            else:
                edge.coords = simplify_arc(edge.coords, PERCENTAGE)

    _, t_simp = _tick(f'simplify edges ({PERCENTAGE}% reduction)', _simplify_edges)

    total_verts_out = sum(len(e.coords) for e in topo.edges.values())
    print(f'    output edge coords = {total_verts_out}  '
          f'({100*(1-total_verts_out/total_verts_in):.1f}% reduction)')

    # 4. Reconstruct features
    features, t_recon = _tick('to_qgs_features() – reconstruct', to_qgs_features, topo)
    print(f'    {len(features)} features reconstructed')

    # Summary
    _hline()
    t_total = t_load + t_build + t_simp + t_recon
    print(f'  {"TOTAL":<40s} {t_total:7.3f} s')
    _hline()

    # Per-phase share
    for label, t in [
        ('Load',        t_load),
        ('Build topo',  t_build),
        ('Simplify',    t_simp),
        ('Reconstruct', t_recon),
    ]:
        print(f'  {label:<40s} {100*t/t_total:5.1f}%')
    _hline()


def _peek_rings(layer):
    """Re-extract raw ring coords for counting (mirrors topology_builder logic)."""
    rings = []
    for feature in layer.getFeatures():
        geom = feature.geometry()
        parts = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
        for polygon in parts:
            for ring_pts in polygon:
                coords = [(p.x(), p.y()) for p in ring_pts[:-1]]
                if len(coords) >= 3:
                    rings.append({'coords': coords})
    return rings


if __name__ == '__main__':
    main()
