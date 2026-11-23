"""backup.py, 2026-11-23: the raw archive's mirror, the repository bundle,
--verify, and the second destination.

The first version left out data/codal_raw as "re-downloadable" (24,586
documents, three and a half months of paced fetching), the code had no copy
outside .git, nothing ever re-read a night, and everything sat on one disk.
"""

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import backup


def _store(path, rows=3):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    conn.commit()
    conn.close()


def _tree(folder):
    """Every file under `folder` as {relative path: size}."""

    out = {}
    for base, _dirs, names in os.walk(folder):
        for name in names:
            full = os.path.join(base, name)
            out[os.path.relpath(full, folder).replace("\\", "/")] = \
                os.path.getsize(full)
    return out


class Project(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="sirat-bk-root-")
        self.dest = tempfile.mkdtemp(prefix="sirat-bk-dest-")
        self.second = tempfile.mkdtemp(prefix="sirat-bk-second-")
        _store(os.path.join(self.root, "sirat_market.db"))
        os.makedirs(os.path.join(self.root, "data"))
        for name in ("fx_rates.json", "outlook_calibration.json"):
            with open(os.path.join(self.root, "data", name), "w") as handle:
                handle.write("{}")
        self.document("12", "1643212.gz", b"a" * 40)
        self.document("95", "1642195.gz", b"b" * 60)

    def tearDown(self):
        for folder in (self.root, self.dest, self.second):
            shutil.rmtree(folder, ignore_errors=True)

    def document(self, shelf, name, body):
        folder = os.path.join(self.root, "data", "codal_raw", shelf)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name), "wb") as handle:
            handle.write(body)

    def night(self, day, **options):
        return backup.run(root=self.root, dest=self.dest,
                          now=datetime(2026, 11, day, 19, 30), **options)

    def manifest(self, day, dest=None):
        with open(os.path.join(dest or self.dest, "2026-11-%02d" % day,
                               backup.MANIFEST), encoding="utf-8") as handle:
            return json.load(handle)


class FilesTests(Project):

    def test_the_dollar_series_and_the_calibration_are_in_a_night(self):
        self.night(23)
        names = {entry["name"] for entry in self.manifest(23)["files"]}
        self.assertIn("data/fx_rates.json", names)
        self.assertIn("data/outlook_calibration.json", names)


class ArchiveTests(Project):

    def mirrored(self):
        found = _tree(os.path.join(self.dest, backup.ARCHIVE_DEST))
        found.pop(backup.ARCHIVE_NOTE, None)
        return found

    def test_the_archive_is_mirrored_beside_the_nights_not_inside_them(self):
        summary = self.night(23)
        self.assertEqual(self.mirrored(), {"12/1643212.gz": 40,
                                           "95/1642195.gz": 60})
        self.assertEqual((summary["archive"]["copied"],
                          summary["archive"]["files"]), (2, 2))
        self.assertFalse(os.path.exists(
            os.path.join(self.dest, "2026-11-23", "data", "codal_raw")))
        self.assertEqual(summary["problems"], [])

    def test_a_later_night_copies_only_what_is_new(self):
        self.night(22)
        self.document("07", "1650007.gz", b"c" * 10)
        with mock.patch.object(backup.shutil, "copyfile",
                               wraps=shutil.copyfile) as copied:
            summary = self.night(23)
        self.assertEqual(summary["archive"]["copied"], 1)
        self.assertEqual([os.path.basename(c[0][0]) for c in copied.call_args_list],
                         ["1650007.gz"])
        self.assertEqual(len(self.mirrored()), 3)

    def test_nothing_in_the_mirror_is_ever_deleted_or_pruned(self):
        self.night(20)
        os.unlink(os.path.join(self.root, "data", "codal_raw", "12", "1643212.gz"))
        for day in (21, 22, 23):
            backup.run(root=self.root, dest=self.dest, keep=1,
                       now=datetime(2026, 11, day, 19, 30))
        self.assertIn("12/1643212.gz", self.mirrored())
        self.assertEqual(backup.nights(self.dest), ["2026-11-23"])

    def test_a_half_written_document_waits_for_the_next_night(self):
        self.document("12", "1650012.gz.tmp", b"half")
        self.night(23)
        self.assertNotIn("12/1650012.gz.tmp", self.mirrored())

    def test_a_document_cut_short_in_the_mirror_is_copied_again(self):
        self.night(22)
        with open(os.path.join(self.dest, backup.ARCHIVE_DEST, "12",
                               "1643212.gz"), "wb") as handle:
            handle.write(b"a" * 7)
        self.night(23)
        self.assertEqual(self.mirrored()["12/1643212.gz"], 40)

    def test_a_mirror_that_fails_is_a_problem_but_the_night_stands(self):
        failed = {"files": 2, "bytes": 100, "copied": 0, "copied_bytes": 0,
                  "failed": 2, "error": "0.1 GB free"}
        with mock.patch.object(backup, "mirror_tree", return_value=failed):
            summary = self.night(23)
        self.assertEqual(backup.nights(self.dest), ["2026-11-23"])
        self.assertEqual(len(summary["problems"]), 1)
        self.assertIn("codal_raw", summary["problems"][0])

    def test_too_little_room_for_the_archive_copies_nothing(self):
        self.night(22, archive=False)
        with mock.patch.object(backup.shutil, "disk_usage",
                               return_value=mock.Mock(free=10)):
            stats = backup.mirror_archive(self.root, self.dest)
        self.assertEqual((stats["copied"], stats["failed"]), (0, 2))
        self.assertIn("needed", stats["error"])

    def test_a_project_without_an_archive_has_nothing_to_mirror(self):
        shutil.rmtree(os.path.join(self.root, "data", "codal_raw"))
        summary = self.night(23)
        self.assertIsNone(summary["archive"])
        self.assertEqual(summary["problems"], [])

    def test_the_cli_exit_code_says_when_something_after_the_night_failed(self):
        summary = {"folder": "x", "files": [], "bytes": 0, "seconds": 1,
                   "removed": [], "skipped": [], "archive": None,
                   "mirror": None, "problems": ["codal_raw mirror: 2 not copied"]}
        with mock.patch.object(backup, "run", return_value=summary), \
                mock.patch("sys.stdout"):
            self.assertEqual(backup.main(["--dest", self.dest]), 1)
        summary["problems"] = []
        with mock.patch.object(backup, "run", return_value=summary), \
                mock.patch("sys.stdout"):
            self.assertEqual(backup.main(["--dest", self.dest]), 0)


