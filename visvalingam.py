import numpy as np
from heapq import heappush, heappop


def triangle_area(ax, ay, bx, by, cx, cy):
    return abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) / 2


def cosine(ax, ay, bx, by, cx, cy):
    abx = bx - ax
    aby = by - ay
    bcx = cx - bx
    bcy = cy - by
    dot = abx * bcx + aby * bcy
    dist_ab = np.sqrt(abx**2 + aby**2)
    dist_bc = np.sqrt(bcx**2 + bcy**2)
    if dist_ab == 0 or dist_bc == 0:
        return 0
    return dot / (dist_ab * dist_bc)


def weighted_area(ax, ay, bx, by, cx, cy):
    area = triangle_area(ax, ay, bx, by, cx, cy)
    cos = cosine(ax, ay, bx, by, cx, cy)
    k = 0.7
    return (-cos * k + 1) * area


def _visvalingam(coords, keep_count):
    """
    Core Visvalingam heap loop.

    Removes interior points (indices 1 … n-2) until ``keep_count`` remain.
    Index 0 and index n-1 are fixed (area = inf) and are never removed.

    Uses lazy deletion: when a neighbour's area is updated, the new value is
    pushed onto the heap.  Stale entries are discarded on pop by comparing
    the popped value against the current ``areas`` array.
    """
    n = len(coords)

    areas = np.full(n, np.inf)
    for i in range(1, n - 1):
        areas[i] = weighted_area(*coords[i - 1], *coords[i], *coords[i + 1])

    heap = [(areas[i], i) for i in range(1, n - 1)]
    heappush(heap, (np.inf, 0))       # sentinels so left/right walk stays in bounds
    heappush(heap, (np.inf, n - 1))

    # heapify once instead of n individual pushes
    from heapq import heapify
    heapify(heap)

    removed = set()
    current_count = n

    while heap and current_count > keep_count:
        val, i = heappop(heap)

        if i in removed:
            continue                    # already gone
        if val != areas[i]:
            continue                    # stale entry – re-pushed with updated area
        if areas[i] == np.inf:
            break                       # only fixed endpoints remain

        removed.add(i)
        current_count -= 1

        # Find the nearest surviving neighbours
        left = i - 1
        while left in removed:
            left -= 1
        right = i + 1
        while right in removed:
            right += 1

        # Update neighbour areas and push fresh entries (lazy deletion)
        if 0 < left < n - 1:
            new_area = weighted_area(*coords[left - 1], *coords[left], *coords[right])
            areas[left] = new_area
            heappush(heap, (new_area, left))

        if 0 < right < n - 1:
            new_area = weighted_area(*coords[left], *coords[right], *coords[right + 1])
            areas[right] = new_area
            heappush(heap, (new_area, right))

    remaining = [i for i in range(n) if i not in removed]
    return coords[remaining]


def simplify_polygon(coords, percentage):
    """
    Simplify a closed polygon ring using the weighted Visvalingam algorithm.

    ``coords`` must be a numpy array of shape (n, 2) where the last point
    equals the first (closing duplicate included), as returned by QGIS.

    The first and last points are fixed (they are the same coordinate).
    At least 4 points are kept so the ring remains a valid polygon.
    """
    n = len(coords)
    if n < 4:
        return coords
    keep_count = max(4, int(n * (1 - percentage / 100)))
    return _visvalingam(coords, keep_count)


def simplify_arc(coords, percentage):
    """
    Simplify an open arc between two fixed junction nodes.

    ``coords`` is a numpy array of shape (n, 2) where coords[0] and
    coords[-1] are the junction nodes and must not be moved or removed.

    At least 2 points are kept (the two endpoints).  Short arcs (n <= 2)
    are returned unchanged.
    """
    n = len(coords)
    if n <= 2:
        return coords
    keep_count = max(2, int(n * (1 - percentage / 100)))
    return _visvalingam(coords, keep_count)
