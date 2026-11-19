from types import SimpleNamespace

from ppt_agent.v2.source_conflicts import conflicts


def store(*pages):
    return SimpleNamespace(documents=[{"name": "doc1", "pages": [{"page": n, "text": t} for n, t in pages]}])


SUMMARY = "截至2025 年12 月，我国网民规模达11.25 亿人，较2024 年12 月增长1717\n万人，互联网普及率达80.1%。"
BODY = "截至2025 年12 月，我国网民规模达11.25 亿人，较2024 年12 月增长17179\n万人，互联网普及率达80.1%。"


def test_same_clause_with_another_figure_is_a_conflict():
    found = conflicts({"lead": "较2024年12月增长17179万人。"}, store((9, SUMMARY), (19, BODY)))
    assert [(f["figure"], f["other"], f["other_page"]) for f in found] == [("17179万人", "1717万人", 9)]


def test_a_shared_phrase_of_another_measure_is_not():
    other = "我国手机网民规模达11.21亿人，较2024 年12 月增长1563万人。"
    assert conflicts({"lead": "较2024年12月增长1717万人"}, store((9, SUMMARY), (20, other))) == []


def test_the_same_figure_twice_is_not():
    assert conflicts({"lead": "增长1717万人"}, store((9, SUMMARY), (10, SUMMARY))) == []


def test_chart_labels_after_the_same_caption_are_not():
    one = "来源：中国互联网络发展状况统计调查2025.12男,50.3%女,49.7%"
    two = "来源：中国互联网络发展状况统计调查2025.12男,51.2%女,48.8%"
    assert conflicts({"items": ["男性占50.3%"]}, store((25, one), (50, two))) == []


def test_running_footers_are_not_statements():
    pages = [(n, f"Electric cars sold strongly.\nGlobal EV Outlook 2025 Outlook PAGE | {n} IEA. CC BY 4.0.") for n in (99, 100, 101, 102)]
    assert conflicts({"lead": "第100页 100IE"}, store(*pages)) == []