class BundleTests(Project):

    def test_no_repository_no_bundle_and_no_error(self):
        self.night(23)
        self.assertIn("%s (no repository)" % backup.BUNDLE,
                      self.manifest(23)["skipped"])

    def test_git_not_found_is_skipped_quietly(self):
        os.makedirs(os.path.join(self.root, ".git"))
        with mock.patch.object(backup, "find_git", return_value=None):
            summary = self.night(23)
        self.assertIn("%s (git not found)" % backup.BUNDLE, summary["skipped"])
        self.assertEqual(summary["problems"], [])

    def test_the_bundle_is_a_file_of_the_night_with_its_hash(self):
        os.makedirs(os.path.join(self.root, ".git"))
        asked = []

        def git(command, **_options):
            asked.append(command)
            with open(command[command.index("create") + 1], "wb") as handle:
                handle.write(b"# v2 git bundle\n")
            return mock.Mock(returncode=0, stderr="")

        with mock.patch.object(backup, "find_git", return_value="git.exe"), \
                mock.patch.object(backup.subprocess, "run", side_effect=git):
            self.night(23)
        entry = next(e for e in self.manifest(23)["files"]
                     if e["name"] == backup.BUNDLE)
        path = os.path.join(self.dest, "2026-11-23", backup.BUNDLE)
        self.assertEqual(entry["sha256"], backup._sha256(path))
        # Read-only on the repository: bundle create, every ref, nothing else.
        self.assertEqual(asked[0][:4], ["git.exe", "-C", self.root, "bundle"])
        self.assertEqual(asked[0][-1], "--all")

    def test_a_git_that_fails_costs_only_the_bundle(self):
        os.makedirs(os.path.join(self.root, ".git"))
        with mock.patch.object(backup, "find_git", return_value="git.exe"), \
                mock.patch.object(backup.subprocess, "run", return_value=mock.Mock(
                    returncode=128, stderr="fatal: not a git repository")):
            summary = self.night(23)
        self.assertTrue(any("git bundle failed" in s for s in summary["skipped"]))
        self.assertEqual(backup.nights(self.dest), ["2026-11-23"])

    def test_git_is_looked_for_where_the_installer_puts_it(self):
        """A task's shell does not always have it on PATH."""

        with mock.patch.object(backup.shutil, "which", return_value=None), \
                mock.patch.object(backup.os.path, "isfile",
                                  side_effect=lambda p: p == backup.GIT_PLACES[0]):
            self.assertEqual(backup.find_git(), backup.GIT_PLACES[0])
        with mock.patch.object(backup.shutil, "which", return_value=None), \
                mock.patch.object(backup.os.path, "isfile", return_value=False):
            self.assertIsNone(backup.find_git())


