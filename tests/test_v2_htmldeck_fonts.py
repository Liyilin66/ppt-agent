from ppt_agent.v2.htmldeck.fonts import FAMILIES, OFFICE_FACES, office_font_css


def test_office_fonts_are_declared_from_the_app_bundle(tmp_path):
    for _, _, file_name in OFFICE_FACES:
        (tmp_path / file_name).write_bytes(b"")
    css, real = office_font_css(tmp_path, platform="darwin")
    assert real
    for family in FAMILIES:
        assert f"font-family:'{family}'" in css
    assert "msyhbd.ttc" in css and "font-weight:700" in css


def test_without_office_the_names_fall_back_to_system_fonts(tmp_path):
    css, real = office_font_css(tmp_path / "missing", platform="darwin")
    assert not real
    assert "local('PingFang SC')" in css and "font-family:'SimSun'" in css


def test_windows_needs_no_rules():
    assert office_font_css(platform="win32") == ("", True)
