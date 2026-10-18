from app.services.person_roles import assign_role, assign_single_cashier, point_in_polygon


def test_point_in_polygon():
    square = [(0.1, 0.1), (0.4, 0.1), (0.4, 0.4), (0.1, 0.4)]
    assert point_in_polygon((0.2, 0.2), square)
    assert not point_in_polygon((0.7, 0.2), square)


def test_roles_follow_camera_zones():
    zones = {
        "cashier": [(0.3, 0.4), (0.7, 0.4), (0.7, 0.8), (0.3, 0.8)],
        "customer": [(0.0, 0.1), (0.3, 0.1), (0.3, 0.6), (0.0, 0.6)],
    }
    assert assign_role((180, 390, 270, 580), 464, 848, zones) == "cashier"
    assert assign_role((20, 220, 120, 430), 464, 848, zones) == "customer"
    assert assign_role((340, 100, 400, 200), 464, 848, zones) == "unknown"


def test_ambiguous_zone_does_not_guess_role():
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    assert assign_role((10, 10, 20, 20), 100, 100, {"cashier": square, "customer": square}) == (
        "unknown"
    )


def test_only_one_cashier_and_remaining_real_people_are_customers():
    zones = {"cashier": [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)]}
    people = [
        {"box_xyxy": [40, 40, 56, 74], "counted": True},
        {"box_xyxy": [20, 20, 40, 50], "counted": True},
        {"box_xyxy": [0, 0, 10, 10], "counted": True},
        {"box_xyxy": [40, 40, 56, 74], "counted": False},
    ]
    assign_single_cashier(people, 100, 100, zones)
    assert [p["role"] for p in people] == ["cashier", "customer", "customer", "unknown"]


def test_no_cashier_candidate_leaves_roles_unresolved():
    zones = {"cashier": [(0.5, 0.5), (1, 0.5), (1, 1), (0.5, 1)]}
    people = [{"box_xyxy": [0, 0, 10, 10], "counted": True}]
    assign_single_cashier(people, 100, 100, zones)
    assert people[0]["role"] == "unknown"
