from pathlib import Path

import pytest

from ppt_agent.v2.figure_discovery import CAPTION, CREDIT, _prose

IEA = Path("data/p4b-source/iea_gevo2025.pdf")


def test_credit_and_caption_lines():
    assert CREDIT.match("IEA. CC BY 4.0.") and CREDIT.match("Sources: IEA analysis") and CREDIT.match("资料来源：CNNIC")
    assert CAPTION.match("Figure 1.2 Global sales").group(1) == "Figure 1.2"
    assert not _prose("Global electric car sales, 2014-2024")


@pytest.mark.skipif(not IEA.is_file(), reason="IEA source not downloaded")
def test_iea_vector_charts_are_found_with_their_titles(tmp_path):
    from ppt_agent.v2.figure_discovery import discover

    found = discover(IEA, 1, tmp_path)
    titles = {item["page"]: item["title"] for item in found}
    assert titles[15] == "Global electric car sales, 2014-2024"
    assert titles[25].startswith("Quarterly electric car sales")
    assert len(found) >= 60 and all((tmp_path / item["id"]).is_file() for item in found)
