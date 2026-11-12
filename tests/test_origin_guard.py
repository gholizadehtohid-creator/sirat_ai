"""app._same_origin_only: what another site's page may not do to the panel.

The full test, 2026-11-12: a DNS-rebinding page (Host and Origin both
evil.example) passed the POST check, and GET routes that start ~100 TSETMC
requests (?refresh=1) were outside it.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as panel  # noqa: E402


class OriginGuardTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = panel.app.test_client()

    def post_codal(self, **headers):
        import codal_sync
        with mock.patch.object(codal_sync, "start_background", return_value=True,
                               create=True) as start, \
                mock.patch.object(codal_sync, "cooling_off", return_value=0):
            r = self.client.post("/api/codal?now=1", headers=headers)
        return r.status_code, start

    def test_a_rebinding_page_is_refused(self):
        status, _ = self.post_codal(Host="evil.example:5000",
                                    Origin="http://evil.example:5000")
        self.assertEqual(status, 403)

    def test_a_cross_site_or_opaque_origin_is_refused(self):
        self.assertEqual(self.post_codal(Origin="http://evil.example")[0], 403)
        self.assertEqual(self.post_codal(Origin="null")[0], 403)

    def test_the_panels_own_page_and_a_headerless_client_pass(self):
        for headers in ({"Origin": "http://localhost"}, {}):
            status, _ = self.post_codal(**headers)
            self.assertNotEqual(status, 403, headers)

    def test_a_cross_site_get_cannot_start_a_refresh(self):
        import opportunities
        with mock.patch.object(opportunities, "report", return_value={}) as report:
            r = self.client.get("/api/opportunities?refresh=1",
                                headers={"Sec-Fetch-Site": "cross-site"})
            self.assertEqual(r.status_code, 403)
            report.assert_not_called()
            r = self.client.get("/api/opportunities?refresh=1",
                                headers={"Sec-Fetch-Site": "same-site"})
            self.assertEqual(r.status_code, 403)

    def test_the_panels_own_get_and_a_typed_url_pass(self):
        for site in ("same-origin", "none", None):
            headers = {"Sec-Fetch-Site": site} if site else {}
            r = self.client.get("/api/no-such-route", headers=headers)
            self.assertEqual(r.status_code, 404, site)      # reached routing

    def test_the_page_itself_opens_from_a_link(self):
        r = self.client.get("/", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status_code, 200)

    def test_a_foreign_host_cannot_read_the_page_either(self):
        r = self.client.get("/", headers={"Host": "evil.example:5000"})
        self.assertEqual(r.status_code, 403)
        r = self.client.get("/", headers={"Host": "127.0.0.1:5000"})
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
