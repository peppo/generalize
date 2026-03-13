"""
Diagnose the shared-border representation between Vohburg and Manching.

Run:
    "C:\\Program Files\\QGIS 3.40.15\\apps\\Python312\\python.exe" diagnose_gaps.py
"""
import os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKSPACE = os.path.dirname(_HERE)
if _WORKSPACE not in sys.path:
    sys.path.insert(0, _WORKSPACE)
import qgis_init

import numpy as np
from qgis.core import QgsVectorLayer
from generalize.topology_builder import build, snap_to_self

SHP = os.path.join(_HERE, 'test_data', 'verwaltungsgrenzen_vermessung', 'VerwaltungsEinheit.shp')
layer = QgsVectorLayer(SHP, 'diag', 'ogr')

# -----------------------------------------------------------------------
# 1. Find Vohburg and Manching
# -----------------------------------------------------------------------
vohburg = manching = None
for feat in layer.getFeatures():
    row = ' '.join(str(a) for a in feat.attributes()).lower()
    if 'vohburg' in row and vohburg is None:
        vohburg = feat
    if 'manching' in row and manching is None:
        manching = feat
    if vohburg and manching:
        break

print(f'Vohburg  fid={vohburg.id()}')
print(f'Manching fid={manching.id()}')

# -----------------------------------------------------------------------
# 2. Get boundary points of both
# -----------------------------------------------------------------------
def ring_pts(geom):
    parts = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
    pts = []
    for poly in parts:
        for ring in poly:
            pts.extend((p.x(), p.y()) for p in ring)
    return np.array(pts)

voh = ring_pts(vohburg.geometry())
man = ring_pts(manching.geometry())
print(f'\nVohburg vertex count:  {len(voh)}')
print(f'Manching vertex count: {len(man)}')

# -----------------------------------------------------------------------
# 3. For each Vohburg vertex, compute distance to nearest Manching VERTEX
#    AND distance to nearest Manching EDGE SEGMENT.
# -----------------------------------------------------------------------
BBOX_MARGIN = 500
v_bbox = vohburg.geometry().boundingBox()
m_bbox = manching.geometry().boundingBox()
int_bb = v_bbox.intersect(m_bbox)

def near(pts, bb, margin):
    return pts[
        (pts[:,0] >= bb.xMinimum()-margin) & (pts[:,0] <= bb.xMaximum()+margin) &
        (pts[:,1] >= bb.yMinimum()-margin) & (pts[:,1] <= bb.yMaximum()+margin)
    ]

voh_n = near(voh, int_bb, BBOX_MARGIN)
man_n = near(man, int_bb, BBOX_MARGIN)

# Nearest-vertex distance (as computed before)
diffs = voh_n[:, np.newaxis, :] - man_n[np.newaxis, :, :]
dist_to_vertex = np.hypot(diffs[:,:,0], diffs[:,:,1]).min(axis=1)

# Nearest-edge distance: project each Vohburg pt onto each Manching segment
# Segment from man_n[i] to man_n[i+1]
def dist_to_segments(pts, seg_pts):
    """Distance from each point in pts to the nearest segment of seg_pts."""
    n_pts = len(pts)
    n_seg = len(seg_pts) - 1
    A = seg_pts[:-1]           # (n_seg, 2)
    B = seg_pts[1:]            # (n_seg, 2)
    AB = B - A                 # (n_seg, 2)
    AB2 = (AB**2).sum(axis=1) # (n_seg,)  squared length of each segment

    # For each pt, project onto each segment, clamp to [0, 1]
    # pt - A: (n_pts, n_seg, 2)
    PA = pts[:, np.newaxis, :] - A[np.newaxis, :, :]   # (n_pts, n_seg, 2)
    t  = (PA * AB[np.newaxis, :, :]).sum(axis=2)        # (n_pts, n_seg)
    # clamp t to [0, AB2]
    AB2_safe = np.where(AB2 > 0, AB2, 1.0)
    t = np.clip(t / AB2_safe, 0.0, 1.0)

    # Closest point on segment
    closest = A[np.newaxis, :, :] + t[:, :, np.newaxis] * AB[np.newaxis, :, :]
    diff = pts[:, np.newaxis, :] - closest
    dist = np.hypot(diff[:,:,0], diff[:,:,1])  # (n_pts, n_seg)
    return dist.min(axis=1)

dist_to_edge = dist_to_segments(voh_n, man_n)

# -----------------------------------------------------------------------
# 4. Points close to the shared border (vertex distance < 1 m)
# -----------------------------------------------------------------------
THRESHOLD = 1.0
shared_v = dist_to_vertex < THRESHOLD
shared_e = dist_to_edge   < THRESHOLD

print(f'\nVohburg boundary points (near bbox, n={len(voh_n)}):')
print(f'  within {THRESHOLD}m of a Manching VERTEX: {shared_v.sum()}  '
      f'(max gap={dist_to_vertex[shared_v].max():.6f} m)')
print(f'  within {THRESHOLD}m of a Manching EDGE:   {shared_e.sum()}  '
      f'(max gap={dist_to_edge[shared_e].max():.6f} m)')

# Points that are close to an EDGE but NOT to a VERTEX:
extra_verts = shared_e & ~shared_v
n_extra = extra_verts.sum()
print(f'\n  => {n_extra} Vohburg vertices lie ON a Manching edge segment')
print(     '     but do NOT coincide with any Manching vertex.')
print(     '     These are the "extra" intermediate vertices that prevent')
print(     '     shared-edge detection in our topology builder.')

if n_extra > 0:
    print(f'\n  Extra vertex positions (up to 5 shown):')
    for xy in voh_n[extra_verts][:5]:
        print(f'    ({xy[0]:.3f}, {xy[1]:.3f})')

# -----------------------------------------------------------------------
# 5. Check whether snap_to_self fixes the shared-edge count
# -----------------------------------------------------------------------
print('\nShared edges WITHOUT snap:', build(layer).shared_edge_count)
snapped = snap_to_self(layer, tolerance=1.0)
print('Shared edges AFTER  snap:', build(snapped).shared_edge_count)
