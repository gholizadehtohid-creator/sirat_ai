"""Tests for the append-only research ledger.

The ledger's job is to make history unfalsifiable, so almost every test here
falsifies it deliberately and checks that the chain notices. Three attacks, and
the third is the one SQLite triggers cannot defend against:

    edit an entry      -> its hash no longer matches
    delete an entry    -> the link across the gap breaks
    rebuild the table  -> no UPDATE ever runs, and the chain still breaks
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sirat.experiments import ledger, stages
from sirat.experiments import manager as mgr

from tests.test_experiment_manager import PASSING, register


class LedgerTestCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sirat_ledger_")
        self.path = os.path.join(self.dir, "research.db")

    def close_one(self, experiment_id, fail_at=stages.ECONOMIC_SIGNAL):
        """Register an experiment and reject it at a chosen stage."""

        handle = register(self.path, experiment_id=experiment_id)

        for stage in stages.ORDER:
            if stage > fail_at:
                break
            metrics = dict(PASSING[stage])
            if stage == fail_at:
                metrics[sorted(metrics)[0]] = False
            handle.run_stage(stage, metrics, detail={"note": "test"})

        handle.decide(mgr.REJECT, "failed at stage %d" % fail_at, "tester")

        return handle


class Appending(LedgerTestCase):

    def test_a_decision_writes_a_ledger_entry_without_being_asked(self):
        self.close_one("E-A")

        rows = ledger.entries(self.path)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["experiment_id"], "E-A")

    def test_the_entry_carries_all_twelve_required_fields(self):
        self.close_one("E-A")

        entry = ledger.entries(self.path)[0]["entry"]

        for field in ledger.REQUIRED_FIELDS:
            self.assertIn(field, entry)
            self.assertNotIn(entry[field], (None, "", [], {}), field)

    def test_validation_evidence_carries_the_numbers_not_a_summary(self):
        self.close_one("E-A")

        evidence = ledger.entries(self.path)[0]["entry"]["validation_evidence"]

        self.assertTrue(evidence)
        self.assertIn("checks", evidence[0])
        self.assertIn("detail", evidence[0])
        self.assertEqual(evidence[0]["detail"]["note"], "test")

    def test_an_open_experiment_cannot_be_entered(self):
        handle = register(self.path, experiment_id="E-OPEN")
        handle.run_stage(stages.DATA_INTEGRITY,
                         dict(PASSING[stages.DATA_INTEGRITY]))

        with self.assertRaises(ledger.LedgerError):
            ledger.append(handle.record(), path=self.path)

    def test_the_first_entry_follows_the_genesis_hash(self):
        self.close_one("E-A")

        self.assertEqual(ledger.entries(self.path)[0]["prev_hash"],
                         ledger.GENESIS)

    def test_each_entry_chains_to_the_one_before_it(self):
        self.close_one("E-A")
        self.close_one("E-B")
        self.close_one("E-C")

        rows = ledger.entries(self.path)

        self.assertEqual(len(rows), 3)

        for earlier, later in zip(rows, rows[1:]):
            self.assertEqual(later["prev_hash"], earlier["entry_hash"])

    def test_head_advances_with_each_entry(self):
        before = ledger.head(self.path)
        self.close_one("E-A")
        after = ledger.head(self.path)

        self.assertEqual(before, ledger.GENESIS)
        self.assertNotEqual(after, before)

    def test_the_decision_returns_the_entry_hash(self):
        handle = register(self.path, experiment_id="E-A")
        handle.run_stage(stages.DATA_INTEGRITY,
                         dict(PASSING[stages.DATA_INTEGRITY],
                              survivorship=False))

        out = handle.decide(mgr.REJECT, "no snapshot", "tester")

        self.assertEqual(out["ledger_entry_hash"],
                         ledger.entries(self.path)[0]["entry_hash"])


class ChainIntegrity(LedgerTestCase):

    def test_an_untouched_chain_verifies(self):
        self.close_one("E-A")
        self.close_one("E-B")

        report = ledger.verify(self.path)

        self.assertTrue(report["intact"])
        self.assertEqual(report["entries"], 2)

    def test_an_empty_ledger_verifies(self):
        report = ledger.verify(self.path)

        self.assertTrue(report["intact"])
        self.assertEqual(report["entries"], 0)

    def test_sql_update_of_a_ledger_row_raises(self):
        self.close_one("E-A")

        conn = sqlite3.connect(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE ledger SET payload = '{}'")
        finally:
            conn.close()

    def test_sql_delete_of_a_ledger_row_raises(self):
        self.close_one("E-A")

        conn = sqlite3.connect(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM ledger")
        finally:
            conn.close()

    def _drop_triggers(self, conn):
        conn.executescript("DROP TRIGGER ledger_no_update;"
                           "DROP TRIGGER ledger_no_delete;")

    def test_an_edited_verdict_breaks_the_chain(self):
        """The attack the triggers stop -- verified to also fail without them."""

        self.close_one("E-A")
        self.close_one("E-B")

        row = ledger.entries(self.path)[0]

        entry = dict(row["entry"])
        entry["final_verdict"] = dict(entry["final_verdict"],
                                      decision="PROMOTE")

        conn = sqlite3.connect(self.path)
        try:
            self._drop_triggers(conn)
            conn.execute("UPDATE ledger SET payload = ? WHERE seq = ?",
                         (json.dumps(entry, ensure_ascii=False,
                                     sort_keys=True, separators=(",", ":"),
                                     default=str), row["seq"]))
            conn.commit()
        finally:
            conn.close()

        report = ledger.verify(self.path)

        self.assertFalse(report["intact"])
        self.assertEqual(report["broken_at"], row["seq"])
        self.assertIn("altered", report["reason"])

    def test_a_deleted_entry_breaks_the_chain(self):
        """What triggers cannot catch on a rebuilt table: a gap."""

        self.close_one("E-A")
        self.close_one("E-B")
        self.close_one("E-C")

        conn = sqlite3.connect(self.path)
        try:
            self._drop_triggers(conn)
            conn.execute("DELETE FROM ledger WHERE seq = 2")
            conn.commit()
        finally:
            conn.close()

        report = ledger.verify(self.path)

        self.assertFalse(report["intact"])
        self.assertEqual(report["broken_at"], 3)
        self.assertIn("removed or reordered", report["reason"])

    def test_a_rebuilt_ledger_with_a_missing_entry_is_detected(self):
        """No UPDATE runs, no trigger fires, and the chain still says no.

        This is the case the hash chain exists for: someone regenerates the
        table from a backup that is one experiment short. Nothing in SQLite
        would object.
        """

        self.close_one("E-A")
        self.close_one("E-B")

        rows = ledger.entries(self.path)

        conn = sqlite3.connect(self.path)
        try:
            self._drop_triggers(conn)
            conn.execute("DELETE FROM ledger")
            # Re-insert only the SECOND entry, with its hashes untouched.
            second = rows[1]
            conn.execute(
                "INSERT INTO ledger (seq, experiment_id, appended_at, "
                "prev_hash, entry_hash, payload) VALUES (?,?,?,?,?,?)",
                (1, second["experiment_id"], second["appended_at"],
                 second["prev_hash"], second["entry_hash"],
                 json.dumps(second["entry"], ensure_ascii=False,
                            sort_keys=True, separators=(",", ":"),
                            default=str)))
            conn.commit()
        finally:
            conn.close()

        report = ledger.verify(self.path)

        self.assertFalse(report["intact"])
        self.assertIn("removed or reordered", report["reason"])

    def test_the_same_experiment_cannot_be_entered_twice(self):
        handle = self.close_one("E-A")

        with self.assertRaises(sqlite3.IntegrityError):
            ledger.append(handle.record(), path=self.path)


class Export(LedgerTestCase):

    def test_json_export_carries_the_chain_report(self):
        self.close_one("E-A")

        out = ledger.export_json(self.path,
                                 os.path.join(self.dir, "ledger.json"))

        with open(out, encoding="utf-8") as handle:
            payload = json.load(handle)

        self.assertTrue(payload["chain"]["intact"])
        self.assertEqual(len(payload["ledger"]), 1)

    def test_markdown_export_names_every_experiment(self):
        self.close_one("E-A")
        self.close_one("E-B", fail_at=stages.EXECUTION_REALISM)

        out = ledger.export_markdown(self.path,
                                     os.path.join(self.dir, "LEDGER.md"))

        with open(out, encoding="utf-8") as handle:
            text = handle.read()

        self.assertIn("E-A", text)
        self.assertIn("E-B", text)
        self.assertIn("chain intact", text)
        self.assertIn("REJECT", text)

    def test_markdown_export_of_an_empty_ledger_says_so(self):
        out = ledger.export_markdown(self.path,
                                     os.path.join(self.dir, "LEDGER.md"))

        with open(out, encoding="utf-8") as handle:
            text = handle.read()

        self.assertIn("No experiment has been closed yet", text)


if __name__ == "__main__":
    unittest.main()
