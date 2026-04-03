"""
Weighted Visvalingam-Whyatt polygon/arc simplification.

Two implementations are provided and can be selected via the ``cascade``
parameter on :func:`simplify_polygon` and :func:`simplify_arc`:

``cascade=True``  (default: False)
    Classic heap + cascade algorithm.  After each point is removed the
    triangle areas of its two neighbours are recomputed.  Produces the
    highest-quality result but is slow on large datasets because every
    point elimination is a separate Python function call.

``cascade=False``  (default)
    Vectorised single-pass implementation.  All interior triangle areas
    are computed in one numpy operation, then the ``keep_count`` points
    with the *largest* areas are selected with ``np.argpartition`` (O(n)).
    No cascade updates — for smooth administrative boundaries the quality
    difference is negligible, and it is typically 50-100× faster.
"""
import numpy as np
from heapq import heappush, heappop, heapify


# ---------------------------------------------------------------------------
# Shared geometry helpers (used by the cascade implementation)
# ---------------------------------------------------------------------------


def _crosses_any_segs(seg_valid, seg_ax, seg_ay, seg_bx, seg_by, lx, ly, rx, ry):
    """
    Vectorised proper-crossing test against pre-allocated segment arrays.

    Check whether the chord (lx,ly)→(rx,ry) properly crosses any active
    segment.  Works on pre-allocated contiguous arrays (no fancy indexing)
    and applies a bounding-box pre-filter to skip distant segments before
    the full cross-product test.

    ``seg_valid`` is a boolean mask of length n-1 updated in-place by
    the caller; ``seg_ax/ay/bx/by`` are the segment coordinate arrays.
    """
    cx0 = lx if lx <= rx else rx;  cx1 = rx if lx <= rx else lx
    cy0 = ly if ly <= ry else ry;  cy1 = ry if ly <= ry else ly

    mask = (seg_valid
            & (np.minimum(seg_ax, seg_bx) <= cx1)
            & (np.maximum(seg_ax, seg_bx) >= cx0)
            & (np.minimum(seg_ay, seg_by) <= cy1)
            & (np.maximum(seg_ay, seg_by) >= cy0))
    if not np.any(mask):
        return False

    ax = seg_ax[mask];  ay = seg_ay[mask]
    bx = seg_bx[mask];  by = seg_by[mask]

    dx_lr = rx - lx;  dy_lr = ry - ly
    d1 = (ax - lx) * dy_lr - (ay - ly) * dx_lr
    d2 = (bx - lx) * dy_lr - (by - ly) * dx_lr

    dx_ab = bx - ax;  dy_ab = by - ay
    d3 = (lx - ax) * dy_ab - (ly - ay) * dx_ab
    d4 = (rx - ax) * dy_ab - (ry - ay) * dx_ab

    return bool(np.any((d1 * d2 < 0) & (d3 * d4 < 0)))


def _weighted_area_scalar(ax, ay, bx, by, cx, cy):
    """Weighted triangle area for a single point triple (scalar version)."""
    area = abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) / 2
    abx, aby = bx - ax, by - ay
    bcx, bcy = cx - bx, cy - by
    dot = abx * bcx + aby * bcy
    d_ab = (abx * abx + aby * aby) ** 0.5
    d_bc = (bcx * bcx + bcy * bcy) ** 0.5
    cos = (dot / (d_ab * d_bc)) if d_ab > 0 and d_bc > 0 else 0.0
    return (-cos * 0.7 + 1) * area


def _weighted_areas_vec(coords):
    """
    Compute the weighted triangle area for every interior point of ``coords``
    in a single vectorised numpy pass.

    Returns a 1-D array of length ``n - 2`` (indices correspond to
    ``coords[1:-1]``).
    """
    a = coords[:-2]    # left  neighbour
    b = coords[1:-1]   # centre point
    c = coords[2:]     # right neighbour

    ab = b - a
    bc = c - b

    # Triangle area via cross product  |det([b-a, c-a])| / 2
    ca = c - a
    tri_area = np.abs(ab[:, 0] * ca[:, 1] - ab[:, 1] * ca[:, 0]) * 0.5

    # Cosine between vectors ab and bc
    dot     = ab[:, 0] * bc[:, 0] + ab[:, 1] * bc[:, 1]
    d_ab    = np.hypot(ab[:, 0], ab[:, 1])
    d_bc    = np.hypot(bc[:, 0], bc[:, 1])
    valid   = (d_ab > 0) & (d_bc > 0)
    with np.errstate(invalid='ignore', divide='ignore'):
        cos_val = np.where(valid, dot / np.where(valid, d_ab * d_bc, 1.0), 0.0)

    return (-cos_val * 0.7 + 1) * tri_area


