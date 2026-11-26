"""The panel's tabs: every "go to" link lands in the tab that holds its
target, and every tab has its loaders (the summary tab was split into four,
2026-11-26)."""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class TabTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.html = read("templates", "dashboard.html")
        cls.js = read("static", "js", "panel.js")
        # Each tab panel's text, by its key.
        cls.panels = {}
        for m in re.finditer(r'<section class="tab-panel" role="tabpanel" data-tab="(\w+)">'
                             r'(.*?)</section>', cls.html, re.S):
            cls.panels[m.group(1)] = m.group(2)

    def test_the_summary_is_split_into_four_tabs(self):
        for tab in ("summary", "assets", "market", "codal"):
            self.assertIn(tab, self.panels)
            self.assertIn('data-tab="%s"' % tab, self.html.split("</nav>")[0])
        where = {"todayPlan": "summary", "holdingsCard": "summary",
                 "allocationTable": "assets", "cheapestCard": "assets",
                 "valuationCard": "market", "commoditiesCard": "market",
                 "marketFlowCard": "market", "fundFlowCard": "market",
                 "announcementsCard": "codal", "calendarBody": "codal"}
        for element, tab in where.items():
            self.assertIn('id="%s"' % element, self.panels[tab], element)

    def test_every_go_to_link_lands_in_the_tab_that_holds_its_target(self):
        links = re.findall(r'data-goto="(\w+):(\w+)"', self.html + self.js)
        self.assertTrue(links)
        for tab, element in links:
            # "tab:elementId" is the comment that documents the attribute.
            if tab in ("guide", "tab"):
                continue
            self.assertIn(tab, self.panels, (tab, element))
            self.assertIn('id="%s"' % element, self.panels[tab], (tab, element))

    def test_every_tab_and_the_welcome_page_end_with_a_way_back_up(self):
        """The owner, 2026-11-26: a button at the foot of each page instead
        of scrolling to the top. Every .tab-panel gets one from panel.js."""

        self.assertIn('document.querySelectorAll(".tab-panel").forEach', self.js)
        self.assertIn("data-to-top", self.js)
        self.assertIn("window.scrollTo({ top: 0", self.js)
        self.assertIn(".to-top", read("static", "css", "panel.css"))
        self.assertIn("data-to-top", read("templates", "welcome.html"))
        self.assertIn("data-to-top", read("static", "js", "welcome.js"))

    def test_every_tab_has_loaders_and_is_prefetched(self):
        loaders = self.js.split("const TAB_LOADERS = {")[1].split("\n};")[0]
        prefetch = re.search(r"const PREFETCH_ORDER = \[(.*?)\];", self.js).group(1)
        for tab in self.panels:
            if tab == "guide":
                continue
            self.assertRegex(loaders, r"\n  %s: \[" % tab, tab)
            self.assertIn('"%s"' % tab, prefetch, tab)


if __name__ == "__main__":
    unittest.main()
