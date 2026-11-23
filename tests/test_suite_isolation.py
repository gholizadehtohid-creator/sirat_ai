"""The suite cannot write the live stores, and does not probe a route.

tests/__init__.py installs both guards when the `tests` package is imported,
and every supported way of running the suite imports it:

    python ci.py
    python -m unittest discover -s tests -t .
    python -m unittest tests.test_x
    python -m unittest tests/test_x.py     (unittest reads the path as tests.test_x)

RUN AS A BARE SCRIPT THIS MODULE FAILS, ON PURPOSE (2026-11-23)

    python tests/test_suite_isolation.py

imports no package: the file is __main__, nothing installs the guard, and a
test that forgot to patch a path writes the real database -- which is how a
CI run once rewrote the live Codal cooldown. Every test module here can be
started that way (each ends in unittest.main()), and none of them is guarded
when it is. The first test below says so out loud instead of passing; the
rest are skipped then, because unguarded they would open the live stores.
"""

import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

# Read before anything of the project is imported: whether the guard was
# already in place when this module loaded is the fact under test.
GUARDED = hasattr(sqlite3.connect, "_sirat_real")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

STORES = ("sirat_market.db", "sirat_fundamentals.db", "sirat_research.db")

UNGUARDED = ("this run did not import the `tests` package, so the read-only "
             "guard is not installed: use `python -m unittest tests.test_x` "
             "(or tests/test_x.py after -m unittest), never "
             "`python tests/test_x.py`")


def refused(conn, statement="CREATE TABLE zz_sirat_guard_probe (x)"):
    """True if `conn` cannot write.

    A write inside a transaction that is always rolled back, so nothing is
    stored either way. Asking for the lock alone tells nothing: SQLite
    accepts BEGIN IMMEDIATE on a read-only connection (it runs it as a plain
    BEGIN; tried on 3.50.4), and only the write itself is refused.
    """

    conn.execute("BEGIN")
    try:
        conn.execute(statement)
    except sqlite3.OperationalError as exc:
        return "readonly" in str(exc).lower()
    finally:
        conn.rollback()
    return False


class GuardInstalledTests(unittest.TestCase):

    def test_the_guard_was_in_place_before_this_module_loaded(self):
        self.assertTrue(GUARDED, UNGUARDED)

    def test_it_is_the_packages_guard_and_it_keeps_the_real_connect(self):
        if not GUARDED:
            self.skipTest(UNGUARDED)

        package = sys.modules["tests"]

        self.assertIs(sqlite3.connect, package._guarded_connect)
        self.assertIsNot(package._real_connect, package._guarded_connect)


class LiveStoreTests(unittest.TestCase):
    """The stores themselves, opened the way a careless test would."""

    def setUp(self):
        if not GUARDED:
            self.skipTest(UNGUARDED)

    def test_each_live_store_opens_read_only(self):
        seen = 0
        for name in STORES:
            path = os.path.join(ROOT, name)
            if not os.path.exists(path):
                continue                    # a tree without its data
            seen += 1
            conn = sqlite3.connect(path, timeout=1)
            try:
                self.assertTrue(refused(conn), "%s opened WRITABLE" % name)
            finally:
                conn.close()
        if not seen:
            self.skipTest("no live store in this tree")

    def test_the_guarded_set_is_the_three_stores_and_the_data_folder(self):
        package = sys.modules["tests"]

        for name in STORES:
            self.assertTrue(package._live_store(os.path.join(ROOT, name)), name)
        self.assertTrue(package._live_store(
            os.path.join(ROOT, "data", "anything.db")))
        self.assertFalse(package._live_store(
            os.path.join(ROOT, "data", "fx_rates.json")))
        self.assertFalse(package._live_store(
            os.path.join(tempfile.gettempdir(), "sirat_market.db")))


