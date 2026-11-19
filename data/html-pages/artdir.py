"""Stage C: composition candidates for fixed, verified pages, compared blind on PowerPoint renderings.

Usage: uv run python data/html-pages/artdir.py RUN_DIR PAGES OUT_DIR [--looks N] [--reuse]

For each page (content verified and frozen in RUN_DIR):
- current: the page as the run designed it (its saved HTML, no model call);
- form:    another form the content can honestly take, not used by the pages around it;
- art:     an art-direction brief written for this page alone (nature of the page, one focus and its scale,
           grid division, what stays quiet), then designed to that brief in the same theme vocabulary.
Every candidate is rendered into one PPTX and exported by PowerPoint; the independent reviewer (another
model) compares the PowerPoint images blind. The browser preview draws native charts as rough sketches,
so comparing on it is biased against chart pages (found on the first 12-page set).
--reuse skips generation and re-reviews the candidates saved in OUT_DIR.
"""
import asyncio, dataclasses, io, json, subprocess, sys
from pathlib import Path

from PIL import Image

from ppt_agent import deck_jobs
from ppt_agent.runtime import load_dotenv_file
from ppt_agent.v2 import visual_review
from ppt_agent.v2.evidence_check import _ADAPTER
from ppt_agent.v2.htmldeck import browser, layout_plan, themes
from ppt_agent.v2.htmldeck.deck import _apply, _replay, job_for, renumber
from ppt_agent.v2.htmldeck.fonts import office_font_css
from ppt_agent.v2.htmldeck.pipeline import design_page
from ppt_agent.v2.ir import DeckDesign
from ppt_agent.v2.render import render_deck
from ppt_agent.v2.visual.content import RichContent
from ppt_agent.v2.visual_review import Version

load_dotenv_file()
run, pages, out = Path(sys.argv[1]), [int(n) for n in sys.argv[2].split(",")], Path(sys.argv[3])
looks = int(sys.argv[sys.argv.index("--looks") + 1]) if "--looks" in sys.argv else 3
reuse = "--reuse" in sys.argv
out.mkdir(parents=True, exist_ok=True)
C = run / "checkpoints"
ROOT = Path(__file__).resolve().parents[2]
NAMES = ("current", "form", "art")

ART_SYSTEM = (
    "你是资深的演示文稿艺术指导。下面是一页已经核对过的内容，以及这套演示文稿的视觉语言（编辑式：纸感底色、墨色、一种朱红；"
    "靠字号反差、细线、留白、图解，不靠卡片）。请只为这一页写一段构图指示，交给版面设计师执行。\n"
    "要求：\n"
    "1. 先判断这一页的性质：数据为主 / 结构或流程 / 一个关键观点 / 高密度对照，并据此决定信息密度。\n"
    "2. 指定唯一的视觉焦点，以及它和其余信息的尺度反差（焦点要大到什么程度，其余要安静到什么程度）。\n"
    "3. 用 12 列 × 8 行网格说明空间怎么分：优先考虑非对称分割、大面积留白、通栏或出血的主视觉、上下分层，"
    "而不是左右各一半；说明视线从哪里开始、到哪里结束。\n"
    "4. 只能用这套主题已有的手段（大数字 d1–d4、标题字号阶梯、细线、一个色域、比例条、方块阵、柱子、阶梯、时间轴、因果链、"
    "层级图、四象限、账本、原生图表、表格）。不要加没有含义的装饰；同一个数字在页面上只出现一次。\n"
    "5. 这一页不应该是：{avoid}。\n"
    '只输出 JSON：{{"base_form":"从 allowed 里选一个最接近的形式","brief":"不超过 160 字的构图指示","why":"不超过 40 字"}}'
)


def content_of(number):
    final = C / f"typeset/final/content_{number:03d}.json"
    data = json.loads(final.read_text())["content"] if final.is_file() else \
        json.loads((C / f"typeset/content_{number:03d}.json").read_text())["content"]
    return RichContent.model_validate(data) if data.get("archetype") == "rich" else _ADAPTER.validate_python(data)


