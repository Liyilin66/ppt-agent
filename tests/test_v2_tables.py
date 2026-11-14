from ppt_agent.v2 import data_series, tables

PAGE = """                表1语言大模型演进迭代情况

       公司             模型            上下文长度
      MetaAI   Llama2            8k

               Llama3.1          128k
               GPT-4             32k
      OpenAI   GPT-4Turbo        128k

               o1/o1mini         128k
     Anthropic Claude3.5         200k
       谷歌      Gemini-1.0        32k
               Gemini-1.5        1000k（100万）
   语言大模型虽然在文本理解与生成、复杂逻辑推理任务上取得
"""
HEADERS = ["公司", "模型", "上下文长度"]
ROWS = [["MetaAI", "Llama 2", "8k"], ["MetaAI", "Llama 3.1", "128k"], ["OpenAI", "GPT-4", "32k"],
        ["OpenAI", "GPT-4 Turbo", "128k"], ["OpenAI", "o1/o1 mini", "128k"], ["Anthropic", "Claude 3.5", "200k"],
        ["谷歌", "Gemini-1.0", "32k"], ["谷歌", "Gemini-1.5", "1000k（100万）"]]


def test_a_table_is_read_row_by_row_with_merged_cells():
    rows, problems = tables.verify(HEADERS, ROWS, PAGE)
    assert problems == [] and rows == ROWS


def test_a_row_that_is_not_on_the_page_sinks_the_table():
    changed = [list(row) for row in ROWS]
    changed[2][2] = "64k"                      # GPT-4 with another model's kind of value
    assert tables.verify(HEADERS, changed, PAGE)[0] == []
    swapped = [ROWS[1], ROWS[0], *ROWS[2:]]    # rows out of the page's order
    assert tables.verify(HEADERS, swapped, PAGE)[0] == []
    stolen = [list(row) for row in ROWS]
    stolen[5][0] = "OpenAI"                    # Claude given to another company
    rows, problems = tables.verify(HEADERS, stolen, PAGE)
    assert rows == [] and problems
    assert tables.verify(["公司", "模型", "参数量"], ROWS, PAGE)[0] == []


def test_a_numeric_column_becomes_a_series_over_every_row():
    table = {"label": "表 1", "title": "语言大模型演进迭代情况", "ref": "r.pdf 第16页", "headers": HEADERS, "rows": ROWS}
    (series,) = tables.series_of(table)
    assert series["unit"] == "k" and series["kind"] == "category" and len(series["points"]) == 8
    assert series["points"][-1]["value"] == 1000 and series["points"][-1]["label"] == "Gemini-1.5"
    assert series["points"][0]["quote"] == "MetaAI Llama 2 8k"


TEXT = "[a.pdf | PDF p3]\n各地区渗透率：北美 41%，欧洲 28%，亚太 19%，拉美 12%，中东约为一成。\n"
QUOTE = "北美 41%，欧洲 28%，亚太 19%，拉美 12%"


def region(extra):
    points = [{"label": name, "value": value, "display": f"{value}%", "quote": QUOTE}
              for name, value in (("北美", 41), ("欧洲", 28), ("亚太", 19), ("拉美", 12))]
    return {"metric": "渗透率", "unit": "%", "kind": "category", "points": points + [extra]}


def test_what_is_left_of_a_series_is_drawn_only_when_it_is_still_a_fair_comparison():
    inside = {"label": "中东", "value": 15, "display": "15%", "quote": "中东约为一成"}
    kept, rejected = data_series.verify([region(inside)], TEXT)
    assert len(kept[0].points) == 4 and kept[0].omitted == ["中东"]
    assert any("已从图中去掉" in entry["why"] for entry in rejected)
    # The value that failed would have been the largest: what is left would mislead.
    top = {"label": "中东", "value": 55, "display": "55%", "quote": "中东约为一成"}
    assert data_series.verify([region(top)], TEXT)[0] == []
    # Parts of a whole cannot lose a part.
    assert data_series.verify([{**region(inside), "kind": "share"}], TEXT)[0] == []


def test_a_table_with_an_unread_row_or_running_off_the_page_is_not_used():
    rows, problems = tables.verify(HEADERS, ROWS[:2] + ROWS[3:], PAGE)   # GPT-4 left out
    assert rows == [] and "没有读进来" in problems[0]["why"]
    foot = PAGE.split("   语言大模型虽然")[0] + "\n                    15\n"
    rows, problems = tables.verify(HEADERS, ROWS, foot)
    assert rows == [] and "页末" in problems[0]["why"]
