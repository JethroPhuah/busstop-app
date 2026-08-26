"""Static-asset guards.

These cover two mistakes that are invisible to Python tests but break the page
in the browser, one of which already shipped once: a permanently visible
lightbox overlay.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")
WEB_HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

# Elements the shared renderer dereferences without a null check. Every shell
# that loads app.js must contain all of them. The published shell once dropped
# the Refresh button, and wiring that missing element threw at the top level,
# which stopped load() ever running: the page sat on its placeholders forever
# with no error shown.
REQUIRED_IDS = {
    "board", "verdicts", "warnings", "clock",
    "lightbox", "lightbox-img", "lightbox-cap",
}


def rule_bodies(css, selector):
    """Every declaration block attached to an exact selector."""
    pattern = r"(?:^|[},])\s*" + re.escape(selector) + r"\s*\{([^}]*)\}"
    return re.findall(pattern, css, re.MULTILINE)


class TestHiddenAttribute(unittest.TestCase):
    """The `hidden` attribute is only `display: none` in the UA stylesheet, so
    an author-level `display` on the same element overrides it. Without a guard
    the element stays visible forever."""

    # Matched as a bool rather than via assertRegex so a failure reports the
    # problem instead of dumping the whole stylesheet.
    GUARD = re.compile(r"\[hidden\]\s*\{[^}]*display\s*:\s*none\s*!important")

    def test_guard_rule_exists(self):
        self.assertTrue(
            self.GUARD.search(CSS),
            "style.css must force [hidden] to display:none !important",
        )

    def test_every_hidden_element_is_covered(self):
        # Elements the page toggles via the hidden attribute.
        hidden_classes = re.findall(r'class="([^"]+)"[^>]*\shidden', HTML)
        self.assertTrue(hidden_classes, "expected at least one hidden element")
        for cls in hidden_classes:
            for name in cls.split():
                for body in rule_bodies(CSS, "." + name):
                    if "display" in body:
                        # Allowed only because the !important guard outranks it.
                        self.assertTrue(
                            self.GUARD.search(CSS),
                            "." + name + " sets display but no [hidden] guard exists",
                        )


ID_LOOKUP = r'getElementById\(["\']([^"\']+)["\']\)'


class TestWiring(unittest.TestCase):
    def test_every_referenced_id_exists_locally(self):
        ids = set(re.findall(ID_LOOKUP, JS))
        self.assertTrue(ids)
        present = set(re.findall(r'id="([^"]+)"', HTML))
        self.assertEqual(ids - present, set(), "app.js references missing element ids")

    def test_required_ids_present_in_every_shell(self):
        for name, html in (("static/index.html", HTML), ("web/index.html", WEB_HTML)):
            present = set(re.findall(r'id="([^"]+)"', html))
            self.assertEqual(REQUIRED_IDS - present, set(),
                             "%s is missing elements app.js needs" % name)

    def test_required_ids_are_really_used(self):
        # Guards REQUIRED_IDS against drifting into a stale wish list.
        for element_id in REQUIRED_IDS:
            self.assertIn(element_id, JS, "%s is not used by app.js" % element_id)

    def test_optional_elements_are_guarded(self):
        """Anything app.js looks up but a shell may omit must be null-checked.

        This is the exact bug that broke the published page: the Refresh button
        exists only in the local shell, and calling addEventListener on the
        missing element threw before load() was ever reached.
        """
        looked_up = set(re.findall(ID_LOOKUP, JS))
        web_ids = set(re.findall(r'id="([^"]+)"', WEB_HTML))
        for element_id in looked_up - web_ids:
            stored = re.search(
                r'(?:const|let|var)\s+(\w+)\s*=\s*document\.getElementById\('
                r'["\']' + re.escape(element_id) + r'["\']\)', JS)
            self.assertIsNotNone(stored, "%s is fetched but not stored" % element_id)
            self.assertRegex(
                JS, r'if\s*\(\s*' + re.escape(stored.group(1)) + r'\s*\)',
                "%s is absent from web/index.html and not null-checked" % element_id)

    def test_assets_are_linked(self):
        self.assertIn("/static/style.css", HTML)
        self.assertIn("/static/app.js", HTML)
        self.assertIn("style.css", WEB_HTML)
        self.assertIn("app.js", WEB_HTML)


if __name__ == "__main__":
    unittest.main()
