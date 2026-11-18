from pathlib import Path

import pytest

from ppt_agent.v2 import data_series, figure_text as ft


def _w(text, x, y, size=8.0):
    return {"text": text, "x": x, "y": y, "size": size, "w": ft._width(text, size)}


def _bars(monkeypatch, extra=()):
    words = [_w("豆包", 111, 562), _w("DeepSeek", 95, 531), _w("腾讯元宝", 95, 499), _w("Kimi", 111, 468),
             _w("72.2%", 398, 562), _w("62.0%", 361, 530), _w("16.5%", 199, 499), _w("16.4%", 199, 467),
             _w("0%", 129, 588), _w("20%", 160, 588), _w("40%", 190, 588), *extra]
    monkeypatch.setattr(ft, "words", lambda page: words)


def test_bar_labels_pair_with_the_value_on_their_row(monkeypatch):
    _bars(monkeypatch)
    assert ft.pairs(None) == [("豆包", "72.2%"), ("DeepSeek", "62.0%"), ("腾讯元宝", "16.5%"), ("Kimi", "16.4%")]


def test_a_value_no_label_claims_leaves_the_figure_unread(monkeypatch):
    _bars(monkeypatch, [_w("12.2%", 184, 436)])  # its label was not read: the series would be partial
    assert ft.pairs(None) == []


def test_two_values_on_one_row_leave_the_figure_unread(monkeypatch):
    _bars(monkeypatch, [_w("70.1%", 300, 562)])
    assert ft.pairs(None) == []


def test_paired_lines_pass_the_series_check_as_quotes():
    evidence = "[r.pdf | pdf p16]\n72.2%62.0%16.5%豆包DeepSeek腾讯元宝\n[r.pdf | 图中坐标配对 p16]\n豆包 72.2%\nDeepSeek 62.0%\n腾讯元宝 16.5%"
    raw = [{"metric": "各产品使用率", "unit": "%", "kind": "category", "points": [
        {"label": label, "value": value, "display": f"{value}%", "quote": f"{label} {value}%"}
        for label, value in (("豆包", 72.2), ("DeepSeek", 62.0), ("腾讯元宝", 16.5))]}]
    kept, rejected = data_series.verify(raw, evidence)
    assert len(kept) == 1 and not rejected
    assert kept[0].points[0].ref == "r.pdf 第16页"


SOURCE = Path("eval/sources/cnnic_genai_2025.pdf")


@pytest.mark.skipif(not SOURCE.is_file(), reason="source PDF not present")
def test_cnnic_usage_purposes_read_whole():
    from pypdf import PdfReader

    found = ft.pairs(PdfReader(str(SOURCE)).pages[16])
    assert found[0] == ("回答问题", "80.9%") and found[-1] == ("帮助写代码", "10.3%") and len(found) == 7


def test_a_series_missing_values_of_a_fuller_one_is_dropped():
    from ppt_agent.v2.visual.content import DataSeries

    def series(pairs):
        return DataSeries(metric="目的", unit="%", kind="category", points=[
            {"label": label, "value": value, "display": f"{value}%", "quote": f"{label} {value}%", "ref": "r.pdf 第20页"}
            for label, value in pairs])

    full = series([("学习", 15.2), ("写作业", 10.8), ("好奇", 10.4), ("写故事", 7.2)])
    part = series([("学习", 15.2), ("写故事", 7.2), ("制作视频", 6.8)][:2])
    assert data_series.prune([part, full]) == [full]
