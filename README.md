# Detroit: Become Human — Save Manager

A lightweight Windows CLI that creates timestamped backups of your *Detroit: Become Human* save folder while you play, and lets you restore any earlier point in seconds.

Think of it as a "time machine" for your decisions.

## Features

- Automatic backups every N minutes while the game is running
- One final backup when you exit the game
- Interactive menu to restore any previous save (with confirmation prompt)
- Per-session folders so multiple playthroughs stay isolated
- Automatic cleanup that keeps the backup count under a configurable limit
- Rotating log file (auto-trims to ~6 MB)
- Stale temporary folder cleanup on startup
- Single-file Python script with no third-party dependencies

## Requirements

- Windows
- Python 3.10 or newer
- A copy of *Detroit: Become Human*

## Setup

1. Make sure `savemanager.py`, this README, and `LICENSE` are in the same folder.
2. Copy `config.example.ini` to `config.ini` (a default `config.ini` is also created automatically on first run if you skip this step).
3. Open `config.ini` in Notepad and set the paths under `[Settings]`:
   - `GameExecutablePath` — full path to `DetroitBecomeHuman.exe`
   - `SourceSavePath` — the folder where the game stores your live saves (defaults to the standard Quantic Dream location)
   - `BackupStoragePath` — the folder where timestamped backups will be stored
4. Run the script:

   ```
   python savemanager.py
   ```

## How to use

1. **Always launch the game through this script.** Don't run the game's `.exe` directly — the script needs to know when the game is running to make backups.
2. When the script starts, you'll see a menu of available backups.
3. Choose an option:
   - `0` — Launch the game with the current save (your normal choice, 99% of the time)
   - `1`, `2`, `3`, ... — Restore a specific backup. The script will ask you to type `yes` to confirm.
   - `Q` — Quit without launching
4. The script launches the game and monitors it in the background. Every N minutes, a new backup is created. When you exit the game, one final backup is made and the script closes itself.

## How it works

- **Backup.** On each tick, the script copies the entire live save folder to a new timestamped sub-folder under your backup location, named `$[YYYY-MM-DD HH.MM.SS]`. The copy is staged to a `.tmp` folder and atomically renamed, so a backup that gets interrupted mid-write never produces a half-copied snapshot.
- **Restore.** The script performs a safe swap: it backs up your current save as a safety net, then replaces the live save with the contents of the chosen backup. If anything goes wrong during the swap, the original save is automatically moved back into place.
- **Cleanup.** When the number of backups exceeds `MaxAutoSaves`, the oldest ones are deleted automatically. Set to `0` to disable cleanup.
- **Log.** All activity is logged to `save_manager.log` (rotates at 2 MB, keeps 3 backups).

## Configuration

All options live in `config.ini` under `[Settings]`. See `config.example.ini` for the full list with descriptions.

| Key | Default | Description |
|---|---|---|
| `GameExecutablePath` | *(empty)* | Full path to `DetroitBecomeHuman.exe`. Required. |
| `SourceSavePath` | `%USERPROFILE%\Saved Games\Quantic Dream\Detroit Become Human` | Folder containing the live game saves. |
| `BackupStoragePath` | `%USERPROFILE%\DetroitSaveBackups` | Root folder for all backups. A sub-folder named after `SessionName` is created inside. |
| `SessionName` | `default` | Sub-folder name for a playthrough. Change this to keep multiple playthroughs separate. |
| `SaveFrequencyMinutes` | `5` | Minutes between automatic backups while the game is running. |
| `MaxAutoSaves` | `50` | Maximum backups to keep. `0` = unlimited. |
| `LaunchBackup` | `yes` | Create an extra backup right before launching the game. |

## Safety notes

- The script always backs up your current save before restoring one, so restoring is reversible.
- Backups live in a different folder from your live saves. Deleting your live save folder does not affect your backups.
- The script only writes inside the configured `BackupStoragePath`. It validates that any restore target lives inside that path before doing anything.

## Limitations

- Windows only. Uses `tasklist` for process detection and Windows path conventions.
- Backups are full copies of the save folder. With a 5-minute interval and a 50-backup limit, expect 50× the disk space of one save. Lower `MaxAutoSaves` or raise `SaveFrequencyMinutes` to reduce usage.

## Troubleshooting

- **"Game executable was not found"** — The path in `GameExecutablePath` is wrong. Open `config.ini` and verify it points to the actual `.exe`.
- **"Source save directory not found"** — The path in `SourceSavePath` is wrong, or the game has not yet created a save file. Check the actual save location in Windows Explorer.
- **No backups are being created** — Make sure you're launching the game *through this script*, not by double-clicking the `.exe`. The script needs to detect the running game process.
- **Backups fill the disk** — Lower `MaxAutoSaves` or raise `SaveFrequencyMinutes` in `config.ini`.
- **Restore didn't work** — Check `save_manager.log` for the error. The script writes a "PRE-RESTORE CURRENT" safety backup before each restore, so nothing should be lost.

## License

MIT — see `LICENSE`.

## Development

The project ships with a unit test suite that covers the pure helpers, configuration handling, and the data-loss-critical restore and backup paths. No third-party dependencies are needed.

Run the tests:

```
python -m unittest tests.test_savemanager -v
```

To add a test, drop it in `tests/test_savemanager.py` next to an existing `Test...` class.
