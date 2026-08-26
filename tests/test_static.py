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


class TestWiring(unittest.TestCase):
    def test_every_referenced_id_exists(self):
        ids = set(re.findall(r'getElementById\(["\']([^"\']+)["\']\)', JS))
        self.assertTrue(ids)
        present = set(re.findall(r'id="([^"]+)"', HTML))
        self.assertEqual(ids - present, set(), "app.js references missing element ids")

    def test_assets_are_linked(self):
        self.assertIn("/static/style.css", HTML)
        self.assertIn("/static/app.js", HTML)


if __name__ == "__main__":
    unittest.main()
