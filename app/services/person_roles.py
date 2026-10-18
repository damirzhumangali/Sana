"""Assign visible people to camera zones; this does not identify individuals."""

from collections.abc import Mapping, Sequence

Point = tuple[float, float]
Box = tuple[float, float, float, float]


def reflection_overlap(box: Box, polygon: Sequence[Point]) -> float:
    """Exact fraction of a normalized box covered by a reflection polygon."""
    x1, y1, x2, y2 = box
    area = (x2 - x1) * (y2 - y1)
    if area <= 0:
        return 0.0
    clipped = list(polygon)
    for axis, boundary, direction in ((0, x1, 1), (0, x2, -1), (1, y1, 1), (1, y2, -1)):
        source, clipped = clipped, []
        if not source:
            return 0.0
        previous = source[-1]
        for current in source:
            previous_inside = direction * (previous[axis] - boundary) >= 0
            current_inside = direction * (current[axis] - boundary) >= 0
            if current_inside != previous_inside:
                ratio = (boundary - previous[axis]) / (current[axis] - previous[axis])
                clipped.append(
                    tuple(previous[i] + ratio * (current[i] - previous[i]) for i in (0, 1))
                )
            if current_inside:
                clipped.append(current)
            previous = current
    twice_area = sum(
        a[0] * b[1] - b[0] * a[1] for a, b in zip(clipped, clipped[1:] + clipped[:1], strict=True)
    )
    return min(1.0, abs(twice_area) / (2 * area))


def is_reflection_candidate(box: Box, width: int, height: int, regions) -> bool:
    """Only suppress boxes almost completely within a calibrated glass region."""
    normalized = (box[0] / width, box[1] / height, box[2] / width, box[3] / height)
    return any(reflection_overlap(normalized, polygon) >= 0.97 for polygon in regions)


def point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    """Return whether a normalized point is inside a camera zone."""
    x, y = point
    inside = False
    previous_x, previous_y = polygon[-1]
    for current_x, current_y in polygon:
        if (current_y > y) != (previous_y > y):
            crossing_x = current_x + (y - current_y) * (previous_x - current_x) / (
                previous_y - current_y
            )
            if x < crossing_x:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return inside


def assign_role(
    box: Box, frame_width: int, frame_height: int, zones: Mapping[str, Sequence[Point]]
) -> str:
    """Use the box center; overlapping or outside zones remain unknown."""
    x1, y1, x2, y2 = box
    center = ((x1 + x2) / (2 * frame_width), (y1 + y2) / (2 * frame_height))
    matches = [role for role, polygon in zones.items() if point_in_polygon(center, polygon)]
    return matches[0] if len(matches) == 1 else "unknown"


def assign_single_cashier(
    people: list[dict], width: int, height: int, zones, anchor: Point = (0.48, 0.57)
) -> None:
    """At most one cashier, chosen near a calibrated seat; others are customers.

    This is a per-frame location heuristic, not person tracking. If no candidate
    occupies the cashier zone, leave real people's roles unresolved.
    """
    candidates = []
    for index, person in enumerate(people):
        person["role"] = "unknown"
        if not person["counted"]:
            continue
        x1, y1, x2, y2 = person["box_xyxy"]
        center = ((x1 + x2) / (2 * width), (y1 + y2) / (2 * height))
        if point_in_polygon(center, zones["cashier"]):
            distance = (center[0] - anchor[0]) ** 2 + (center[1] - anchor[1]) ** 2
            candidates.append((distance, index))
    if not candidates:
        return
    candidates.sort()
    # Do not pick arbitrarily when two people are equally plausible.
    if len(candidates) > 1 and abs(candidates[0][0] - candidates[1][0]) < 0.0001:
        return
    selected = candidates[0][1]
    for index, person in enumerate(people):
        if person["counted"]:
            person["role"] = "cashier" if index == selected else "customer"
