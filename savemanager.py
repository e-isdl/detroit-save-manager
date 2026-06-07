# savemanager.py
# -*- coding: utf-8 -*-

"""
Detroit: Become Human Save Manager.

Launches the game, creates timestamped save backups while it is running, and
lets the player restore an older save before launch.
"""

import configparser
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


APP_NAME = "Detroit: Become Human Save Manager"
APP_VERSION = "3.0"
CONFIG_FILE = "config.ini"
LOG_FILE = "save_manager.log"
GAME_PROCESS_NAME = "DetroitBecomeHuman.exe"
DEFAULT_SAVE_DIR = Path.home() / "Saved Games" / "Quantic Dream" / "Detroit Become Human"
INVALID_SESSION_CHARS = r'<>:"/\|?*'


def get_app_dir() -> Path:
    """Return the folder containing the script or bundled executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = get_app_dir()
CONFIG_PATH = APP_DIR / CONFIG_FILE
LOG_PATH = APP_DIR / LOG_FILE


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - [%(levelname)s] - %(message)s",
        handlers=[
            RotatingFileHandler(
                LOG_PATH, mode="a", encoding="utf-8",
                maxBytes=2 * 1024 * 1024, backupCount=3,
            ),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.info("=" * 70)
    logging.info("%s v%s starting", APP_NAME, APP_VERSION)
    logging.info("Application folder: %s", APP_DIR)


def sanitize_session_name(name: str) -> str:
    cleaned = "".join("_" if ch in INVALID_SESSION_CHARS else ch for ch in name.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "default"


def unique_directory_path(parent: Path, name: str) -> Path:
    candidate = parent / name
    if not candidate.exists():
        return candidate
    for index in range(1, 1000):
        candidate = parent / f"{name}-{index:02d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not create a unique backup name for {name!r}")


def format_age(timestamp: float) -> str:
    """Return a short human-readable string for how long ago `timestamp` was."""
    delta = int(time.time() - timestamp)
    if delta < 0:
        return "just now"
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


class ConfigManager:
    """Loads config.ini and creates a public-friendly default file."""

    def __init__(self, config_path: Path):
        self.path = config_path
        self.config = configparser.ConfigParser(interpolation=None)
        if not self.path.exists():
            self._create_default_config()
        self.config.read(self.path, encoding="utf-8")

    def _create_default_config(self) -> None:
        self.config["Settings"] = {
            "GameExecutablePath": "",
            "SourceSavePath": str(DEFAULT_SAVE_DIR),
            "BackupStoragePath": str(Path.home() / "DetroitSaveBackups"),
            "SessionName": "default",
            "SaveFrequencyMinutes": "5",
            "MaxAutoSaves": "50",
            "LaunchBackup": "yes",
        }
        self.config["Documentation"] = {
            "Info": "Edit the Settings section. GameExecutablePath is required.",
            "GameExecutablePath": "Full path to DetroitBecomeHuman.exe.",
            "SourceSavePath": "Folder containing the live Detroit: Become Human saves.",
            "BackupStoragePath": "Folder where timestamped backups will be stored.",
            "SessionName": "Sub-folder name for a playthrough.",
            "SaveFrequencyMinutes": "How often to back up while the game is running.",
            "MaxAutoSaves": "Maximum backups to keep. Use 0 for unlimited.",
            "LaunchBackup": "yes/no. Create a backup immediately before launching.",
        }
        with self.path.open("w", encoding="utf-8") as configfile:
            self.config.write(configfile)

    def get(self, key: str, fallback=None):
        return self.config.get("Settings", key, fallback=fallback)

    def getint(self, key: str, fallback=None) -> int:
        return self.config.getint("Settings", key, fallback=fallback)

    def getboolean(self, key: str, fallback=False) -> bool:
        return self.config.getboolean("Settings", key, fallback=fallback)

    def get_path(self, key: str, fallback=None) -> Path:
        value = self.get(key, fallback)
        if not value:
            return Path()
        expanded = os.path.expandvars(os.path.expanduser(value))
        return Path(expanded)


class SaveManager:
    """Manages backup, restore, and cleanup operations."""

    def __init__(self, config: ConfigManager):
        self.config = config
        configured_session = self.config.get("SessionName", "default")
        self.session_name = sanitize_session_name(configured_session)
        if self.session_name != configured_session:
            logging.warning("SessionName contained invalid characters. Using: %s", self.session_name)

        self.source_save_dir = self.config.get_path("SourceSavePath", DEFAULT_SAVE_DIR)
        self.backup_root_dir = self.config.get_path("BackupStoragePath")
        if not self.backup_root_dir:
            logging.error("FATAL: BackupStoragePath is not set in config.ini.")
            sys.exit(1)

        self.session_backup_dir = self.backup_root_dir / self.session_name
        self._initialize_directories()

    def _initialize_directories(self) -> None:
        if not self.source_save_dir.is_dir():
            logging.error("FATAL: Game save directory not found: %s", self.source_save_dir)
            sys.exit(1)
        try:
            self.backup_root_dir.mkdir(parents=True, exist_ok=True)
            self.session_backup_dir.mkdir(parents=True, exist_ok=True)
            logging.info("Watching saves in: %s", self.source_save_dir)
            logging.info("Storing backups in: %s", self.session_backup_dir)
        except OSError as error:
            logging.error("Could not create backup directories: %s", error)
            sys.exit(1)
        self._cleanup_stale_temporaries()

    def _cleanup_stale_temporaries(self) -> None:
        if not self.session_backup_dir.exists():
            return
        for item in self.session_backup_dir.iterdir():
            if item.is_dir() and item.name.endswith(".tmp"):
                try:
                    shutil.rmtree(item)
                    logging.info("Removed stale temporary folder: %s", item.name)
                except OSError as error:
                    logging.warning(
                        "Could not remove stale temporary folder %s: %s",
                        item.name, error,
                    )

    def get_sorted_backups(self) -> list[Path]:
        if not self.session_backup_dir.exists():
            return []
        backups = [
            item
            for item in self.session_backup_dir.iterdir()
            if item.is_dir() and not item.name.endswith(".tmp")
        ]
        backups.sort(key=lambda item: (item.name, item.stat().st_ctime), reverse=True)
        return backups

    def _ensure_backup_is_inside_session(self, backup_path: Path) -> Path:
        backup_path = backup_path.resolve()
        session_dir = self.session_backup_dir.resolve()
        try:
            backup_path.relative_to(session_dir)
        except ValueError as error:
            raise ValueError("Selected backup is outside the configured backup session.") from error
        if not backup_path.is_dir():
            raise FileNotFoundError(f"Backup folder not found: {backup_path}")
        return backup_path

    def backup_current_save(self, label: str | None = None) -> Path | None:
        if not self.source_save_dir.exists() or not any(self.source_save_dir.iterdir()):
            logging.info("Source save directory is empty. Skipping backup.")
            return None

        timestamp = datetime.now().strftime("$[%Y-%m-%d %H.%M.%S]")
        backup_name = f"{timestamp} {label}" if label else timestamp
        destination = unique_directory_path(self.session_backup_dir, backup_name)
        temp_destination = unique_directory_path(self.session_backup_dir, f"{backup_name}.tmp")

        try:
            shutil.copytree(self.source_save_dir, temp_destination)
            temp_destination.rename(destination)
            logging.info("Created backup: %s", destination.name)
            self.cleanup_old_saves()
            return destination
        except Exception as error:
            logging.error("Failed to create backup: %s", error, exc_info=True)
            shutil.rmtree(temp_destination, ignore_errors=True)
            return None

    def restore_save(self, backup_container_path: Path) -> bool:
        try:
            backup_container_path = self._ensure_backup_is_inside_session(backup_container_path)
        except Exception as error:
            logging.error("Restore rejected: %s", error)
            return False

        logging.warning("Preparing to restore save from: %s", backup_container_path.name)
        safety_backup = self.backup_current_save("PRE-RESTORE CURRENT")
        if self.source_save_dir.exists() and safety_backup is None:
            logging.error("Restore cancelled because the current save could not be backed up safely.")
            return False

        restore_temp = unique_directory_path(self.source_save_dir.parent, f"{self.source_save_dir.name}_restore_in_progress")
        old_live = unique_directory_path(self.source_save_dir.parent, f"{self.source_save_dir.name}_before_restore")

        try:
            shutil.copytree(backup_container_path, restore_temp)
            if self.source_save_dir.exists():
                shutil.move(str(self.source_save_dir), str(old_live))
            shutil.move(str(restore_temp), str(self.source_save_dir))
            shutil.rmtree(old_live, ignore_errors=True)
            logging.info("Restore successful.")
            if safety_backup:
                logging.info("Previous current save kept at: %s", safety_backup)
            return True
        except Exception as error:
            logging.error("An error occurred during restore: %s", error, exc_info=True)
            shutil.rmtree(restore_temp, ignore_errors=True)
            if old_live.exists():
                if self.source_save_dir.exists():
                    shutil.rmtree(self.source_save_dir, ignore_errors=True)
                shutil.move(str(old_live), str(self.source_save_dir))
                logging.info("Original save restored from local safety folder.")
            return False

    def cleanup_old_saves(self) -> None:
        max_saves = self.config.getint("MaxAutoSaves", 50)
        if max_saves == 0:
            return
        if max_saves < 0:
            logging.warning("MaxAutoSaves cannot be negative. Cleanup skipped.")
            return

        backups = self.get_sorted_backups()
        if len(backups) <= max_saves:
            return

        saves_to_delete = backups[max_saves:]
        logging.info(
            "Cleanup: found %s saves (limit is %s). Deleting %s oldest saves.",
            len(backups),
            max_saves,
            len(saves_to_delete),
        )
        for save in saves_to_delete:
            try:
                shutil.rmtree(save)
                logging.info("Deleted old backup: %s", save.name)
            except OSError as error:
                logging.error("Failed to delete old backup %s: %s", save.name, error)


class App:
    def __init__(self):
        self.config_manager = ConfigManager(CONFIG_PATH)
        self.save_manager = SaveManager(self.config_manager)
        self.game_path: Path | None = None
        self.game_working_dir: Path | None = None
        self._get_game_executable_from_config()

    def _get_game_executable_from_config(self) -> None:
        game_path = self.config_manager.get_path("GameExecutablePath")
        if not game_path:
            logging.error("FATAL: GameExecutablePath is not set in config.ini.")
            return
        if not game_path.is_file():
            logging.error("FATAL: Game executable was not found: %s", game_path)
            return

        self.game_path = game_path
        self.game_working_dir = game_path.parent
        logging.info("Game executable located: %s", self.game_path)
        logging.info("Game working directory set to: %s", self.game_working_dir)

    def _is_game_running(self) -> bool:
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {GAME_PROCESS_NAME}"],
                text=True,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
            return GAME_PROCESS_NAME.lower() in result.stdout.lower()
        except Exception as error:
            logging.warning("Could not check game process: %s", error)
            return False

    def _wait_for_game_process(self, timeout: int = 60) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._is_game_running():
                logging.info("Game process detected. Beginning monitoring.")
                return True
            time.sleep(2)
        return False

    def _first_run_wizard(self) -> bool:
        print(f"\n{'=' * 70}")
        print(f"Welcome to {APP_NAME} v{APP_VERSION}!")
        print(f"{'=' * 70}")

        current_raw = self.config_manager.get("GameExecutablePath")
        if current_raw:
            print(f"The config file has: {Path(current_raw).expanduser()}")
            print("That file was not found. Let's fix it.\n")
        else:
            print("This looks like your first run. Let's set up the paths.\n")

        while True:
            raw = input("Full path to DetroitBecomeHuman.exe: ").strip().strip('"\'')
            if not raw:
                print("  Path cannot be empty.")
                continue
            if raw.lower() in ("skip", "exit", "quit"):
                return False

            p = Path(raw).expanduser()
            if p.suffix.lower() != ".exe":
                p = p / "DetroitBecomeHuman.exe"
            p = p.resolve()

            if not p.is_file():
                print(f"  File not found: {p}")
                print("  Check the path and try again, or type 'skip'.\n")
                continue

            self.game_path = p
            self.game_working_dir = p.parent
            self.config_manager.config.set("Settings", "GameExecutablePath", str(p))
            break

        default_save = str(DEFAULT_SAVE_DIR)
        current_save = self.config_manager.get("SourceSavePath")
        prompt = f"Save folder (Enter=default) [{current_save or default_save}]: "
        raw = input(prompt).strip()
        if raw:
            self.config_manager.config.set("Settings", "SourceSavePath", raw)

        default_backup = str(Path.home() / "DetroitSaveBackups")
        current_backup = self.config_manager.get("BackupStoragePath")
        prompt = f"Backup folder (Enter=default) [{current_backup or default_backup}]: "
        raw = input(prompt).strip()
        if raw:
            self.config_manager.config.set("Settings", "BackupStoragePath", raw)

        with open(self.config_manager.path, "w", encoding="utf-8") as f:
            self.config_manager.config.write(f)
        logging.info("First-run wizard completed. Config saved to %s", self.config_manager.path)
        print("\nConfiguration saved! Starting the Save Manager...\n")
        return True

    def _display_menu(self):
        while True:
            backups = self.save_manager.get_sorted_backups()
            print("\n" + "=" * 70)
            print(f"{APP_NAME} v{APP_VERSION} | Session: [{self.save_manager.session_name}]")
            print("=" * 70)
            print(" 0) Continue with Current Save (Normal Start)")
            print("-" * 30)
            if not backups:
                print("No backups found yet for this session.")
            for index, backup in enumerate(backups, 1):
                marker = "  <-- Newest" if index == 1 else ""
                age = format_age(backup.stat().st_mtime)
                print(f" {index}) {backup.name}  [{age}]{marker}")
            print("-" * 30)
            print(" Q) Quit")

            choice = input("\nSelect an option and press Enter: ").strip().lower()
            if choice == "q":
                return None
            if choice == "":
                print("Please enter a number or Q.")
                continue
            if choice == "0":
                return "start"
            try:
                index = int(choice) - 1
            except ValueError:
                print("Invalid input. Please enter a number or Q.")
                continue

            if 0 <= index < len(backups):
                selected = backups[index]
                confirm = input(
                    f"\nRestore from '{selected.name}' ({format_age(selected.stat().st_mtime)})?\n"
                    f"Your current save will be backed up first as a safety net.\n"
                    f"Type 'yes' to confirm: "
                ).strip().lower()
                if confirm != "yes":
                    print("Restore cancelled.")
                    continue
                if self.save_manager.restore_save(selected):
                    return "start"
                input("\nRestore failed. Check the log, then press Enter to return to menu.")
            else:
                print("Invalid number. Please try again.")

    def run(self) -> None:
        if self._is_game_running():
            logging.info("Game is already running. Entering monitoring mode only.")
            self.monitor_game()
            return

        if not self.game_path or not self.game_working_dir:
            if not self._first_run_wizard():
                input("Setup cancelled. Press Enter to exit.")
                return

        action = self._display_menu()
        if action != "start":
            logging.info("User exited without launching the game.")
            return

        if self.config_manager.getboolean("LaunchBackup", True):
            logging.info("Creating launch backup before starting the game.")
            self.save_manager.backup_current_save("LAUNCH")

        logging.info("Starting game...")
        try:
            subprocess.Popen([str(self.game_path)], cwd=self.game_working_dir)
        except Exception as error:
            logging.error("Failed to start the game: %s", error, exc_info=True)
            input("Press Enter to exit.")
            return

        if not self._wait_for_game_process(timeout=60):
            logging.error("Game process did not appear within 60 seconds.")
            input("The game may have failed to start. Check save_manager.log, then press Enter to exit.")
            return

        self.monitor_game()

    def monitor_game(self) -> None:
        save_frequency_minutes = self.config_manager.getint("SaveFrequencyMinutes", 5)
        if save_frequency_minutes <= 0:
            logging.warning("SaveFrequencyMinutes must be positive. Using 5 minutes.")
            save_frequency_minutes = 5

        save_frequency_sec = save_frequency_minutes * 60
        next_save_time = time.time() + save_frequency_sec
        logging.info("Monitoring game. Backups will occur every %s minutes.", save_frequency_minutes)

        while self._is_game_running():
            if time.time() >= next_save_time:
                self.save_manager.backup_current_save()
                next_save_time = time.time() + save_frequency_sec
            time.sleep(15)

        logging.info("Game process has ended. Performing one final backup.")
        self.save_manager.backup_current_save("FINAL")
        logging.info("Save Manager is now closing. Goodbye.")


if __name__ == "__main__":
    configure_logging()
    try:
        App().run()
    except KeyboardInterrupt:
        logging.info("Save Manager stopped by user.")
    except Exception as error:
        logging.error("A critical and unexpected error occurred: %s", error, exc_info=True)
        input("A critical error occurred. Check save_manager.log, then press Enter to exit.")