class VerifyTests(Project):

    def test_a_good_night_verifies_and_nothing_is_written(self):
        self.night(23)
        before = _tree(self.dest)
        good, lines = backup.verify(self.dest, root=self.root)
        self.assertTrue(good, lines)
        self.assertTrue(any("quick_check ok" in line for line in lines))
        self.assertTrue(any("2 of 2 documents mirrored" in line for line in lines))
        self.assertEqual(_tree(self.dest), before)       # no -shm, no journal

    def test_a_changed_file_fails_the_night(self):
        self.night(23)
        with open(os.path.join(self.dest, "2026-11-23", "data",
                               "fx_rates.json"), "w") as handle:
            handle.write("[]")
        good, lines = backup.verify(self.dest, "2026-11-23", root=self.root)
        self.assertFalse(good)
        self.assertTrue(any("BAD" in line and "fx_rates" in line for line in lines))

    def test_a_missing_file_and_a_damaged_database_fail_it(self):
        self.night(23)
        folder = os.path.join(self.dest, "2026-11-23")
        os.unlink(os.path.join(folder, "data", "outlook_calibration.json"))
        with mock.patch.object(backup, "_quick_check",
                               return_value="*** page 7 never used"):
            good, lines = backup.verify(self.dest, root=self.root)
        self.assertFalse(good)
        text = "\n".join(lines)
        self.assertIn("MISSING", text)
        self.assertIn("quick_check says", text)

    def test_an_unknown_night_and_an_empty_destination_fail(self):
        self.assertFalse(backup.verify(self.dest, root=self.root)[0])
        self.night(23)
        self.assertFalse(backup.verify(self.dest, "2026-11-01", root=self.root)[0])

    def test_the_cli_verifies_the_newest_night_by_default(self):
        self.night(22)
        self.night(23)
        with mock.patch.object(backup, "verify",
                               return_value=(True, ["night"])) as verify, \
                mock.patch("sys.stdout"):
            self.assertEqual(backup.main(["--dest", self.dest, "--verify"]), 0)
            verify.assert_called_with(self.dest, None)
            self.assertEqual(backup.main(
                ["--dest", self.dest, "--verify", "2026-11-22"]), 0)
            verify.assert_called_with(self.dest, "2026-11-22")
        with mock.patch.object(backup, "verify", return_value=(False, [])), \
                mock.patch("sys.stdout"):
            self.assertEqual(backup.main(["--dest", self.dest, "--verify"]), 1)


class SecondDestinationTests(Project):

    def test_the_night_and_the_archive_reach_the_second_destination(self):
        summary = self.night(23, mirror=self.second)
        self.assertEqual(summary["problems"], [])
        self.assertEqual(backup.nights(self.second), ["2026-11-23"])
        self.assertTrue(backup.verify(self.second, root=self.root)[0])
        self.assertIn("95/1642195.gz",
                      _tree(os.path.join(self.second, backup.ARCHIVE_DEST)))

    def test_the_second_destination_keeps_the_same_number_of_nights(self):
        for day in (21, 22, 23):
            backup.run(root=self.root, dest=self.dest, keep=2, mirror=self.second,
                       now=datetime(2026, 11, day, 19, 30))
        self.assertEqual(backup.nights(self.second), ["2026-11-22", "2026-11-23"])

    def test_a_second_destination_that_cannot_be_written_is_a_problem_only(self):
        blocked = os.path.join(self.second, "a-file")
        with open(blocked, "w") as handle:
            handle.write("not a folder")
        summary = self.night(23, mirror=os.path.join(blocked, "backups"))
        self.assertEqual(backup.nights(self.dest), ["2026-11-23"])
        self.assertEqual(len(summary["problems"]), 1)
        self.assertIn("second destination", summary["problems"][0])

    def test_the_first_destination_is_not_its_own_mirror(self):
        summary = self.night(23, mirror=self.dest)
        self.assertIn("the first one", summary["problems"][0])
        self.assertEqual(backup.nights(self.dest), ["2026-11-23"])

    def test_the_environment_variable_names_it_and_the_flag_overrides(self):
        summary = {"folder": "x", "files": [], "bytes": 0, "seconds": 1,
                   "removed": [], "skipped": [], "archive": None,
                   "mirror": None, "problems": []}
        with mock.patch.object(backup, "run", return_value=summary) as run, \
                mock.patch("sys.stdout"):
            with mock.patch.dict(os.environ, {backup.MIRROR_ENV: "E:\\sirat"}):
                backup.main(["--dest", self.dest])
                self.assertEqual(run.call_args[1]["mirror"], "E:\\sirat")
                backup.main(["--dest", self.dest, "--mirror", "F:\\other"])
                self.assertEqual(run.call_args[1]["mirror"], "F:\\other")
            environment = dict(os.environ)
            environment.pop(backup.MIRROR_ENV, None)
            with mock.patch.dict(os.environ, environment, clear=True):
                backup.main(["--dest", self.dest])
                self.assertIsNone(run.call_args[1]["mirror"])

    def test_the_help_says_the_default_is_the_same_disk(self):
        import io
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            with self.assertRaises(SystemExit):
                backup.main(["--help"])
        self.assertIn("SAME DISK", out.getvalue())
        self.assertIn(backup.MIRROR_ENV, out.getvalue())


if __name__ == "__main__":
    unittest.main()