async def main():
    writer, reviewer = deck_jobs.create_v2_model_client(), deck_jobs.create_v2_review_client()
    theme = themes.get("studio")
    theme = dataclasses.replace(theme, css=theme.css + themes.studio_roles_css())
    font_css, _ = office_font_css()
    skeleton = {slot["page_number"]: slot for slot in json.loads((C / "skeleton.json").read_text())["slots"]}
    plan = {item["page"]: layout_plan.Fingerprint(**item) for item in json.loads((C / "html/layout_plan.json").read_text())}
    notes_path = out / "notes.json"
    notes = json.loads(notes_path.read_text()) if notes_path.is_file() else {}

    def fresh(number, fingerprint):
        slot = skeleton[number]
        job = job_for(content_of(number), page_number=number, section_index=slot.get("section_index"),
                      section_title=slot.get("section_title"), assets_dir=run / "assets")
        job.type_roles = True
        _apply(job, fingerprint)
        return job

    async with browser.Session(concurrency=4) as session:
        gate = asyncio.Semaphore(4)

        async def generate(number):
            current = plan[number]
            saved = json.loads((C / f"html/page_{number:03d}.json").read_text())
            (out / f"p{number:03d}-current.html").write_text(saved["html"])
            note = {"current": {"form": current.form, "arrangement": current.arrangement}}
            job = fresh(number, current)
            near = {plan[p].form for p in plan if 0 < abs(p - number) <= 2}
            allowed = layout_plan.candidates({**job.content, "archetype": job.archetype})
            others = [f for f in allowed if f != current.form and f not in near] or [f for f in allowed if f != current.form]
            if others:
                fp = layout_plan.Fingerprint(page=number, form=others[0],
                                             focus="right" if current.focus == "left" else "left", ratio="wide")
                async with gate:
                    b = await design_page(writer, session, theme, font_css, fresh(number, fp), look=False)
                note["form"] = {"form": others[0], "ok": b.ok, "defects": b.defects[:3]}
                if b.ok:
                    (out / f"p{number:03d}-form.html").write_text(b.html)
            avoid = f"「{themes.STUDIO_FORMS.get(current.form, current.form)}」那样的排法"
            try:
                brief = await writer.complete_json(task="art_direction", system=ART_SYSTEM.format(avoid=avoid),
                                                   user=json.dumps({"content": job.content, "allowed": allowed}, ensure_ascii=False),
                                                   max_output_tokens=800)
            except Exception as error:  # noqa: BLE001
                brief = {"error": str(error)[:100]}
            base = brief.get("base_form") if brief.get("base_form") in allowed else (others or allowed)[0]
            job_c = fresh(number, layout_plan.Fingerprint(page=number, form=base))
            job_c.idea = f"{base}——{themes.STUDIO_FORMS.get(base, '')}。艺术指导的构图要求（优先于示范页的网格）：{brief.get('brief', '')}"
            async with gate:
                c = await design_page(writer, session, theme, font_css, job_c, look=False)
            note["art"] = {"brief": brief, "base_form": base, "ok": c.ok, "defects": c.defects[:3]}
            if c.ok:
                (out / f"p{number:03d}-art.html").write_text(c.html)
            notes[str(number)] = note

        if not reuse:
            await asyncio.gather(*(generate(number) for number in pages))
            notes_path.write_text(json.dumps(notes, ensure_ascii=False, indent=1))

        # Replay every saved candidate (no model) into one deck, in a fixed order.
        order, designed = [], []
        for number in pages:
            for name in NAMES:
                path = out / f"p{number:03d}-{name}.html"
                if not path.is_file():
                    continue
                note = notes.get(str(number), {}).get(name) or {}
                form = note.get("form") or note.get("base_form") or plan[number].form
                job = fresh(number, plan[number] if name == "current" else layout_plan.Fingerprint(page=number, form=form))
                result = await _replay(session, theme, font_css, job, path.read_text(), trusted=True)
                if result.ok:
                    order.append((number, name))
                    designed.append(renumber(result.page, len(order)))
    base = json.loads(next(run.glob("*_design.json")).read_text())
    deck = DeckDesign.model_validate({**base, "pages": [page.model_dump(mode="json") for page in designed]})
    pptx = render_deck(deck, out / "candidates.pptx", assets_dir=run / "assets")
    shots = out / "ppt"
    shots.mkdir(exist_ok=True)
    subprocess.run(["zsh", str(ROOT / "scripts/pptx_snapshot.sh"), str(shots), str(pptx)], check=True, stdout=subprocess.DEVNULL)
    images = {}
    for index, key in enumerate(order, start=1):
        image = Image.open(shots / f"candidates-{index:02d}.png").convert("RGB")
        image.thumbnail((1280, 1280))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        images[key] = buffer.getvalue()
        image.save(out / f"p{key[0]:03d}-{key[1]}.png")

    report = {}

    async def review(number):
        versions = [Version(name, images[(number, name)]) for name in NAMES if (number, name) in images]
        if len(versions) < 2:
            return
        job = fresh(number, plan[number])
        facts = {k: job.content.get(k) for k in ("title", "highlight", "takeaway", "items", "lead") if job.content.get(k)}
        verdict = await visual_review.review_page(reviewer, facts=facts, versions=versions, reference_png=None,
                                                  seed=number, looks=looks)
        report[number] = {name: {k: v.get(k) for k in ("overall", "wins", "vetoes", "scores", "issues")}
                          for name, v in (verdict.get("versions") or {}).items()}
        print(number, {name: v["overall"] for name, v in report[number].items()}, flush=True)

    await asyncio.gather(*(review(number) for number in pages))
    (out / "report.json").write_text(json.dumps({"notes": notes, "review": report}, ensure_ascii=False, indent=1, default=str))
    print(writer.usage.snapshot(), reviewer.usage.snapshot())


asyncio.run(main())
