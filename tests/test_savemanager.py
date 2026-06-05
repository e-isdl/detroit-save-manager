import logging
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from savemanager import (
    ConfigManager,
    SaveManager,
    format_age,
    sanitize_session_name,
    unique_directory_path,
)


class TestSanitizeSessionName(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(sanitize_session_name("hello"), "hello")

    def test_invalid_chars_replaced(self):
        self.assertEqual(
            sanitize_session_name('a/b\\c:d*e?f"g<h>i|j'),
            "a_b_c_d_e_f_g_h_i_j",
        )

    def test_whitespace_collapsed(self):
        self.assertEqual(sanitize_session_name("a  b\tc"), "a b c")

    def test_stripped(self):
        self.assertEqual(sanitize_session_name("  name  "), "name")

    def test_dots_stripped(self):
        self.assertEqual(sanitize_session_name("...name..."), "name")

    def test_empty_falls_back_to_default(self):
        self.assertEqual(sanitize_session_name(""), "default")
        self.assertEqual(sanitize_session_name("   "), "default")
        self.assertEqual(sanitize_session_name("..."), "default")
        self.assertEqual(sanitize_session_name(" . "), "default")

    def test_all_invalid_keeps_underscores(self):
        self.assertEqual(sanitize_session_name("///"), "___")
        self.assertEqual(sanitize_session_name("<<>>"), "____")


class TestUniqueDirectoryPath(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_slot_when_empty(self):
        result = unique_directory_path(self.tmp, "foo")
        self.assertEqual(result, self.tmp / "foo")

    def test_second_slot_on_collision(self):
        (self.tmp / "foo").mkdir()
        result = unique_directory_path(self.tmp, "foo")
        self.assertEqual(result, self.tmp / "foo-01")

    def test_third_slot_on_two_collisions(self):
        (self.tmp / "foo").mkdir()
        (self.tmp / "foo-01").mkdir()
        result = unique_directory_path(self.tmp, "foo")
        self.assertEqual(result, self.tmp / "foo-02")


class TestFormatAge(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(format_age(time.time() - 30), "30s ago")

    def test_minutes(self):
        self.assertEqual(format_age(time.time() - 90), "1m ago")

    def test_hours(self):
        self.assertEqual(format_age(time.time() - 3700), "1h ago")

    def test_days(self):
        self.assertEqual(format_age(time.time() - 90000), "1d ago")

    def test_future_returns_just_now(self):
        self.assertEqual(format_age(time.time() + 100), "just now")


class TestConfigManager(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.config_path = self.tmpdir / "config.ini"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_default_when_missing(self):
        self.assertFalse(self.config_path.exists())
        cm = ConfigManager(self.config_path)
        self.assertTrue(self.config_path.exists())
        self.assertIn("Settings", cm.config.sections())
        self.assertEqual(cm.get("GameExecutablePath"), "")

    def test_reads_existing_values(self):
        self.config_path.write_text(
            "[Settings]\n"
            "GameExecutablePath = C:/Games/foo.exe\n"
            "MaxAutoSaves = 10\n",
            encoding="utf-8",
        )
        cm = ConfigManager(self.config_path)
        self.assertEqual(cm.get("GameExecutablePath"), "C:/Games/foo.exe")
        self.assertEqual(cm.getint("MaxAutoSaves"), 10)

    def test_expandvars_in_path(self):
        self.config_path.write_text(
            "[Settings]\n"
            "BackupStoragePath = %USERPROFILE%/savemanager-test-backups\n",
            encoding="utf-8",
        )
        cm = ConfigManager(self.config_path)
        result = cm.get_path("BackupStoragePath")
        self.assertNotIn("%USERPROFILE%", str(result))
        self.assertTrue(str(result).endswith("savemanager-test-backups"))


class TestSaveManager(unittest.TestCase):
    def _make_config(self, max_saves=50):
        self.config_path = self.tmpdir / "config.ini"
        self.config_path.write_text(
            "[Settings]\n"
            f"SourceSavePath = {self.source}\n"
            f"BackupStoragePath = {self.backup_root}\n"
            "SessionName = default\n"
            f"MaxAutoSaves = {max_saves}\n",
            encoding="utf-8",
        )
        return ConfigManager(self.config_path)

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.source = self.tmpdir / "src"
        self.backup_root = self.tmpdir / "backups"
        self.source.mkdir()
        (self.source / "save1.dat").write_text("v1")
        (self.source / "save2.dat").write_text("v1")
        self.sm = SaveManager(self._make_config())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_backup_creates_copy(self):
        result = self.sm.backup_current_save()
        self.assertIsNotNone(result)
        self.assertTrue(result.is_dir())
        self.assertEqual((result / "save1.dat").read_text(), "v1")
        self.assertEqual((result / "save2.dat").read_text(), "v1")

    def test_backup_label_added_to_name(self):
        result = self.sm.backup_current_save("LAUNCH")
        self.assertIsNotNone(result)
        self.assertIn("LAUNCH", result.name)

    def test_backup_skips_empty_source(self):
        for entry in self.source.iterdir():
            entry.unlink()
        result = self.sm.backup_current_save()
        self.assertIsNone(result)

    def test_restore_copies_backup_to_source(self):
        backup = self.sm.backup_current_save()
        (self.source / "save1.dat").write_text("v2")
        ok = self.sm.restore_save(backup)
        self.assertTrue(ok)
        self.assertEqual((self.source / "save1.dat").read_text(), "v1")
        self.assertEqual((self.source / "save2.dat").read_text(), "v1")

    def test_restore_creates_safety_backup(self):
        backup = self.sm.backup_current_save()
        (self.source / "save1.dat").write_text("v2")
        count_before = len(self.sm.get_sorted_backups())
        self.sm.restore_save(backup)
        count_after = len(self.sm.get_sorted_backups())
        self.assertEqual(count_after, count_before + 1)
        safety = [b for b in self.sm.get_sorted_backups() if "PRE-RESTORE" in b.name]
        self.assertEqual(len(safety), 1)
        self.assertEqual((safety[0] / "save1.dat").read_text(), "v2")

    def test_restore_rejects_path_outside_session(self):
        other_dir = self.tmpdir / "other"
        other_dir.mkdir()
        (other_dir / "save1.dat").write_text("v9")
        ok = self.sm.restore_save(other_dir)
        self.assertFalse(ok)
        self.assertEqual((self.source / "save1.dat").read_text(), "v1")

    def test_cleanup_stale_temporaries_removes_orphan(self):
        stale = self.sm.session_backup_dir / "stale.tmp"
        stale.mkdir()
        (stale / "junk").write_text("junk")
        self.sm._cleanup_stale_temporaries()
        self.assertFalse(stale.exists())

    def test_cleanup_stale_temporaries_keeps_real_backups(self):
        good = self.sm.session_backup_dir / "realfolder"
        good.mkdir()
        self.sm._cleanup_stale_temporaries()
        self.assertTrue(good.exists())

    def test_cleanup_old_saves_keeps_max(self):
        sm = SaveManager(self._make_config(max_saves=3))
        for _ in range(5):
            sm.backup_current_save()
        self.assertEqual(len(sm.get_sorted_backups()), 3)

    def test_cleanup_disabled_when_zero(self):
        sm = SaveManager(self._make_config(max_saves=0))
        for _ in range(5):
            sm.backup_current_save()
        self.assertEqual(len(sm.get_sorted_backups()), 5)


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main(verbosity=2)
