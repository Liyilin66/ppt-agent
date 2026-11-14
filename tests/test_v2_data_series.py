import asyncio
import json

import pytest
from pydantic import ValidationError

from ppt_agent.v2 import data_series
from ppt_agent.v2.visual.content import DataSeries, RichContent

# caict_ai_2024.pdf, as the evidence step delivers it.
EVIDENCE = """[caict_ai_2024.pdf | PDF p11]
全球人工智能产业保持高速增长。据IDC 预测，2024年全球人
工智能产业规模将达到 6233亿美元，同比增长 21.5%1。具体来看，

[caict_ai_2024.pdf | PDF p12]
大模型领域拉动全球人工智能投融资金额上扬。2024年上半年，
全球人工智能投融资金额达 316 亿美元，同比上升 84%。在全球融
资紧缩的背景下，受益于大模型发展和企业融资带动，人工智能领
域融资占全行业融资比例持续上升，从 2022年的 4.5%上升至 2024
年上半年的 12.1%。2023年，生成式人工智能投融资规模达 252 亿
美元，约为 2022 年的 9 倍，占 2023年所有人工智能相关投资的约

[caict_ai_2024.pdf | PDF p13]
四分之一3。2024 年上半年，全球金额最大的 10 笔融资事件中有 6
笔为大模型企业融资，金额总计达 135亿美元。
"""
QUOTE = "从 2022年的 4.5%上升至 2024年上半年的 12.1%"


def share(points, **extra):
    return {"metric": "人工智能领域融资占全行业融资比例", "unit": "%", "kind": "time", "scope": "全球",
            "period": "2022年—2024年上半年", "points": points, **extra}


def point(label, value, display, quote=QUOTE, **extra):
    return {"label": label, "value": value, "display": display, "quote": quote, **extra}


GOOD = share([point("2022年", 4.5, "4.5%"), point("2024年上半年", 12.1, "12.1%")])


def test_a_series_quoted_word_for_word_is_kept_with_its_page():
    kept, rejected = data_series.verify([GOOD], EVIDENCE)
    assert rejected == [] and len(kept) == 1
    assert [(p.label, p.value, p.ref) for p in kept[0].points] == [
        ("2022年", 4.5, "caict_ai_2024.pdf 第12页"), ("2024年上半年", 12.1, "caict_ai_2024.pdf 第12页")]
    assert kept[0].kind == "time" and not any(p.projected for p in kept[0].points)


@pytest.mark.parametrize("points, why", [
    ([point("2022年", 4.5, "4.5%"), point("2024年上半年", 12.7, "12.7%")], "引文里没有这个数字"),
    ([point("2022年", 4.5, "4.5%"), point("2024年上半年", 12.1, "12.1%", quote="占比在2024年上半年升到了12.1%")], "不是原文里的原话"),
    ([point("2022年", 12.1, "12.1%"), point("2024年上半年", 4.5, "4.5%")], "最近的年份"),
    ([point("2022年", 4.5, "4.5%"), point("2024年上半年", 12.1, "12.1个百分点")], "单位"),
    ([point("2022年", 4.5, "4.5%"), point("2023年", 12.1, "12.1%")], "没有提到这个标签"),
    ([point("2022年", 4.5, "4.5%"), point("2024年上半年", 12.0, "12.1%")], "不一致"),
])
def test_a_value_that_does_not_stand_in_the_source_sinks_the_series(points, why):
    kept, rejected = data_series.verify([share(points)], EVIDENCE)
    assert kept == [] and any(why in entry["why"] for entry in rejected), rejected


def test_a_year_binds_the_value_that_follows_it():
    lone = "2023年，生成式人工智能投融资规模达 252 亿美元，约为 2022 年的 9 倍"
    wrong = {"metric": "生成式人工智能投融资规模", "unit": "亿美元", "kind": "time", "points": [
        point("2022年", 252, "252亿美元", quote=lone),
        point("2024年上半年", 316, "316亿美元", quote="2024年上半年，全球人工智能投融资金额达 316 亿美元")]}
    kept, rejected = data_series.verify([wrong], EVIDENCE)
    assert kept == [] and any("最近的年份" in entry["why"] for entry in rejected)


