"""Static contract checks for the dependency-free story structure review UI."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "styles.css").read_text(encoding="utf-8")

# These checks protect the user-visible review surface without requiring a
# browser or a build tool in the local-first project.
assert "function storyGraphSvg(relationships)" in APP
assert "function storyTimelineHtml(events)" in APP
assert "function knowledgeScopeHtml()" in APP
assert "knowledge_scope" in APP and "scope.filtered" in APP
assert "data-action=\"refreshStory\"" in APP
assert "aria-label=\"人物关系图\"" in APP
for token in ("story-graph", "story-timeline", "scope-review-row", "story-scope-badge"):
    assert token in CSS, token

# The graph and scope review must escape persisted author content before it is
# placed in SVG/HTML templates.
assert "esc(label)" in APP and "esc(item.entity_name" in APP
print("frontend story fixtures: ok")
