import pytest

from ppt_agent.design import (
    DesignSpec,
    get_layout_contract,
    list_layout_contracts,
)
from ppt_agent.layouts import TEMPLATE_LAYOUTS


def test_design_spec_defaults() -> None:
    spec = DesignSpec()

    assert spec.theme_name == "clean_business"
    assert spec.visual_tone == "clean, professional, presentation-ready"
    assert spec.density_level == "medium"
    assert spec.font_scale == "standard"
    assert spec.accent_color is None
    assert spec.background_style == "light"


def test_layout_contract_registry_lists_current_template_layouts() -> None:
    contracts = list_layout_contracts()
    names = {contract.layout_name for contract in contracts}

    assert names == set(TEMPLATE_LAYOUTS)
    for contract in contracts:
        assert contract.min_items <= contract.max_items
        assert contract.required_slots


def test_layout_contract_registry_includes_professional_layouts() -> None:
    contracts = {contract.layout_name: contract for contract in list_layout_contracts()}

    assert contracts["comparison_matrix"].max_items == 2
    assert "decision_rule" in contracts["comparison_matrix"].optional_slots
    assert contracts["process_flow"].min_items == 3
    assert contracts["process_flow"].max_items == 5
    assert contracts["risk_matrix"].min_items == 3
    assert contracts["risk_matrix"].max_items == 4
    assert contracts["key_takeaway"].min_items == 2
    assert contracts["key_takeaway"].max_items == 4


def test_section_divider_contract_is_transition_only() -> None:
    contract = get_layout_contract("section_divider")

    assert "chapter transition" in contract.best_for
    assert "section break" in contract.best_for
    assert "ordinary content explanation" in contract.avoid_when
    assert "single key_message plus one explanation" in contract.avoid_when


def test_get_layout_contract_rejects_unsupported_layout_with_clear_error() -> None:
    with pytest.raises(ValueError) as exc_info:
        get_layout_contract("timeline")

    message = str(exc_info.value)
    assert "Unsupported layout 'timeline'" in message
    assert "Supported LayoutContract layout_name values" in message
    assert "title_slide" in message
    assert "closing_slide" in message
