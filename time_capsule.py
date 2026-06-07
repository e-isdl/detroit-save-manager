# time_capsule.py
# -*- coding: utf-8 -*-

"""
Time Capsule - universal game save backup & restore.

Launches a game, creates timestamped save backups while it is running, and
lets the player restore an older save before launch.  Supports per-game
profiles so you can use it with any game.
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


APP_NAME = "Time Capsule"
APP_VERSION = "3.0"
CONFIG_FILE = "config.ini"
LOG_FILE = "time_capsule.log"
PROFILES_DIR_NAME = "profiles"
DEFAULT_PROCESS_NAME = "Game.exe"
DEFAULT_SAVE_DIR = Path.home() / "Saved Games"
INVALID_SESSION_CHARS = r'<>:"/\|?*'


def get_app_dir() -> Path:
    """Return the folder containing the script or bundled executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = get_app_dir()
CONFIG_PATH = APP_DIR / CONFIG_FILE
LOG_PATH = APP_DIR / LOG_FILE
PROFILES_DIR = APP_DIR / PROFILES_DIR_NAME


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
            "BackupStoragePath": str(Path.home() / "TimeCapsuleBackups"),
            "SessionName": "default",
            "SaveFrequencyMinutes": "5",
            "MaxAutoSaves": "50",
            "LaunchBackup": "yes",
        }
        self.config["Documentation"] = {
            "Info": "Edit the Settings section or create a profile from the menu.",
            "GameExecutablePath": "Full path to the game .exe (required).",
            "SourceSavePath": "Folder containing the live game saves.",
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


class Vault:
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
        self.main_config_path = CONFIG_PATH
        self.config_manager = ConfigManager(CONFIG_PATH)
        self.active_profile_path: Path | None = None
        self.active_profile_name: str | None = None
        self._init_config()
        self.vault = Vault(self.config_manager)
        self.game_path: Path | None = None
        self.game_working_dir: Path | None = None
        self.game_process_name: str = DEFAULT_PROCESS_NAME
        self._get_game_executable_from_config()

    def _init_config(self) -> None:
        """If ActiveProfile is set in config.ini, delegate to that profile file."""
        tmp = configparser.ConfigParser(interpolation=None)
        tmp.read(str(self.main_config_path))
        name = tmp.get("Settings", "ActiveProfile", fallback="")
        if name:
            p = PROFILES_DIR / f"{name}.ini"
            if p.exists():
                self.active_profile_name = name
                self.active_profile_path = p
                self.config_manager = ConfigManager(p)
                logging.info("Active profile: %s (%s)", name, p)
                return
            logging.warning("Active profile '%s' not found at %s", name, p)
        self.config_manager = ConfigManager(self.main_config_path)

    def _save_config(self) -> None:
        """Write config to the active file (profile or config.ini)."""
        target = self.active_profile_path or self.main_config_path
        with open(str(target), "w", encoding="utf-8") as f:
            self.config_manager.config.write(f)

    def _set_active_profile(self, name: str) -> None:
        """Write ActiveProfile to config.ini without touching the profile file."""
        self.active_profile_name = name
        self.active_profile_path = PROFILES_DIR / f"{name}.ini"
        cfg = configparser.ConfigParser(interpolation=None)
        cfg["Settings"] = {"ActiveProfile": name}
        with open(str(self.main_config_path), "w", encoding="utf-8") as f:
            cfg.write(f)

    def _clear_active_profile(self) -> None:
        """Remove ActiveProfile so config.ini works standalone."""
        self.active_profile_name = None
        self.active_profile_path = None
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.add_section("Settings")
        cfg["Settings"]["ActiveProfile"] = ""
        with open(str(self.main_config_path), "w", encoding="utf-8") as f:
            cfg.write(f)
        self.config_manager = ConfigManager(self.main_config_path)

    def _get_game_executable_from_config(self) -> None:
        game_path = self.config_manager.get_path("GameExecutablePath")
        if not game_path:
            logging.error("FATAL: GameExecutablePath is not set in config.")
            return
        if not game_path.is_file():
            logging.error("FATAL: Game executable was not found: %s", game_path)
            return

        self.game_path = game_path
        self.game_working_dir = game_path.parent
        self.game_process_name = self.config_manager.get(
            "GameProcessName", DEFAULT_PROCESS_NAME,
        )
        logging.info("Game executable located: %s", self.game_path)
        logging.info("Game working directory set to: %s", self.game_working_dir)
        logging.info("Game process name: %s", self.game_process_name)

    def _is_game_running(self) -> bool:
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {self.game_process_name}"],
                text=True,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
            return self.game_process_name.lower() in result.stdout.lower()
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

    def _list_profiles(self) -> list[str]:
        """Return sorted list of profile names (filenames without .ini)."""
        if not PROFILES_DIR.is_dir():
            return []
        return sorted(
            p.stem for p in PROFILES_DIR.iterdir()
            if p.suffix.lower() == ".ini"
        )

    def _create_profile_wizard(self, is_first: bool = False) -> bool:
        """Interactive wizard that creates a new profile .ini file."""
        print(f"\n{'=' * 70}")
        print(f"Create a new game profile")
        print(f"{'=' * 70}\n")

        name = None
        while True:
            raw = input("Friendly name for this game (e.g. 'Cyberpunk 2077'): ").strip()
            if not raw:
                print("  Name cannot be empty.")
                continue
            cleaned = "".join(c for c in raw if c not in '<>:"/\\|?*')
            if not cleaned:
                print("  Name contains only invalid characters. Try again.")
                continue
            name = cleaned
            break

        cfg = configparser.ConfigParser(interpolation=None)
        cfg["Settings"] = {}

        print("\n--- Game executable ---")
        current_raw = self.config_manager.get("GameExecutablePath", "")
        exe_roots = [
            Path(os.environ.get("ProgramFiles", "C:\\Program Files")),
            Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")),
        ]
        exe_folders = []
        for root in exe_roots:
            if root.is_dir():
                for child in sorted(root.iterdir()):
                    if child.is_dir():
                        exe_folders.append(child)
        if exe_folders:
            print("  Pick your game's installation folder:")
            for i, p in enumerate(exe_folders[:30], 1):
                print(f"  {i}) {p.name}")
            print("  P) Paste the full path to the .exe")
            if current_raw:
                print(f"  Enter) Use previous: {current_raw}")
            else:
                print(f"  Enter) Paste instead")
            choice = input("\nChoice: ").strip().lower()
            if choice in ("skip", "exit", "quit"):
                return False
        else:
            choice = "p"

        if choice == "p" or not choice:
            raw = None
            while True:
                prompt = "Full path to the game .exe (or 'skip' to cancel): "
                if not raw:
                    raw = input(prompt).strip().strip('"\'')
                if not raw:
                    raw = input(prompt).strip().strip('"\'')
                    continue
                if raw.lower() in ("skip", "exit", "quit"):
                    return False
                p = Path(raw).expanduser()
                if p.suffix.lower() != ".exe":
                    print("  Path must end with .exe")
                    raw = None
                    continue
                p = p.resolve()
                if not p.is_file():
                    print(f"  File not found: {p}")
                    raw = None
                    continue
                cfg["Settings"]["GameExecutablePath"] = str(p)
                cfg["Settings"]["GameProcessName"] = p.name
                self.game_path = p
                self.game_working_dir = p.parent
                self.game_process_name = p.name
                break
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(exe_folders):
                folder = exe_folders[idx]
                exes = sorted(folder.rglob("*.exe"))
                if not exes:
                    print(f"  No .exe files in {folder.name}. Paste the path instead.")
                    raw = None
                    while True:
                        raw = input("Full path to the game .exe: ").strip().strip('"\'')
                        if not raw:
                            continue
                        p = Path(raw).expanduser().resolve()
                        if p.is_file() and p.suffix.lower() == ".exe":
                            cfg["Settings"]["GameExecutablePath"] = str(p)
                            cfg["Settings"]["GameProcessName"] = p.name
                            self.game_path = p
                            self.game_working_dir = p.parent
                            self.game_process_name = p.name
                            break
                        print("  File not found or not an .exe.")
                else:
                    print(f"\n  .exe files in {folder.name}:")
                    for i, x in enumerate(exes[:20], 1):
                        print(f"  {i}) {x.name}")
                    print("  P) Paste a different path")
                    sub = input("\nPick the game .exe: ").strip().lower()
                    if sub == "p" or not sub:
                        raw = None
                        while True:
                            raw = input("Full path to the game .exe: ").strip().strip('"\'')
                            if not raw:
                                continue
                            p = Path(raw).expanduser().resolve()
                            if p.is_file() and p.suffix.lower() == ".exe":
                                cfg["Settings"]["GameExecutablePath"] = str(p)
                                cfg["Settings"]["GameProcessName"] = p.name
                                self.game_path = p
                                self.game_working_dir = p.parent
                                self.game_process_name = p.name
                                break
                            print("  File not found or not an .exe.")
                    elif sub.isdigit():
                        si = int(sub) - 1
                        if 0 <= si < len(exes):
                            cfg["Settings"]["GameExecutablePath"] = str(exes[si])
                            cfg["Settings"]["GameProcessName"] = exes[si].name
                            self.game_path = exes[si]
                            self.game_working_dir = exes[si].parent
                            self.game_process_name = exes[si].name
                        else:
                            return False
                    else:
                        return False
            else:
                return False
        else:
            # User typed something else — treat as paste attempt
            raw = choice
            while True:
                p = Path(raw).expanduser()
                if p.suffix.lower() != ".exe":
                    print("  Path must end with .exe")
                    raw = input("Full path to the game .exe: ").strip().strip('"\'')
                    if not raw:
                        return False
                    continue
                p = p.resolve()
                if not p.is_file():
                    print(f"  File not found: {p}")
                    raw = input("Full path (or 'skip'): ").strip().strip('"\'')
                    if not raw or raw.lower() in ("skip", "exit", "quit"):
                        return False
                    continue
                cfg["Settings"]["GameExecutablePath"] = str(p)
                cfg["Settings"]["GameProcessName"] = p.name
                self.game_path = p
                self.game_working_dir = p.parent
                self.game_process_name = p.name
                break

        print("\n--- Save folder ---")
        current_save = self.config_manager.get("SourceSavePath", str(DEFAULT_SAVE_DIR))
        save_roots = [
            Path.home() / "Saved Games",
            Path.home() / "Documents" / "My Games",
        ]
        save_options = []
        for root in save_roots:
            if root.is_dir():
                for child in sorted(root.iterdir()):
                    if child.is_dir():
                        save_options.append(child)
        if save_options:
            print("  Pick your game's save folder:")
            for i, p in enumerate(save_options, 1):
                print(f"  {i}) {p}")
            print("  P) Paste a different path")
            print(f"  Enter) Use default: {current_save}")
            choice = input("\nChoice: ").strip().lower()
        else:
            print("  No game save folders detected automatically.")
            print(f"  Look in: {save_roots[0]} or {save_roots[1]}")
            choice = "p"
        if choice == "p":
            raw = input(f"Save folder path (Enter for default): ").strip()
            cfg["Settings"]["SourceSavePath"] = raw or current_save
        elif choice and choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(save_options):
                cfg["Settings"]["SourceSavePath"] = str(save_options[idx])
            else:
                cfg["Settings"]["SourceSavePath"] = current_save
        else:
            cfg["Settings"]["SourceSavePath"] = current_save

        print("\n--- Backup folder ---")
        current_backup = self.config_manager.get("BackupStoragePath", str(Path.home() / "TimeCapsuleBackups"))
        print(f"  Where to store the backups. Default is fine for most people.")
        raw = input(f"Backup folder (Enter=default) [{current_backup}]: ").strip()
        cfg["Settings"]["BackupStoragePath"] = raw or current_backup

        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        profile_path = PROFILES_DIR / f"{name}.ini"
        with open(str(profile_path), "w", encoding="utf-8") as f:
            cfg.write(f)

        self.active_profile_name = name
        self.active_profile_path = profile_path
        self.config_manager = ConfigManager(profile_path)
        self._set_active_profile(name)

        logging.info("Created profile '%s' at %s", name, profile_path)
        print(f"\nProfile '{name}' created and activated!")
        return True

    def _first_run_wizard(self) -> bool:
        """When no config exists, guide the user through creating a profile."""
        print(f"\n{'=' * 70}")
        print(f"Welcome to {APP_NAME} v{APP_VERSION}!")
        print(f"{'=' * 70}")
        print("This looks like your first run. Let's set up a game profile.\n")
        return self._create_profile_wizard(is_first=True)

    def _select_profile_menu(self) -> str | None:
        """Show profile picker, return selected profile name or None to quit."""
        profiles = self._list_profiles()
        while True:
            print("\n" + "=" * 70)
            print(f"Game Profiles")
            print("=" * 70)
            if not profiles:
                print("  No profiles saved yet.")
            for i, p in enumerate(profiles, 1):
                marker = "  <-- active" if p == self.active_profile_name else ""
                print(f"  {i}) {p}{marker}")
            print("-" * 30)
            print("  N) Create new profile")
            print("  Q) Back")
            if self.active_profile_name:
                print("  R) Remove active profile")

            choice = input("\nSelect: ").strip().lower()
            if choice == "q":
                return None
            if choice == "n":
                if self._create_profile_wizard():
                    return self.active_profile_name
                continue
            if choice == "r":
                if self.active_profile_name:
                    p = PROFILES_DIR / f"{self.active_profile_name}.ini"
                    if p.exists():
                        p.unlink()
                        logging.info("Removed profile '%s'", self.active_profile_name)
                    self._clear_active_profile()
                    profiles = self._list_profiles()
                    continue

            try:
                idx = int(choice) - 1
                if 0 <= idx < len(profiles):
                    name = profiles[idx]
                    p = PROFILES_DIR / f"{name}.ini"
                    if p.exists():
                        self.active_profile_name = name
                        self.active_profile_path = p
                        self.config_manager = ConfigManager(p)
                        self._set_active_profile(name)
                        logging.info("Switched to profile '%s'", name)
                        return name
            except (ValueError, IndexError):
                print("Invalid option.")
                continue

    def _display_menu(self):
        while True:
            backups = self.vault.get_sorted_backups()
            profile_tag = f" | Profile: [{self.active_profile_name}]" if self.active_profile_name else ""
            print("\n" + "=" * 70)
            print(f"{APP_NAME} v{APP_VERSION}{profile_tag} | Session: [{self.vault.session_name}]")
            print("=" * 70)
            print(" 0) Continue with Current Save (Normal Start)")
            print(" P) Switch game profile")
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
            if choice == "p":
                self._select_profile_menu()
                # Re-load vault with new profile settings
                self.vault = Vault(self.config_manager)
                continue
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
                if self.vault.restore_save(selected):
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
            profiles = self._list_profiles()
            if not profiles:
                if not self._first_run_wizard():
                    input("Setup cancelled. Press Enter to exit.")
                    return
            elif not self.active_profile_name or not self.game_path:
                if not self._select_profile_menu():
                    input("No profile selected. Press Enter to exit.")
                    return

            self.vault = Vault(self.config_manager)
            self._get_game_executable_from_config()

            if not self.game_path or not self.game_working_dir:
                input("Could not resolve game executable. Press Enter to exit.")
                return

        action = self._display_menu()
        if action != "start":
            logging.info("User exited without launching the game.")
            return

        if self.config_manager.getboolean("LaunchBackup", True):
            logging.info("Creating launch backup before starting the game.")
            self.vault.backup_current_save("LAUNCH")

        logging.info("Starting game...")
        try:
            subprocess.Popen([str(self.game_path)], cwd=self.game_working_dir)
        except Exception as error:
            logging.error("Failed to start the game: %s", error, exc_info=True)
            input("Press Enter to exit.")
            return

        if not self._wait_for_game_process(timeout=60):
            logging.error("Game process did not appear within 60 seconds.")
            input(f"The game may have failed to start. Check {LOG_FILE}, then press Enter to exit.")
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
                self.vault.backup_current_save()
                next_save_time = time.time() + save_frequency_sec
            time.sleep(15)

        logging.info("Game process has ended. Performing one final backup.")
        self.vault.backup_current_save("FINAL")
        logging.info("Time Capsule is now closing. Goodbye.")


if __name__ == "__main__":
    configure_logging()
    try:
        App().run()
    except KeyboardInterrupt:
        logging.info("Time Capsule stopped by user.")
    except Exception as error:
        logging.error("A critical and unexpected error occurred: %s", error, exc_info=True)
        input(f"A critical error occurred. Check {LOG_FILE}, then press Enter to exit.")
