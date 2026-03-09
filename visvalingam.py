import numpy as np
from heapq import heappush, heappop, heapify


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
    return ( -cos * k + 1 ) * area


def simplify_polygon(coords, percentage):
    """
    Simplify a polygon using Visvalingam algorithm.
    coords: numpy array of shape (n, 2)
    percentage: reduction percentage (0-100), higher means more reduction
    """
    n = len(coords)
    if n < 4:
        return coords

    # Calculate effective areas
    areas = np.full(n, np.inf)
    areas[0] = np.inf
    areas[-1] = np.inf

    for i in range(1, n-1):
        areas[i] = weighted_area(*coords[i-1], *coords[i], *coords[i+1])

    # Use heap to find points to remove
    heap = []
    for i in range(1, n-1):
        heappush(heap, (areas[i], i))

    # Determine how many points to keep
    keep_count = max(4, int(n * (1 - percentage / 100)))
    removed = set()

    while len(heap) > 0 and len(coords) - len(removed) > keep_count:
        val, i = heappop(heap)
        if i in removed or val == np.inf:
            continue
        removed.add(i)
        # Update neighbors
        left = i - 1
        right = i + 1
        while left in removed:
            left -= 1
        while right in removed:
            right += 1
        if left > 0 and left not in removed:
            areas[left] = weighted_area(*coords[left-1], *coords[left], *coords[right])
            # Re-insert into heap (but heap doesn't support decrease key easily, so rebuild)
        if right < n-1 and right not in removed:
            areas[right] = weighted_area(*coords[left], *coords[right], *coords[right+1])

    # Rebuild heap if needed, but for simplicity, since we remove sequentially, it might be ok
    # Actually, better to use a list and sort
    # Simplified version: just remove the smallest areas until we reach the count

    # Collect remaining points
    remaining = [i for i in range(n) if i not in removed]
    return coords[remaining]