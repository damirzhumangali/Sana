"""Remove duplicate raw detections without merging independently tracked objects."""

from app.events.tracking import area


def suppress_object_duplicates(observations, iou_threshold):
    kept, suppressed = [], []
    for observation in sorted(observations, key=lambda row: row["confidence"], reverse=True):
        duplicate = None
        for other in kept:
            if observation["label"] != other["label"]:
                continue
            # Existing external IDs may describe a separate part of a stack.
            if observation.get("track_id") is not None or other.get("track_id") is not None:
                continue
            a, b = observation["bbox"], other["bbox"]
            intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
                0, min(a[3], b[3]) - max(a[1], b[1])
            )
            iou = intersection / (area(a) + area(b) - intersection)
            if iou >= iou_threshold:
                duplicate = {**observation, "suppression_iou": iou, "kept_bbox": other["bbox"]}
                break
        if duplicate is None:
            kept.append(observation)
        else:
            suppressed.append(duplicate)
    return kept, suppressed
