import logging
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from savemanager import (
    App,
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

    def test_cleanup_with_negative_max_warns_and_skips(self):
        sm = SaveManager(self._make_config(max_saves=-5))
        for _ in range(3):
            sm.backup_current_save()
        self.assertEqual(len(sm.get_sorted_backups()), 3)

    def test_backup_copies_subdirectories(self):
        sub = self.source / "subdir"
        sub.mkdir()
        (sub / "nested.dat").write_text("nested-v1")
        result = self.sm.backup_current_save()
        self.assertEqual((result / "subdir" / "nested.dat").read_text(), "nested-v1")

    def test_get_sorted_backups_excludes_tmp_folders(self):
        self.sm.backup_current_save()
        (self.sm.session_backup_dir / "leftover.tmp").mkdir()
        names = [b.name for b in self.sm.get_sorted_backups()]
        self.assertNotIn("leftover.tmp", names)
        self.assertEqual(len(names), 1)

    def test_get_sorted_backups_sorts_newest_first(self):
        first = self.sm.backup_current_save()
        time.sleep(1.05)
        second = self.sm.backup_current_save()
        ordered = self.sm.get_sorted_backups()
        self.assertEqual(ordered[0].name, second.name)
        self.assertEqual(ordered[1].name, first.name)

    def test_backup_label_with_spaces_preserved(self):
        result = self.sm.backup_current_save("LAUNCH 2")
        self.assertIn("LAUNCH 2", result.name)

    def test_restore_creates_source_dir_if_missing(self):
        backup = self.sm.backup_current_save()
        shutil.rmtree(self.source)
        ok = self.sm.restore_save(backup)
        self.assertTrue(ok)
        self.assertTrue(self.source.is_dir())
        self.assertEqual((self.source / "save1.dat").read_text(), "v1")

    def test_backup_idempotent_creates_distinct_folders(self):
        first = self.sm.backup_current_save()
        second = self.sm.backup_current_save()
        self.assertNotEqual(first.name, second.name)
        self.assertEqual(len(self.sm.get_sorted_backups()), 2)


class TestConfigManagerExtras(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.config_path = self.tmpdir / "config.ini"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_get_with_missing_key_uses_fallback(self):
        self.config_path.write_text("[Settings]\n", encoding="utf-8")
        cm = ConfigManager(self.config_path)
        self.assertEqual(cm.get("Nonexistent", "default-value"), "default-value")

    def test_getint_with_missing_key_uses_fallback(self):
        self.config_path.write_text("[Settings]\n", encoding="utf-8")
        cm = ConfigManager(self.config_path)
        self.assertEqual(cm.getint("Nonexistent", 42), 42)

    def test_getboolean_true_values(self):
        for truthy in ("yes", "true", "1", "on", "Yes", "TRUE"):
            with self.subTest(value=truthy):
                self.config_path.write_text(
                    f"[Settings]\nLaunchBackup = {truthy}\n", encoding="utf-8"
                )
                cm = ConfigManager(self.config_path)
                self.assertTrue(cm.getboolean("LaunchBackup"))

    def test_getboolean_false_values(self):
        for falsy in ("no", "false", "0", "off", "No", "FALSE"):
            with self.subTest(value=falsy):
                self.config_path.write_text(
                    f"[Settings]\nLaunchBackup = {falsy}\n", encoding="utf-8"
                )
                cm = ConfigManager(self.config_path)
                self.assertFalse(cm.getboolean("LaunchBackup"))

    def test_getint_raises_on_non_numeric(self):
        self.config_path.write_text(
            "[Settings]\nMaxAutoSaves = not-a-number\n", encoding="utf-8"
        )
        cm = ConfigManager(self.config_path)
        with self.assertRaises(ValueError):
            cm.getint("MaxAutoSaves")

    def test_get_path_empty_value_returns_empty_path(self):
        self.config_path.write_text(
            "[Settings]\nGameExecutablePath = \n", encoding="utf-8"
        )
        cm = ConfigManager(self.config_path)
        self.assertEqual(str(cm.get_path("GameExecutablePath")), ".")

    def test_get_path_expanduser(self):
        self.config_path.write_text(
            "[Settings]\nBackupStoragePath = ~/my-backups\n", encoding="utf-8"
        )
        cm = ConfigManager(self.config_path)
        result = cm.get_path("BackupStoragePath")
        self.assertNotIn("~", str(result))
        self.assertTrue(str(result).endswith("my-backups"))


class TestAppProcessCheck(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.source = self.tmpdir / "src"
        self.backup_root = self.tmpdir / "backups"
        self.fake_game = self.tmpdir / "fake_game.exe"
        self.source.mkdir()
        self.fake_game.write_bytes(b"")
        (self.source / "save.dat").write_text("v1")
        config_path = self.tmpdir / "config.ini"
        config_path.write_text(
            "[Settings]\n"
            f"GameExecutablePath = {self.fake_game}\n"
            f"SourceSavePath = {self.source}\n"
            f"BackupStoragePath = {self.backup_root}\n"
            "SessionName = default\n"
            "MaxAutoSaves = 50\n",
            encoding="utf-8",
        )
        self._patcher = patch("savemanager.CONFIG_PATH", config_path)
        self._patcher.start()
        self.app = App()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_is_game_running_true_when_process_in_output(self):
        mock_result = MagicMock(stdout="INFO: DetroitBecomeHuman.exe  12345")
        with patch("savemanager.subprocess.run", return_value=mock_result):
            self.assertTrue(self.app._is_game_running())

    def test_is_game_running_false_when_process_not_in_output(self):
        mock_result = MagicMock(stdout="INFO: No tasks are running...")
        with patch("savemanager.subprocess.run", return_value=mock_result):
            self.assertFalse(self.app._is_game_running())

    def test_is_game_running_false_on_subprocess_exception(self):
        with patch("savemanager.subprocess.run", side_effect=Exception("boom")):
            self.assertFalse(self.app._is_game_running())

    def test_wait_for_game_process_returns_true_when_found(self):
        with patch.object(App, "_is_game_running", return_value=True):
            self.assertTrue(self.app._wait_for_game_process(timeout=5))

    def test_wait_for_game_process_returns_false_on_timeout(self):
        with patch.object(App, "_is_game_running", return_value=False), \
             patch("savemanager.time.sleep"):
            self.assertFalse(self.app._wait_for_game_process(timeout=1))


class TestAppMenu(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.source = self.tmpdir / "src"
        self.backup_root = self.tmpdir / "backups"
        self.fake_game = self.tmpdir / "fake_game.exe"
        self.source.mkdir()
        self.fake_game.write_bytes(b"")
        (self.source / "save.dat").write_text("v1")
        config_path = self.tmpdir / "config.ini"
        config_path.write_text(
            "[Settings]\n"
            f"GameExecutablePath = {self.fake_game}\n"
            f"SourceSavePath = {self.source}\n"
            f"BackupStoragePath = {self.backup_root}\n"
            "SessionName = default\n"
            "MaxAutoSaves = 50\n",
            encoding="utf-8",
        )
        self._patcher = patch("savemanager.CONFIG_PATH", config_path)
        self._patcher.start()
        self.app = App()
        self.app.save_manager.backup_current_save()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_q_returns_none(self):
        with patch("builtins.input", return_value="q"):
            self.assertIsNone(self.app._display_menu())

    def test_zero_returns_start(self):
        with patch("builtins.input", return_value="0"):
            self.assertEqual(self.app._display_menu(), "start")

    def test_empty_input_reprompts(self):
        with patch("builtins.input", side_effect=["", "q"]):
            self.assertIsNone(self.app._display_menu())

    def test_non_numeric_input_reprompts(self):
        with patch("builtins.input", side_effect=["xyz", "q"]):
            self.assertIsNone(self.app._display_menu())

    def test_out_of_range_number_reprompts(self):
        with patch("builtins.input", side_effect=["99", "q"]):
            self.assertIsNone(self.app._display_menu())

    def test_restore_confirm_yes_returns_start(self):
        with patch("builtins.input", side_effect=["1", "yes"]):
            self.assertEqual(self.app._display_menu(), "start")

    def test_restore_confirm_no_continues_loop(self):
        with patch("builtins.input", side_effect=["1", "no", "q"]):
            self.assertIsNone(self.app._display_menu())

    def test_restore_confirm_anything_besides_yes_cancels(self):
        with patch("builtins.input", side_effect=["1", "y", "q"]):
            self.assertIsNone(self.app._display_menu())


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main(verbosity=2)
