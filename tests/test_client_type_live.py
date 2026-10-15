"""Tests for the whole-market intraday flow collector.

There is almost no arithmetic here to test. What has to be tested is the one
thing that has already gone wrong once in this project, on this exact data:

    GetClientTypeHistory spells a count `buy_I_Count`.
    GetClientTypeAll     spells the same count `buy_CountI`.

The first version of get_client_type_history used the wrong spelling. Nothing
raised. Every value column parsed, every count became NaN, and قدرت خریدار --
which exists only because the counts are published -- reported "unavailable"
for every symbol in the market while the composite score carried on using the
components that were left. A whole signal disappeared and the system called
it a data gap.

So the collector declares its count fields and FAILS when one is absent, and
these tests exist to keep it failing.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from providers.base import CLIENT_TYPE_LIVE_COLUMNS            # noqa: E402
from providers.tsetmc_cdn import TsetmcCdnProvider             # noqa: E402


def record(ins_code="1", **overrides):
    """One GetClientTypeAll row, spelled the way the live feed spells it."""

    item = {
        "insCode": ins_code,
        "buy_I_Volume": 90713479.0, "sell_I_Volume": 85208076.0,
        "buy_N_Volume": 822427.0, "sell_N_Volume": 6327830.0,
        "buy_CountI": 4837, "sell_CountI": 3798,
        "buy_CountN": 9, "sell_CountN": 8,
        # Present in the live payload, deliberately not read.
        "buy_CountDDD": 0, "buy_DDD_Volume": 0.0,
    }

    item.update(overrides)

    return item


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def provider_returning(payload=None, error=None):
    provider = TsetmcCdnProvider()

    def _get(url, timeout=None):
        if error:
            raise error

        return FakeResponse(payload)

    provider._get = _get

    return provider


class FieldNameTests(unittest.TestCase):
    """The transposition that failed silently must now fail loudly."""

    def test_the_live_spelling_parses(self):
        result = provider_returning(
            {"clientTypeAllDto": [record("1"), record("2")]}
        ).get_client_type_all()

        self.assertTrue(result.ok, result.error)
        self.assertEqual(list(result.data.columns), CLIENT_TYPE_LIVE_COLUMNS)
        self.assertEqual(len(result.data), 2)
        self.assertEqual(result.data["individual_buy_count"].iloc[0], 4837)

    def test_the_history_spelling_is_refused_not_tolerated(self):
        """`buy_I_Count` is the OTHER endpoint's name. Reject the payload.

        Tolerating it would produce a full table of NaN counts, which reads
        downstream as a market-wide data gap rather than as a broken adapter.
        """

        bad = record()
        bad["buy_I_Count"] = bad.pop("buy_CountI")

        result = provider_returning(
            {"clientTypeAllDto": [bad]}).get_client_type_all()

        self.assertFalse(result.ok)
        self.assertIn("buy_CountI", result.error)
        self.assertIn("field names have changed", result.error)

    def test_every_declared_count_is_required(self):
        for key in ("buy_CountI", "sell_CountI", "buy_CountN", "sell_CountN"):
            with self.subTest(field=key):
                bad = record()
                bad.pop(key)

                result = provider_returning(
                    {"clientTypeAllDto": [bad]}).get_client_type_all()

                self.assertFalse(result.ok)
                self.assertIn(key, result.error)

    def test_every_declared_volume_is_required(self):
        for key in ("buy_I_Volume", "sell_I_Volume",
                    "buy_N_Volume", "sell_N_Volume"):
            with self.subTest(field=key):
                bad = record()
                bad.pop(key)

                result = provider_returning(
                    {"clientTypeAllDto": [bad]}).get_client_type_all()

                self.assertFalse(result.ok)
                self.assertIn(key, result.error)

    def test_undocumented_ddd_fields_are_not_carried(self):
        """A column whose meaning is a guess is worse than one that is absent."""

        result = provider_returning(
            {"clientTypeAllDto": [record()]}).get_client_type_all()

        self.assertTrue(result.ok)

        for column in result.data.columns:
            self.assertNotIn("ddd", column.lower())


class ShapeTests(unittest.TestCase):

    def test_no_value_columns_are_invented(self):
        """The feed publishes volumes and counts. Rials are not available.

        Deriving a value from volume would need a price this payload does not
        carry, and a derived rial figure sitting in a column named like the
        stored one is how a snapshot gets mistaken for history.
        """

        result = provider_returning(
            {"clientTypeAllDto": [record()]}).get_client_type_all()

        for column in result.data.columns:
            self.assertNotIn("value", column)

    def test_rows_without_an_instrument_code_are_dropped(self):
        payload = {"clientTypeAllDto": [record("1"), record(""), record("2")]}

        result = provider_returning(payload).get_client_type_all()

        self.assertEqual(sorted(result.data["ins_code"]), ["1", "2"])

    def test_an_empty_payload_is_a_failure_not_an_empty_frame(self):
        result = provider_returning(
            {"clientTypeAllDto": []}).get_client_type_all()

        self.assertFalse(result.ok)
        self.assertIn("no rows", result.error)

    def test_a_transport_error_is_reported_not_raised(self):
        result = provider_returning(
            error=OSError("no route to host")).get_client_type_all()

        self.assertFalse(result.ok)
        self.assertIn("OSError", result.error)

    def test_unparseable_numbers_become_nan_rather_than_breaking_the_pull(self):
        """One bad instrument must not cost the other two thousand."""

        payload = {"clientTypeAllDto": [record("1", buy_I_Volume="n/a"),
                                        record("2")]}

        result = provider_returning(payload).get_client_type_all()

        self.assertTrue(result.ok)
        self.assertEqual(len(result.data), 2)
        self.assertTrue(result.data["individual_buy_volume"].isna().iloc[0])
        self.assertFalse(result.data["individual_buy_volume"].isna().iloc[1])


class BuyerPowerTests(unittest.TestCase):
    """The signal survives the missing value columns -- the reason this is useful."""

    def test_per_capita_ratio_is_computable_from_volume_and_count(self):
        """Within one instrument in one session, price cancels in the ratio.

        Measured against 57,649 stored rows that carry both: median relative
        difference 0.0135%, correlation 0.99999999. The test below is the
        arithmetic that claim rests on, not a re-measurement of it.
        """

        result = provider_returning(
            {"clientTypeAllDto": [record()]}).get_client_type_all()

        row = result.data.iloc[0]

        power = ((row["individual_buy_volume"] / row["individual_buy_count"])
                 / (row["individual_sell_volume"]
                    / row["individual_sell_count"]))

        self.assertAlmostEqual(power, (90713479.0 / 4837)
                               / (85208076.0 / 3798), places=9)

    def test_the_accounting_identity_holds_on_the_fixture(self):
        """Every share bought was sold. Measured true on all 2,286 live rows."""

        traded = record(buy_I_Volume=600.0, buy_N_Volume=400.0,
                        sell_I_Volume=250.0, sell_N_Volume=750.0)

        row = provider_returning(
            {"clientTypeAllDto": [traded]}).get_client_type_all().data.iloc[0]

        self.assertEqual(
            row["individual_buy_volume"] + row["institutional_buy_volume"],
            row["individual_sell_volume"] + row["institutional_sell_volume"])


if __name__ == "__main__":
    unittest.main()