def test_labels_and_values_in_one_sentence_pair_in_reading_order():
    text = "[a.pdf | PDF p3]\n其中北美、欧洲的渗透率分别为 41%、28%，亚太地区仍在起步。\n"
    quote = "其中北美、欧洲的渗透率分别为 41%、28%"
    def series(north, europe):
        return {"metric": "渗透率", "unit": "%", "kind": "category", "points": [
            point("北美", north, f"{north}%", quote=quote), point("欧洲", europe, f"{europe}%", quote=quote)]}
    assert data_series.verify([series(41, 28)], text)[0]
    kept, rejected = data_series.verify([series(28, 41)], text)
    assert kept == [] and "顺序对不上" in rejected[0]["why"]


def test_a_forecast_is_marked_even_when_the_model_forgets():
    text = "[a.pdf | PDF p1]\n2023年全球产业规模为 5130 亿美元，据IDC预测，2024年将达到 6233亿美元。\n"
    series = {"metric": "全球人工智能产业规模", "unit": "亿美元", "kind": "time", "points": [
        point("2023年", 5130, "5130亿美元", quote="2023年全球产业规模为 5130 亿美元"),
        point("2024年", 6233, "6233亿美元", quote="据IDC预测，2024年将达到 6233亿美元")]}
    kept, rejected = data_series.verify([series], text)
    assert [p.projected for p in kept[0].points] == [False, True], rejected


def test_a_quote_may_run_over_a_page_break():
    series = {"metric": "x", "unit": "笔", "kind": "category", "points": [
        point("最大融资事件", 10, "10笔", quote="2024 年上半年，全球金额最大的 10 笔融资事件中有 6笔为大模型企业融资"),
        point("大模型企业融资", 6, "6笔", quote="2024 年上半年，全球金额最大的 10 笔融资事件中有 6笔为大模型企业融资")]}
    kept, rejected = data_series.verify([series], EVIDENCE)
    assert kept and kept[0].points[0].ref == "caict_ai_2024.pdf 第13页", rejected
    across = point("生成式AI占比", 2023, "2023年", quote="占 2023年所有人工智能相关投资的约四分之一")
    assert data_series._locate(across["quote"], data_series.pages_of(EVIDENCE)) == "caict_ai_2024.pdf 第12-13页"


def test_the_model_rules_what_a_series_is():
    with pytest.raises(ValidationError):
        DataSeries.model_validate({**GOOD, "kind": "share", "points": [
            {**point("A", 70, "70%"), "ref": "a 第1页"}, {**point("B", 60, "60%"), "ref": "a 第1页"}]})
    with pytest.raises(ValidationError):
        DataSeries.model_validate({**GOOD, "points": [{**point("A", 1, "1%"), "ref": "a 第1页"}] * 2})
    old = {"title": "旧内容", "items": [{"heading": "一条", "body": "没有序列的旧页面"}]}
    assert RichContent.model_validate(old).series == []


class Client:
    def __init__(self, reply, ok=True):
        self.reply, self.calls, self.ok = reply, 0, ok

    async def complete_json(self, *, task, **kwargs):
        if task == "series_audit":
            return {"verdicts": [{"index": 0, "ok": self.ok, "reason": "两个不同的指标"}]}
        self.calls += 1
        return self.reply


class Store:
    def __init__(self):
        self.saved = {}

    def load(self, name):
        return self.saved.get(name)

    def save(self, name, value):
        self.saved[name] = json.loads(json.dumps(value))


def test_extract_reads_each_packet_once_and_skips_prose():
    client, store = Client({"series": [GOOD]}), Store()
    first = asyncio.run(data_series.extract(client, store, EVIDENCE))
    again = asyncio.run(data_series.extract(client, store, EVIDENCE))
    assert client.calls == 1 and first == again and len(first["series"]) == 1
    prose = asyncio.run(data_series.extract(client, store, "[a.pdf | PDF p1]\n治理体系渐趋明晰，2024年合作更加频繁。"))
    assert client.calls == 1 and prose["series"] == []


