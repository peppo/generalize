"""
Performance benchmark for gemeinden_mittelfranken.

Run:
    "C:\\Program Files\\QGIS 3.40.15\\apps\\Python312\\python.exe" tests/perf_mittelfranken.py
    "C:\\Program Files\\QGIS 3.40.15\\apps\\Python312\\python.exe" tests/perf_mittelfranken.py --profile
"""
import os
import sys
import time
import argparse

_HERE      = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT  = os.path.dirname(_HERE)
_WORKSPACE = os.path.dirname(_PKG_ROOT)

if _PKG_ROOT  not in sys.path: sys.path.insert(0, _PKG_ROOT)
if _WORKSPACE not in sys.path: sys.path.insert(0, _WORKSPACE)

import qgis_init  # noqa: E402

PERCENTAGE = 50
SHP = os.path.join(
    _PKG_ROOT, 'test_data', 'gemeinden_mittelfranken',
    'gemeinden_mittelfranken.shp',
)


def _hline():
    print('-' * 65)


def _tick(label, fn, *args, **kwargs):
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = time.perf_counter() - t0
    print(f'  {label:<44s} {elapsed:7.3f} s')
    return result, elapsed


def run():
    from qgis.core import QgsVectorLayer
    from generalize.topology_builder import (
        build, to_qgs_features, repair_ring_inversions,
    )
    from generalize.visvalingam import simplify_arc, simplify_polygon

    _hline()
    print(f'Generalisation benchmark — gemeinden_mittelfranken ({PERCENTAGE}% reduction)')
    _hline()

    # 1. Load
    layer, t_load = _tick('Load QgsVectorLayer', QgsVectorLayer, SHP, 'bench', 'ogr')
    print(f'    {layer.featureCount()} features')

    # 2. Build topology (with sub-phase logging)
    phases = []
    def _phase_cb(msg):
        if phases:
            phases[-1][1] = time.perf_counter()
        phases.append([msg, None])
        print(f'    [{msg}]')

    t_build0 = time.perf_counter()
    topo = build(layer, phase_callback=_phase_cb)
    t_build = time.perf_counter() - t_build0
    if phases and phases[-1][1] is None:
        phases[-1][1] = time.perf_counter()

    print(f'  {"build() total":<44s} {t_build:7.3f} s')
    print(f'    nodes={len(topo.nodes)}  edges={len(topo.edges)}'
          f'  shared={topo.shared_edge_count}  polygons={len(topo.polygons)}')

    # 3. Simplify
    original_edge_coords = {e.id: e.coords.copy() for e in topo.edges.values()}

    def _simplify():
        for edge in topo.edges.values():
            is_loop = (edge.start_node == edge.end_node)
            if is_loop:
                edge.coords = simplify_polygon(edge.coords, PERCENTAGE)
            else:
                edge.coords = simplify_arc(edge.coords, PERCENTAGE)

    _, t_simp = _tick(f'simplify_edges ({PERCENTAGE}%)', _simplify)

    # 4. repair_ring_inversions
    _, t_repair = _tick('repair_ring_inversions', repair_ring_inversions,
                        topo, original_edge_coords)

    # 5. Reconstruct
    _, t_recon = _tick('to_qgs_features', to_qgs_features, topo)

    # Summary
    _hline()
    t_total = t_load + t_build + t_simp + t_repair + t_recon
    print(f'  {"TOTAL":<44s} {t_total:7.3f} s')
    _hline()
    for label, t in [
        ('Load',                  t_load),
        ('Build topology',        t_build),
        ('Simplify edges',        t_simp),
        ('repair_ring_inversions',t_repair),
        ('Reconstruct features',  t_recon),
    ]:
        print(f'  {label:<44s} {100*t/t_total:5.1f}%')
    _hline()


def run_with_profile():
    import cProfile
    import pstats
    import io

    pr = cProfile.Profile()
    pr.enable()
    run()
    pr.disable()

    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats('cumulative')
    ps.print_stats(30)
    print('\n=== cProfile top 30 (cumulative) ===')
    print(s.getvalue())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', action='store_true')
    args = parser.parse_args()

    if args.profile:
        run_with_profile()
    else:
        run()
