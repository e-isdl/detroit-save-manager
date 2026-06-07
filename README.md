# Save Manager — Universal Game Save Backup & Restore

A lightweight Windows CLI that creates timestamped backups of your save folder while you play any game, and lets you restore any earlier point in seconds.

Think of it as a "time machine" for your decisions, in any single-player game. Supports per-game profiles so you can switch between games with one keypress.

## Features

- Automatic backups every N minutes while the game is running
- One final backup when you exit the game
- Interactive menu to restore any previous save (with confirmation prompt)
- Per-game profiles — set up each game once, then pick-and-go
- Per-session folders so multiple playthroughs stay isolated
- Automatic cleanup that keeps the backup count under a configurable limit
- Rotating log file (auto-trims to ~6 MB)
- Stale temporary folder cleanup on startup
- Single-file Python script with no third-party dependencies

## Requirements

- Windows
- Python 3.10 or newer (only needed if running from source)
- Any game that saves locally to a folder

## Setup

**Option A — Standalone .exe (recommended for most users)**

1. Download the `.exe` from the [Releases](https://github.com/e-isdl/detroit-save-manager/releases) page.
2. Put it in a folder and **double-click**.
3. The first-run wizard will ask for a profile name and the path to your game's `.exe`.
4. That's it. The manager starts and shows the backup menu.

**Option B — Python script**

1. Download `savemanager.py`.
2. Run it:
   ```
   python savemanager.py
   ```
3. The first-run wizard will guide you through creating a profile.

## How to use

1. **Always launch the game through this script.** Don't run the game's `.exe` directly — the script needs to know when the game is running to make backups.
2. When the script starts, you'll see a menu of available backups.
3. Choose an option:
   - `0` — Launch the game with the current save (your normal choice, 99% of the time)
   - `P` — Switch to a different game profile
   - `1`, `2`, `3`, ... — Restore a specific backup. The script will ask you to type `yes` to confirm.
   - `Q` — Quit without launching
4. The script launches the game and monitors it in the background. Every N minutes, a new backup is created. When you exit the game, one final backup is made and the script closes itself.

## Profiles

Each profile stores the game-specific settings for one game:

- Game executable path
- Game process name (for `tasklist` detection)
- Save folder location
- Backup storage folder

Press `P` in the main menu to create, switch, or remove profiles. Profiles are stored as `profiles/<Name>.ini` files next to the script (or `.exe`).

**Built-in preset:** The default process name is `DetroitBecomeHuman.exe` and the default save path points to the Quantic Dream save folder, so *Detroit: Become Human* works out of the box.

## How it works

- **Backup.** On each tick, the script copies the entire live save folder to a new timestamped sub-folder under your backup location, named `$[YYYY-MM-DD HH.MM.SS]`. The copy is staged to a `.tmp` folder and atomically renamed, so a backup that gets interrupted mid-write never produces a half-copied snapshot.
- **Restore.** The script performs a safe swap: it backs up your current save as a safety net, then replaces the live save with the contents of the chosen backup. If anything goes wrong during the swap, the original save is automatically moved back into place.
- **Cleanup.** When the number of backups exceeds `MaxAutoSaves`, the oldest ones are deleted automatically. Set to `0` to disable cleanup.
- **Log.** All activity is logged to `save_manager.log` (rotates at 2 MB, keeps 3 backups).

## Configuration

Configuration is stored per-game in `profiles/<Name>.ini` files. Settings that apply across all games live in `config.ini`.

| Key | Default | Description |
|---|---|---|
| `GameExecutablePath` | *(set by wizard)* | Full path to the game's `.exe`. |
| `GameProcessName` | `DetroitBecomeHuman.exe` | Process name shown in Task Manager. |
| `SourceSavePath` | *(set by wizard)* | Folder containing the live game saves. |
| `BackupStoragePath` | `%USERPROFILE%\SaveManagerBackups` | Root folder for all backups. |
| `SessionName` | `default` | Sub-folder name for a playthrough. |
| `SaveFrequencyMinutes` | `5` | Minutes between automatic backups. |
| `MaxAutoSaves` | `50` | Maximum backups to keep. `0` = unlimited. |
| `LaunchBackup` | `yes` | Create an extra backup right before launching. |

## Safety notes

- The script always backs up your current save before restoring one, so restoring is reversible.
- Backups live in a different folder from your live saves. Deleting your live save folder does not affect your backups.
- The script only writes inside the configured `BackupStoragePath`. It validates that any restore target lives inside that path before doing anything.

## Limitations

- Windows only. Uses `tasklist` for process detection and Windows path conventions.
- Backups are full copies of the save folder. With a 5-minute interval and a 50-backup limit, expect 50× the disk space of one save. Lower `MaxAutoSaves` or raise `SaveFrequencyMinutes` to reduce usage.

## Troubleshooting

- **"Game executable was not found"** — The path in the profile is wrong. Press `P` in the menu to edit or recreate the profile.
- **"Source save directory not found"** — The path in the profile is wrong, or the game has not yet created a save file. Check the actual save location in Windows Explorer.
- **No backups are being created** — Make sure you're launching the game *through this script*, not by double-clicking the `.exe`. The script needs to detect the running game process.
- **Backups fill the disk** — Lower `MaxAutoSaves` or raise `SaveFrequencyMinutes` in the profile file.
- **Restore didn't work** — Check `save_manager.log` for the error. The script writes a "PRE-RESTORE CURRENT" safety backup before each restore, so nothing should be lost.

## License

MIT — see `LICENSE`.

## Development

The project ships with 58 unit tests that cover the pure helpers, configuration handling, App-class behavior (with mocked `subprocess` and `input`), and the data-loss-critical restore and backup paths. No third-party dependencies are needed.

Run the tests:

```
python -m unittest tests.test_savemanager -v
```

Tests run automatically on push and pull request via the workflow in `.github/workflows/test.yml` (Windows runner, Python 3.10–3.13).

To add a test, drop a `test_...` method into the appropriate `Test...` class in `tests/test_savemanager.py`. New test classes can be added in the same file.

**Building the .exe (from source)**

```
pip install pyinstaller
pyinstaller --onefile --name "Save Manager" savemanager.py
```

Output is at `dist/Save Manager.exe` (~8.5 MB).
