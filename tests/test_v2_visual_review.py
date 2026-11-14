import asyncio
import io

from PIL import Image

from ppt_agent.v2 import visual_review
from ppt_agent.v2.visual_review import Version


def png(colour, mark=None):
    image = Image.new("RGB", (160, 90), colour)
    if mark:
        image.paste(mark, (20, 20, 120, 70))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


PLAIN, CHART, PHOTO = png("#f3eee4"), png("#f3eee4", "#c5341c"), png("#f3eee4", "#3b6fd4")


class Reviewer:
    """Scores by what the picture is, wherever it is put; remembers what it was told."""

    def __init__(self, marks, veto=None):
        self.marks, self.veto, self.users, self.systems = marks, veto or {}, [], []

    async def complete_json(self, *, task, system, user, images, **kwargs):
        self.users.append(user)
        self.systems.append(system)
        versions = []
        for label, (_, data) in zip("ABCDEF", images[1:]):
            import base64
            key = base64.b64decode(data)
            versions.append({"label": label, "scores": {name: self.marks[key] for name in visual_review.CRITERIA},
                             "veto": self.veto.get(key, []), "issues": []})
        best = max(versions, key=lambda entry: entry["scores"]["polish"])["label"]
        return {"versions": versions, "best": best}


def run(reviewer, versions, incumbent="P0"):
    result = asyncio.run(visual_review.review_page(reviewer, facts={"title": "t"}, versions=versions, reference_png=PLAIN))
    return result, visual_review.choose(result, incumbent)


def test_the_reviewer_is_never_told_where_a_version_came_from_and_order_changes():
    reviewer = Reviewer({PLAIN: 3, CHART: 4, PHOTO: 2})
    result, _ = run(reviewer, [Version("P0", PLAIN), Version("P1", CHART), Version("P1.5", PHOTO)])
    assert result["looks"] == 5 and not any(name in text for text in reviewer.users for name in ("P0", "P1", "P1.5"))
    assert "既不加分也不减分" in reviewer.systems[0]
    assert result["versions"]["P1"]["overall"] == 4 and result["versions"]["P1"]["wins"] == 5


def test_the_page_in_place_stays_unless_another_is_clearly_better():
    close, _ = None, None
    _, kept = run(Reviewer({PLAIN: 3.8, CHART: 4.0}), [Version("P0", PLAIN), Version("P1", CHART)])
    assert kept["keep"] == "P0" and not kept["changed"] and "差距不足" in kept["why"]
    _, moved = run(Reviewer({PLAIN: 3, CHART: 4}), [Version("P0", PLAIN), Version("P1", CHART)])
    assert moved["keep"] == "P1" and moved["changed"] and moved["lead"] == 1.0


def test_a_wrong_fact_is_a_veto_not_a_low_score():
    versions = [Version("P0", PLAIN), Version("P1", CHART), Version("P1.5", PHOTO, vetoes=["重画把并列写成了包含"])]
    result, kept = run(Reviewer({PLAIN: 3, CHART: 4, PHOTO: 5}, veto={CHART: ["柱高与数值不符"]}), versions)
    assert result["versions"]["P1"]["vetoes"] and result["versions"]["P1.5"]["vetoes"] == ["重画把并列写成了包含"]
    assert kept["keep"] == "P0" and not kept["changed"]
    _, forced = run(Reviewer({PLAIN: 3, CHART: 4}), [Version("P0", PLAIN, vetoes=["数字错"]), Version("P1", CHART)])
    assert forced["keep"] == "P1" and forced["changed"]


def test_the_same_picture_under_two_names_is_judged_once():
    reviewer = Reviewer({PLAIN: 3, CHART: 4})
    result, kept = run(reviewer, [Version("P0", PLAIN), Version("P1", CHART), Version("P1.5", PLAIN)])
    assert "版本 A、B。" in reviewer.users[0]
    assert result["versions"]["P1.5"]["same_picture_as"] == ["P0", "P1.5"]
    assert result["versions"]["P0"] == result["versions"]["P1.5"]
