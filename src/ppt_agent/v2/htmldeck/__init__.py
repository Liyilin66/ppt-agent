"""Pages designed in constrained HTML, measured in a browser, emitted as native PPTX.

The model writes one slide of HTML against a theme's stylesheet; Chromium lays
it out; ``convert`` turns every measured box and text block into literal
``PageDesign`` elements that the ordinary renderer writes as editable shapes.
"""
