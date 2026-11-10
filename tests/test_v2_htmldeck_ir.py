"""The two literal-styled element types that browser-measured pages use."""

from pptx import Presentation
from pptx.oxml.ns import qn

from ppt_agent.v2.design import get_builtin_theme
from ppt_agent.v2.ir import (
    BleedFrame,
    BoxItem,
    ColorStop,
    DeckDesign,
    Frame,
    PageDesign,
    Paint,
    RichTextItem,
    TextItem,
    TextRun,
)
from ppt_agent.v2.preview import page_to_html
from ppt_agent.v2.render import render_deck


def _deck(tmp_path):
    glow = BoxItem(
        id="glow",
        frame=BleedFrame(x=-300, y=40, w=700, h=700),
        shape="ellipse",
        fill=Paint(
            kind="radial",
            stops=[ColorStop(hex="2B5CFF", alpha=0.4, pos=0), ColorStop(hex="2B5CFF", alpha=0, pos=65)],
        ),
    )
    card = BoxItem(
        id="card",
        frame=BleedFrame(x=60, y=200, w=400, h=200),
        shape="round",
        radius=20,
        fill=Paint(kind="linear", angle_deg=90, stops=[
            ColorStop(hex="3FF0D0", pos=0), ColorStop(hex="2B5CFF", pos=100)]),
        stroke_hex="FFFFFF",
        stroke_alpha=0.12,
        stroke_width=1,
        glow_hex="3FF0D0",
        glow_alpha=0.35,
        glow_radius=25,
    )
    text = RichTextItem(
        id="t",
        frame=BleedFrame(x=60, y=80, w=600, h=60),
        runs=[
            TextRun(text="全球 AI ", size_pt=22, hex="EEF1F8", bold=True),
            TextRun(text="6233", size_pt=22, hex="EEF1F8", bold=True, letter_spacing_pt=1.5,
                    paint=Paint(kind="linear", stops=[
                        ColorStop(hex="6EA8FF", pos=0), ColorStop(hex="3FF0D0", pos=100)])),
            TextRun(line_break=True),
            TextRun(text="亿美元", size_pt=14, hex="8D97AD", alpha=0.55, font="SimSun"),
        ],
        wrap=False,
        line_pt=30,
    )
    page = PageDesign(page_number=1, background_hex="07090F", show_chrome=False,
                      elements=[glow, card, text])
    deck = DeckDesign(deck_title="t", theme=get_builtin_theme("aurora"), pages=[page])
    return deck, render_deck(deck, tmp_path / "d.pptx")


def test_literal_elements_render_as_native_text_and_shapes(tmp_path):
    deck, path = _deck(tmp_path)
    slide = Presentation(str(path)).slides[0]
    shapes = list(slide.shapes)
    assert [shape.has_text_frame and bool(shape.text_frame.text) for shape in shapes] == [
        False, False, True]
    glow, card, text = shapes
    for shape in (glow, card):
        order = [child.tag.split("}")[1] for child in shape._element.spPr]
        assert order == ["xfrm", "prstGeom", "gradFill", "ln", "effectLst"]
    xml = glow._element.xml
    assert 'path="circle"' in xml and '<a:alpha val="40000"/>' in xml
    assert 'ang="5400000"' in card._element.xml and "<a:glow" in card._element.xml
    runs = text.text_frame.paragraphs[0].runs
    assert [run.text for run in runs] == ["全球 AI ", "6233", "亿美元"]
    assert runs[0].font.size.pt == 22 and runs[0].font.bold
    assert runs[1]._r.rPr.find(qn("a:gradFill")) is not None
    assert runs[1]._r.rPr.get("spc") == "150"
    assert runs[2].font.name == "SimSun"
    assert runs[2]._r.rPr.find(qn("a:ea")).get("typeface") == "SimSun"
    assert text.text_frame.word_wrap is False
    assert text.text_frame.paragraphs[0].line_spacing.pt == 30


def test_pages_round_trip_and_old_pages_still_load(tmp_path):
    deck, _ = _deck(tmp_path)
    again = DeckDesign.model_validate_json(deck.model_dump_json())
    assert again == deck
    old = PageDesign.model_validate({
        "page_number": 1,
        "elements": [{"type": "text", "id": "a", "frame": {"x": 0, "y": 0, "w": 10, "h": 10},
                      "text": "x"}],
    })
    assert isinstance(old.elements[0], TextItem) and old.background_hex is None
    assert isinstance(old.elements[0].frame, Frame)


def test_preview_draws_literal_elements(tmp_path):
    deck, _ = _deck(tmp_path)
    html = page_to_html(deck.pages[0], deck)
    assert "background:#07090F" in html and "radial-gradient" in html and "6233" in html
