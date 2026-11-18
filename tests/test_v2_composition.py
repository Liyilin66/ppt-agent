from ppt_agent.v2.htmldeck import composition as cm, layout_plan
from ppt_agent.v2.htmldeck.layout_plan import Fingerprint


def test_distance_ignores_nothing_but_composition():
    side = cm.Composition("cols", 0.62, 0.43, 0.51, 0.37, 0.37, "display>data")
    same = cm.Composition("cols", 0.65, 0.45, 0.51, 0.45, 0.32, "display>data")
    band = cm.Composition("rows", 0.3, 0.5, 0.55, 0.3, 0.4, "display>data>text")
    assert cm.distance(side, same) < cm.CLOSE < cm.distance(side, band)
    assert cm.close_pairs({4: side, 8: same, 20: same}) == [(4, 8, cm.distance(side, same))]


def _chart(kind="category", points=4, role="primary"):
    return {"series": [{"kind": kind, "points": [{}] * points}], "visual": {"role": role}}


def test_arrangement_follows_the_data_and_turns_between_chart_pages():
    assert layout_plan.arrangements_for(_chart(role="supporting"))[0] == "inset"
    assert layout_plan.arrangements_for(_chart("share", 3)) == ["side", "inset"]
    assert layout_plan.arrangements_for(_chart("share", 6)) == ["side", "full"]
    assert layout_plan.arrangements_for(_chart("category", 8))[0] == "full"
    earlier = [Fingerprint(page=4, form="chart", arrangement="band"), Fingerprint(page=6, form="chart", arrangement="side")]
    assert layout_plan.arrangement(9, _chart("category", 4), earlier) == "full"
    assert layout_plan.arrangement(9, _chart("category", 4), earlier[:1]) == "side"


def test_a_mirror_image_is_the_same_composition():
    left = cm.Composition("cols", 0.62, 0.43, 0.51, 0.37, 0.37, "display>data")
    right = cm.mirrored(left)
    assert right.path == "data>display" and cm.distance(left, right) == 0.0
