"""A symbol is text wherever the panel writes it into the page.

The security review, 2026-11-23: panel.js builds its tables as HTML strings,
and table() takes every cell as markup. Almost every renderer wrapped its
strings in esc(); three did not, and passed the instrument's symbol straight
through -- the sector members, the fundamental breakdown, and the list of
shares left out of the screen. The chart tooltips' defaults did the same with
their labels (symbols, sector names). A symbol is third-party data: it comes
from the exchange's watch and from a reference list on GitHub, and an entry
spelled <svg/onload=...> would have run in the panel.

There is no JavaScript engine in this project's toolchain (no node, no
browser driver), so these tests read the two scripts as text. They pin the
shape -- a symbol is never a bare table cell, a default tooltip never prints
a label unescaped -- not the rendering.
"""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def script(name):
    with open(os.path.join(ROOT, "static", "js", name),
              encoding="utf-8") as handle:
        return handle.read()


class SymbolCellTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.code = script("panel.js")

    def test_a_symbol_is_never_a_bare_table_cell(self):
        """The three shapes a raw cell took: alone on its line, at the head
        of a row, and beside an escaped name."""

        for shape in (r"^\s*\w+\.symbol,\s*$",
                      r"\[\s*\w+\.symbol\s*,",
                      r"^\s*\w+\.symbol,\s*esc\("):
            found = re.findall(shape, self.code, flags=re.M)
            self.assertEqual(found, [], shape)

    def test_the_three_tables_escape_it(self):
        # sector members
        self.assertIn("esc(m.symbol), esc(m.name", self.code)
        # fundamental breakdown
        self.assertRegex(
            self.code,
            r"rows\.map\(r => \[\s*esc\(r\.symbol\),\s*\.\.\.FUND_COMPONENTS")
        # shares left out of the screen: the symbol and the board
        self.assertIn("sample.map(r => [esc(r.symbol), esc((r.name", self.code)
        self.assertIn('esc(r.board || "—")', self.code)

    def test_esc_is_the_panels_own_and_covers_markup(self):
        body = re.search(r"function esc\(value\) \{(.*?)\n\}", self.code,
                         flags=re.S).group(1)

        for entity in ("&amp;", "&lt;", "&gt;", "&quot;"):
            self.assertIn(entity, body)


class ChartTooltipTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.code = script("charts.js")

    def test_every_default_tooltip_title_is_escaped(self):
        titles = re.findall(r't-title">\$\{([^}]*)\}', self.code)

        self.assertGreaterEqual(len(titles), 4)
        for expression in titles:
            self.assertTrue(expression.startswith("escText("), expression)

    def test_every_default_tooltip_series_name_is_escaped(self):
        names = re.findall(r"</span>\$\{([^}]*)\}</span><b>", self.code)

        self.assertGreaterEqual(len(names), 3)
        for expression in names:
            self.assertTrue(expression.startswith("escText("), expression)

    def test_esc_text_covers_markup_and_both_quotes(self):
        body = re.search(r"function escText\(value\) \{(.*?)\n\}", self.code,
                         flags=re.S).group(1)

        self.assertIn("/[&<>\"']/g", body)
        for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
            self.assertIn(entity, body)

    def test_an_axis_label_is_set_as_text_not_markup(self):
        """Why the callers must NOT escape a label themselves: the axis
        prints it with textContent, and would show the entity."""

        self.assertIn('probe.textContent = String(label ?? "")', self.code)
        panel = script("panel.js")
        self.assertIsNone(re.search(r"label:\s*esc\(", panel))


if __name__ == "__main__":
    unittest.main()