def test_a_second_reader_can_veto_a_series_and_silence_is_a_veto():
    doubted = asyncio.run(data_series.extract(Client({"series": [GOOD]}, ok=False), Store(), EVIDENCE))
    assert doubted["series"] == [] and "复核未通过" in doubted["rejected"][0]["why"]

    class Silent(Client):
        async def complete_json(self, *, task, **kwargs):
            return {} if task == "series_audit" else self.reply

    assert asyncio.run(data_series.extract(Silent({"series": [GOOD]}), Store(), EVIDENCE))["series"] == []


def test_two_measures_in_one_unit_are_not_a_series_and_a_part_is_not_the_whole():
    text = ("[a.pdf | PDF p40]\nTCL 通过视觉技术实现液晶面板缺陷检测，准确率超 90%，生产周期缩短 60%。\n"
            "[a.pdf | PDF p16]\nGPT-4 Turbo 128k 上下文；Qwen 8k 上下文；Gemini-1.5 1000k（100 万）上下文。\n")
    q = "准确率超 90%，生产周期缩短 60%"
    mixed = {"metric": "准确率和生产周期缩短比例", "unit": "%", "kind": "category", "points": [
        point("准确率", 90, "超90%", quote=q), point("生产周期缩短", 60, "60%", quote=q)]}
    kept, rejected = data_series.verify([mixed], text)
    assert kept == [] and any("不是被比较的对象" in entry["why"] for entry in rejected)
    table = {"metric": "上下文长度", "unit": "k", "kind": "category", "points": [
        point("GPT-4 Turbo", 128, "128k", quote="GPT-4 Turbo 128k 上下文"), point("Qwen", 8, "8k", quote="Qwen 8k 上下文"),
        point("Claude 3.5", 200, "200k", quote="Claude 3.5 200k 上下文")]}
    kept, rejected = data_series.verify([table], text)
    assert kept == [] and any("整条不画" in entry["why"] for entry in rejected)
    assert data_series.verify([{**table, "kind": "time", "points": table["points"][:2]}], text)[0] == []


def test_an_approximate_value_keeps_its_qualifier():
    text = "[a.pdf | PDF p57]\n截至8月，国内已有近 1919 个深度合成算法、190 个生成式人工智能服务完成备案，攻击模板 80 余种。\n"
    q = "国内已有近 1919 个深度合成算法、190 个生成式人工智能服务完成备案"
    kept, rejected = data_series.verify([{"metric": "完成备案的数量", "unit": "个", "kind": "category", "points": [
        point("深度合成算法", 1919, "近1919个", quote=q), point("生成式人工智能服务", 190, "190个", quote=q)]}], text)
    assert kept and kept[0].points[0].display == "近1919个" and kept[0].points[0].value == 1919, rejected
    assert data_series.approximate("近1919个") and data_series.approximate("80余种") and data_series.approximate("90%以上")
    assert not data_series.approximate("12.1%")


def test_a_series_goes_to_the_one_page_that_talks_about_it():
    series, _ = data_series.verify([GOOD], EVIDENCE)
    pages = {
        4: {"title": "2024年全球AI产业将达6233亿美元", "items": [{"heading": "增速", "value": "21.5%"}]},
        5: {"title": "2024年上半年AI融资达316亿美元",
            "items": [{"heading": "占比提高", "body": "由2022年的4.5%升至2024年上半年12.1%。", "value": "12.1%"}]},
        6: {"title": "融资占比升至12.1%", "items": [{"heading": "一条", "body": "别的"}]},
    }
    placed = data_series.assign({4: series, 5: series, 6: series}, pages)
    assert list(placed) == [6]  # the page that leads with the figure
    assert list(data_series.assign({4: series, 5: series}, pages)) == [5]
    assert data_series.assign({4: series}, pages) == {}