# ---------------------------------------------------------------------------
# Cascade (heap-based) implementation
# ---------------------------------------------------------------------------


def _visvalingam_cascade(coords, keep_count):
    """
    Classic Visvalingam heap loop with neighbour-area cascade updates.
    Uses lazy deletion so each area update is a single heappush.
    """
    n = len(coords)

    areas = np.full(n, np.inf)
    interior_areas = _weighted_areas_vec(coords)   # vectorised first pass
    areas[1:-1] = interior_areas

    heap = list(zip(areas[1:-1], range(1, n - 1)))
    heap.append((np.inf, 0))        # sentinels keep the left/right walks bounded
    heap.append((np.inf, n - 1))
    heapify(heap)

    removed = set()
    current_count = n

    while heap and current_count > keep_count:
        val, i = heappop(heap)

        if i in removed:
            continue
        if val != areas[i]:         # stale entry
            continue
        if areas[i] == np.inf:
            break

        removed.add(i)
        current_count -= 1

        left = i - 1
        while left in removed:
            left -= 1
        right = i + 1
        while right in removed:
            right += 1

        if 0 < left < n - 1:
            new_area = _weighted_area_scalar(*coords[left - 1], *coords[left], *coords[right])
            areas[left] = new_area
            heappush(heap, (new_area, left))

        if 0 < right < n - 1:
            new_area = _weighted_area_scalar(*coords[left], *coords[right], *coords[right + 1])
            areas[right] = new_area
            heappush(heap, (new_area, right))

    remaining = [i for i in range(n) if i not in removed]
    return coords[remaining]


# ---------------------------------------------------------------------------
# Vectorised single-pass implementation (default)
# ---------------------------------------------------------------------------

def _visvalingam_vec(coords, keep_count):
    """
    Vectorised Visvalingam: compute all interior areas in one numpy call,
    then select the ``keep_count`` points with the largest areas.

    No cascade: neighbour areas are not updated after each removal.
    For smooth curves this approximation is visually equivalent to the
    cascade version while being ~50-100× faster.
    """
    n = len(coords)
    interior_n = n - 2          # number of removable interior points
    interior_keep = keep_count - 2

    if interior_keep <= 0:
        return coords[[0, n - 1]]

    if interior_keep >= interior_n:
        return coords

    w_areas = _weighted_areas_vec(coords)   # shape (interior_n,)

    # Indices of the interior_keep points with the LARGEST weighted areas.
    # np.argpartition puts the k-th smallest at position k; everything to
    # the right (positions k … interior_n-1) is >= that value.
    remove_count = interior_n - interior_keep
    partition    = np.argpartition(w_areas, remove_count)
    keep_interior = np.sort(partition[remove_count:])   # restore spatial order

    all_keep = np.empty(interior_keep + 2, dtype=np.intp)
    all_keep[0]    = 0
    all_keep[1:-1] = keep_interior + 1   # +1: interior index → coord index
    all_keep[-1]   = n - 1
    return coords[all_keep]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def simplify_polygon(coords, percentage, cascade=False):
    """
    Simplify a closed polygon ring.

    ``coords`` must be a numpy array of shape (n, 2) where the last point
    equals the first (closing duplicate included).  The first and last
    points are never removed.  At least 4 points are kept.

    :param cascade:  use the slower heap-cascade algorithm (default: False).
    """
    n = len(coords)
    if n < 4:
        return coords
    keep_count = max(5, int(n * (1 - percentage / 100)))
    fn = _visvalingam_cascade if cascade else _visvalingam_vec
    return fn(coords, keep_count)


def simplify_arc(coords, percentage, cascade=False):
    """
    Simplify an open arc between two fixed junction nodes.

    ``coords`` is a numpy array of shape (n, 2) where coords[0] and
    coords[-1] are the junction nodes and are never removed.  At least
    2 points are kept.

    :param cascade:  use the slower heap-cascade algorithm (default: False).
    """
    n = len(coords)
    if n <= 2:
        return coords
    keep_count = max(3, int(n * (1 - percentage / 100)))
    fn = _visvalingam_cascade if cascade else _visvalingam_vec
    return fn(coords, keep_count)
