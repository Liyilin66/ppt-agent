from types import SimpleNamespace

from ppt_agent.v2.htmldeck.selection import key_pages


def job(number, section, highlight=None, role="content"):
    return SimpleNamespace(page_number=number, section=section, role=role,
                           content={"highlight": {"value": highlight} if highlight else None})


def test_key_pages_are_section_openers_and_visual_pages_about_a_fifth_of_the_deck():
    jobs = [job(n, "A" if n < 6 else "B", highlight="36.5%" if n in (4, 8) else None) for n in range(2, 12)]
    plan = {n: SimpleNamespace(form="chart" if n == 8 else "ledger") for n in range(2, 12)}
    assert key_pages(jobs, plan, held=set()) == [2, 8]
    assert key_pages(jobs, plan, held={8}) == [2, 6]
