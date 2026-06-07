# Time Capsule — Save Game Time Travel

A lightweight Windows CLI that creates timestamped backups of your save folder while you play any game, and lets you restore any earlier point in seconds.

Each backup is a **time capsule** — a preserved moment you can always return to.

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

1. Download `Time Capsule.exe` from the [Releases](https://github.com/e-isdl/detroit-save-manager/releases) page.
2. Put it in a folder and **double-click**.
3. The first-run wizard will ask for a profile name and the path to your game's `.exe`.
4. That's it. Time Capsule starts and shows the backup menu.

**Option B — Python script**

1. Download `savemanager.py`.
2. Run it:
   ```
   python savemanager.py
   ```
3. The first-run wizard will guide you through creating a profile.

## How to use

1. **Always launch the game through Time Capsule.** Don't run the game's `.exe` directly — it needs to know when the game is running to make backups.
2. When it starts, you'll see a menu of available backups.
3. Choose an option:
   - `0` — Launch the game with the current save (your normal choice, 99% of the time)
   - `P` — Switch to a different game profile
   - `1`, `2`, `3`, ... — Restore a specific backup. Type `yes` to confirm.
   - `Q` — Quit without launching
4. Time Capsule launches the game and monitors it in the background. Every N minutes, a new capsule is created. When you exit the game, one final capsule is made and it closes itself.

## Profiles

Each profile stores the game-specific settings for one game:

- Game executable path
- Game process name (for `tasklist` detection)
- Save folder location
- Backup storage folder

Press `P` in the main menu to create, switch, or remove profiles. Profiles are stored as `profiles/<Name>.ini` files next to the script (or `.exe`).

**Built-in preset:** The default process name is `DetroitBecomeHuman.exe` and the default save path points to the Quantic Dream save folder, so *Detroit: Become Human* works out of the box.

## How it works

- **Backup (create a capsule).** On each tick, the entire live save folder is copied to a new timestamped sub-folder under your backup location, named `$[YYYY-MM-DD HH.MM.SS]`. The copy is staged to a `.tmp` folder and atomically renamed, so a backup that gets interrupted mid-write never produces a half-copied snapshot.
- **Restore (open a capsule).** A safe swap is performed: your current save is backed up as a safety net, then the live save is replaced with the contents of the chosen backup. If anything goes wrong, the original save is automatically moved back into place.
- **Cleanup.** When the number of capsules exceeds `MaxAutoSaves`, the oldest ones are deleted automatically. Set to `0` to disable cleanup.
- **Log.** All activity is logged to `time_capsule.log` (rotates at 2 MB, keeps 3 backups).

## Configuration

Configuration is stored per-game in `profiles/<Name>.ini` files. Settings that apply across all games live in `config.ini`.

| Key | Default | Description |
|---|---|---|
| `GameExecutablePath` | *(set by wizard)* | Full path to the game's `.exe`. |
| `GameProcessName` | `DetroitBecomeHuman.exe` | Process name shown in Task Manager. |
| `SourceSavePath` | *(set by wizard)* | Folder containing the live game saves. |
| `BackupStoragePath` | `%USERPROFILE%\TimeCapsuleBackups` | Root folder for all backups. |
| `SessionName` | `default` | Sub-folder name for a playthrough. |
| `SaveFrequencyMinutes` | `5` | Minutes between automatic backups. |
| `MaxAutoSaves` | `50` | Maximum backups to keep. `0` = unlimited. |
| `LaunchBackup` | `yes` | Create an extra backup right before launching. |

## Safety notes

- Your current save is always backed up before restoring one, so restoring is reversible.
- Capsules live in a different folder from your live saves. Deleting your live save folder does not affect your capsules.
- Writes only happen inside the configured `BackupStoragePath`. Any restore target is validated to live inside that path before anything is done.

## Limitations

- Windows only. Uses `tasklist` for process detection and Windows path conventions.
- Backups are full copies of the save folder. With a 5-minute interval and a 50-backup limit, expect 50× the disk space of one save. Lower `MaxAutoSaves` or raise `SaveFrequencyMinutes` to reduce usage.

## Troubleshooting

- **"Game executable was not found"** — The path in the profile is wrong. Press `P` in the menu to edit or recreate the profile.
- **"Source save directory not found"** — The path in the profile is wrong, or the game has not yet created a save file. Check the actual save location in Windows Explorer.
- **No capsules are being created** — Make sure you're launching the game *through Time Capsule*, not by double-clicking the `.exe`. It needs to detect the running game process.
- **Capsules fill the disk** — Lower `MaxAutoSaves` or raise `SaveFrequencyMinutes` in the profile file.
- **Restore didn't work** — Check `time_capsule.log` for the error. A "PRE-RESTORE CURRENT" safety capsule is created before each restore, so nothing should be lost.

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
pyinstaller --onefile --name "Time Capsule" savemanager.py
```

Output is at `dist/Time Capsule.exe` (~8.5 MB).
