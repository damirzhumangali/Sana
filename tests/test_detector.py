from app.services.detector import (
    classify_persons,
    is_reflection,
)


def test_reflection_filtering():
    # Box in left glass partition (x < 68) is detected as reflection
    reflection_box = [5.0, 200.0, 50.0, 280.0]
    assert is_reflection(reflection_box) is True

    # Real person box in doorway or at desk is not a reflection
    real_client_box = [130.0, 300.0, 210.0, 420.0]
    assert is_reflection(real_client_box) is False


def test_classify_persons_geometry():
    # Cashier in chair (cy > 450, cx > 120)
    cashier_box = [160.0, 400.0, 260.0, 600.0]
    # Client at desk (cy <= 450, cx >= 160)
    client_box = [170.0, 300.0, 230.0, 420.0]
    # Entering person (cx in [70, 160], cy <= 450)
    entering_box = [90.0, 200.0, 150.0, 380.0]
    # Glass reflection (cx < 68)
    reflection_box = [5.0, 200.0, 50.0, 280.0]

    boxes = [cashier_box, client_box, entering_box, reflection_box]
    classified = classify_persons(boxes)

    assert len(classified["cashier"]) == 1
    assert classified["cashier"][0].role == "cashier"

    assert len(classified["client"]) == 1
    assert classified["client"][0].role == "client"

    assert len(classified["entering"]) == 1
    assert classified["entering"][0].role == "entering"

    # Reflection was cleanly suppressed
    total_classified = (
        len(classified["cashier"]) + len(classified["client"]) + len(classified["entering"])
    )
    assert total_classified == 3
