"""ppt-agent v2: hybrid free-layout pipeline for long, design-rich decks.

v2 replaces the fixed-template visual layer with a constrained free-layout
PageDesign IR generated one page per model call, rendered deterministically to
native editable PPTX. It is provider-agnostic (BYOK: any OpenAI-compatible or
Anthropic endpoint) and scales to 100+ slides through concurrent per-page
generation with checkpoint resume.
"""
