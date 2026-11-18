from ppt_agent.v2.htmldeck import autofix

PAGE = ('<div class="page"><div class="r" style="grid-column:1/13;grid-row:1/2">t</div>'
        '<div class="r" style="grid-column:1/8;grid-row:2/5"><div class="d1">466次</div></div>'
        '<div class="r bot" style="grid-column:8/13;grid-row:4/6">x</div>'
        '<div class="r" style="grid-column:1/8;grid-row:6/8">y</div></div>')


def test_regions_grow_only_into_rows_left_empty():
    grown = autofix.regions(autofix.grow(PAGE))
    assert grown == [(1, 13, 1, 2), (1, 8, 2, 6), (8, 13, 4, 6), (1, 8, 6, 9)]


def test_display_type_steps_down_body_type_never():
    page = PAGE.replace('<div class="r" style="grid-column:1/8;grid-row:6/8">y', '<div class="r" style="grid-column:1/8;grid-row:6/8"><p class="b">y</p>')
    smaller = autofix.step_down(page)
    assert 'class="d2"' in smaller and 'class="b"' in smaller


def test_contrast_ratio():
    from ppt_agent.v2.htmldeck.checks import contrast
    assert round(contrast("rgb(26, 25, 22)", "rgb(26, 25, 22)"), 1) == 1.0
    assert contrast("rgb(26, 25, 22)", "rgb(243, 238, 228)") > 14
    assert contrast("rgb(111, 106, 94)", "rgb(243, 238, 228)") > 4.5   # the theme's caption grey reads