class GuardMechanismTests(unittest.TestCase):
    """Every way a path can be spelled, on a stand-in registered as a store.

    A stand-in, not the real file: if a spelling slipped past the guard the
    connection would be writable, and that must not be learned on the live
    database.
    """

    def setUp(self):
        if not GUARDED:
            self.skipTest(UNGUARDED)

        self.package = sys.modules["tests"]
        self.dir = tempfile.mkdtemp(prefix="sirat-guard-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "stand_in.db")

        seed = self.package._real_connect(self.path)
        seed.execute("CREATE TABLE t (x)")
        seed.execute("INSERT INTO t VALUES (1)")
        seed.commit()
        seed.close()

        registered = set(self.package._STORES) | {os.path.normcase(self.path)}
        patcher = mock.patch.object(self.package, "_STORES", registered)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_read_only(self, conn, label):
        try:
            self.assertEqual(conn.execute("SELECT x FROM t").fetchall(), [(1,)])
            self.assertTrue(refused(conn, "INSERT INTO t VALUES (2)"),
                            "%s opened WRITABLE" % label)
        finally:
            conn.close()

    def test_a_plain_path_in_each_of_its_types(self):
        for target in (self.path, pathlib.Path(self.path),
                       os.fsencode(self.path), self.path.upper()):
            self.assert_read_only(sqlite3.connect(target), repr(target))
        self.assert_read_only(sqlite3.connect(database=self.path), "keyword")

    def test_a_writable_uri_in_each_of_its_spellings(self):
        slashed = self.path.replace("\\", "/")
        for uri in ("file:%s", "file:%s?mode=rw", "file:%s?mode=rwc&cache=private",
                    "file:/%s?mode=rw", "file:///%s", "file:///%s?mode=rwc",
                    "file://localhost/%s?mode=rw"):
            uri = uri % slashed.lstrip("/")
            self.assert_read_only(sqlite3.connect(uri, uri=True), uri)

    def test_a_read_only_uri_is_left_as_it_was(self):
        uri = "file:%s?mode=ro" % self.path.replace("\\", "/")
        self.assert_read_only(sqlite3.connect(uri, uri=True), uri)

    def test_the_stand_in_is_unchanged_afterwards(self):
        for target in (self.path, "file:///%s" % self.path.replace("\\", "/").lstrip("/")):
            conn = sqlite3.connect(target, uri=target.startswith("file:"))
            refused(conn, "INSERT INTO t VALUES (2)")
            conn.close()

        check = self.package._real_connect(self.path)
        try:
            self.assertEqual(check.execute("SELECT x FROM t").fetchall(), [(1,)])
        finally:
            check.close()

    def test_a_store_that_does_not_exist_is_not_created_in_its_place(self):
        ghost = os.path.join(self.dir, "ghost.db")
        registered = set(self.package._STORES) | {os.path.normcase(ghost)}

        with mock.patch.object(self.package, "_STORES", registered):
            conn = sqlite3.connect(ghost)
            try:
                conn.execute("CREATE TABLE IF NOT EXISTS made (x)")
                conn.commit()
            finally:
                conn.close()

        self.assertFalse(os.path.exists(ghost))

    def test_the_suites_own_databases_stay_writable(self):
        mine = os.path.join(self.dir, "mine.db")
        conn = sqlite3.connect(mine)
        try:
            conn.execute("CREATE TABLE t (x)")
            conn.execute("INSERT INTO t VALUES (1)")
            conn.commit()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM t").fetchone(), (1,))
        finally:
            conn.close()

        memory = sqlite3.connect(":memory:")
        memory.execute("CREATE TABLE t (x)")
        memory.close()


class NoRouteProbeTests(unittest.TestCase):
    """Building a session used to open TLS connections to TSETMC and codal.ir
    from the suite (providers.transport.discover_source_ip)."""

    def setUp(self):
        if not GUARDED:
            self.skipTest(UNGUARDED)

    def test_the_projects_own_switch_is_off_for_the_suite(self):
        import settings

        self.assertEqual(os.environ.get("SIRAT_BIND_SOURCE_IP"), "off")
        self.assertEqual(settings.BIND_SOURCE_IP.strip().lower(), "off")

    def test_finding_a_route_asks_nothing(self):
        from providers import transport

        with mock.patch.object(transport, "_reaches",
                               side_effect=AssertionError("probed a route")), \
                mock.patch.dict(transport._bind_cache):
            self.assertIsNone(transport.discover_source_ip(
                "isolation-test.invalid", force=True))


if __name__ == "__main__":
    unittest.main()
