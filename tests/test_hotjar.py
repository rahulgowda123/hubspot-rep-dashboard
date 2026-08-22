"""Tests for the Hotjar wiring.

Stdlib unittest on purpose: this repo has no test runner and requirements.txt is
Flask + requests only, so a test that needs pytest or a JS runner would not run.

    python -m unittest discover -s tests -v

The JavaScript guards in static/hotjar.js are not exercised here -- there is no JS
toolchain in this project. What IS covered is the contract those guards depend on:
that app.py only ever hands the template a real digits ID or an empty string, and
that the value lands in the page as a safely quoted JS literal.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as mbr  # noqa: E402


def _module_source():
    """static/hotjar.js as text, read with the handle closed."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "static", "hotjar.js"), encoding="utf-8") as fh:
        return fh.read()


class HotjarTestCase(unittest.TestCase):
    def setUp(self):
        # The real .env sets HOTJAR_SITE_ID, and _hotjar_site_id() re-reads .env
        # whenever the var looks unset. Both have to be neutralised or the
        # "not configured" cases would silently pick up the developer's own ID.
        self._saved = os.environ.get("HOTJAR_SITE_ID")
        os.environ.pop("HOTJAR_SITE_ID", None)
        self._real_loader = mbr._load_dotenv_inline
        mbr._load_dotenv_inline = lambda: None
        self.client = mbr.app.test_client()

    def tearDown(self):
        mbr._load_dotenv_inline = self._real_loader
        os.environ.pop("HOTJAR_SITE_ID", None)
        if self._saved is not None:
            os.environ["HOTJAR_SITE_ID"] = self._saved

    # ----- the resolver -------------------------------------------------------

    def test_unset_resolves_to_off(self):
        self.assertEqual(mbr._hotjar_site_id(), "")

    def test_blank_and_whitespace_resolve_to_off(self):
        for raw in ("", "   ", "\t"):
            os.environ["HOTJAR_SITE_ID"] = raw
            self.assertEqual(mbr._hotjar_site_id(), "", "raw=%r" % raw)

    def test_unsubstituted_placeholder_resolves_to_off(self):
        # A packaging step that forgets to substitute must fall through to off,
        # never be sent to Hotjar as though it were a real ID.
        os.environ["HOTJAR_SITE_ID"] = "__HOTJAR_SITE_ID__"
        self.assertEqual(mbr._hotjar_site_id(), "")

    def test_configured_id_is_returned_trimmed(self):
        os.environ["HOTJAR_SITE_ID"] = "  6765855 "
        self.assertEqual(mbr._hotjar_site_id(), "6765855")

    # ----- what reaches the page ---------------------------------------------

    def test_page_is_wired_but_off_when_unset(self):
        html = self.client.get("/").get_data(as_text=True)
        # Wiring is present unconditionally...
        self.assertIn("/static/hotjar.js", html)
        # ...and blank is the off state, so initHotjar() returns false and the
        # remote snippet is never requested.
        self.assertRegex(html, r'hotjarSiteId:\s*""')
        self.assertNotIn("static.hotjar.com", html)

    def test_configured_id_reaches_the_page(self):
        os.environ["HOTJAR_SITE_ID"] = "6765855"
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("6765855", html)
        self.assertRegex(html, r'hotjarSiteId:\s*"6765855"')

    def test_junk_value_cannot_break_out_of_the_js_string(self):
        # tojson quoting is what keeps a hostile env var from becoming script.
        # The digits-only guard in hotjar.js then refuses to use the value at all.
        os.environ["HOTJAR_SITE_ID"] = '</script><script>alert(1)</script>'
        html = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("</script><script>", html)

    # ----- the module itself --------------------------------------------------

    def test_snippet_version_matches_between_settings_and_url(self):
        # A mismatch here loads a snippet Hotjar will not accept, and fails with
        # nothing in the console pointing at the cause.
        src = _module_source()
        self.assertIn("hjsv: SNIPPET_VERSION", src)
        self.assertIn('".js?sv=" + SNIPPET_VERSION', src)
        self.assertRegex(src, r"SNIPPET_VERSION\s*=\s*\d+")

    def test_module_guards_are_present(self):
        src = _module_source()
        self.assertIn('getElementById(SCRIPT_ID)', src)   # idempotent
        self.assertIn(r'/^\d+$/.test(HOTJAR_SITE_ID)', src)  # digits only
        self.assertIn("Number(HOTJAR_SITE_ID)", src)      # numeric hjid


if __name__ == "__main__":
    unittest.main(verbosity=2)
