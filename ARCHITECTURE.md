# MindShield Architecture and Implementation Status

## Project overview

MindShield is a Windows desktop focus-tracking application. It provides timed
focus sessions, foreground-window distraction monitoring, scheduled eye-care
break reminders, a system-tray menu, focus analytics, and a Windows installer.

## Technology

- Python 3.14
- CustomTkinter for the desktop interface
- Python's standard-library `sqlite3` for local database storage
- `pywinctl`, `psutil`, `pywinauto`, and `comtypes` for foreground-window,
  process, and accessibility information
- `pystray` and Pillow for system-tray integration and image assets
- PyInstaller for the standalone application bundle
- Inno Setup scripts for Windows installation

## Source layout

```text
MindShield/
├── assets/
│   ├── Demo/                         # Original supplied logo screenshot
│   ├── mindshield-logo.png           # Transparent logo-and-name wordmark
│   └── mindshield.ico                # Square mark for Windows icon surfaces
├── src/
│   ├── core/
│   │   ├── app_icon.py               # Shared logo and icon asset helpers
│   │   ├── timer_manager.py          # Threaded focus and break timer state
│   │   ├── tray_manager.py           # Tray menu and window lifecycle
│   │   └── window_tracker.py         # Active-window monitoring
│   ├── database/
│   │   └── db_handler.py             # SQLite storage and analytics queries
│   └── gui/
│       ├── main_window.py            # Dashboard, analytics, and settings
│       └── overlay_window.py         # Scheduled eye-care reminder
├── tests/
├── main.py
├── MindShield.spec
└── inno_setup/
    └── setup_script.iss
```

## Runtime responsibilities

### Application startup

`main.py` creates and initializes the database handler, seeds sample history
only when no focus logs exist, then creates the timer manager, window tracker,
main window, and tray manager before entering the GUI event loop. Closing the
main window hides it to the system tray; choosing Exit stops background
workers and closes the application.

The timer and window-tracking worker threads are started in response to focus
or break actions. They are not both started automatically on application
launch.

### Persistence

`DatabaseHandler` stores development data in the project-root
`mindshield.db`; frozen Windows builds use `%LOCALAPPDATA%\MindShield` so the
installer does not need write access to Program Files. It creates three
tables:

- `work_logs`: completed focus sessions, including date, start time, duration,
  and work zone
- `settings`: saved preferences as key-value pairs
- `break_logs`: scheduled-break start, duration, work zone, and completion

New database files use standard SQLite and are not encrypted. When a legacy
SQLCipher database and its DPAPI-protected key are present, startup migrates
its work logs, settings, and break logs to standard SQLite and preserves the
encrypted original as `mindshield.db.sqlcipher.bak`. If the key or migration
dependency is unavailable, startup stops with an explicit error without
replacing the original.

`seed_demo_data()` runs during startup and inserts seven days of sample focus
history only if the work-log table is empty. It does not duplicate sample
history or add it to a database that already has focus logs.

### Timer and break behavior

`TimerManager` uses a worker thread and monotonic deadlines. Defaults are a
20-minute work interval and a 20-second scheduled break. Demo Mode changes
those intervals to 10 seconds and 5 seconds. The UI also offers 5-, 10-, and
15-minute manual breaks; meeting mode supports saved durations of 30 minutes,
1 hour, or 2 hours.

### Window tracking

`WindowTracker` polls the foreground window every two seconds while focus
tracking is active. It matches the configured work zone against the window
title and process information, and can inspect accessible controls for URLs
and absolute paths. Distraction reporting is supported; minimizing distracting
windows is an optional setting.

### Tray and interface

`TrayManager` provides Open MindShield, Pause / Meeting Mode using the saved
duration, and Exit actions.

`MainWindow` opens to a light, Windows-style workspace with a persistent
sidebar for Overview, Profile & Analytics, and Settings. The Overview page
puts the focus timer and work-zone controls first; the other pages retain
scrollable layouts for add/edit/delete controls on focus and scheduled-break
history, a configurable/resettable daily goal, confirmed analytics reset,
timer preferences, and timed manual breaks.
Sidebar destinations display distinct home, analytics, and settings icons.
The sidebar's Light/Dark appearance selector applies a coordinated palette to
the full workspace and saves the selected mode as a user preference. Light
mode uses a crisp near-white navigation rail, soft cool-gray workspace, and
white cards; Dark mode uses a deep slate canvas, a subtly distinct sidebar,
raised charcoal cards, and tuned text/accent contrast.
Theme changes blend the complete workspace palette, and settings switches use
an eased sliding-knob animation for clear active/inactive feedback.
Primary focus, break, and work-zone actions use rounded button styling with
animated hover and press feedback. Sidebar icons ease in size on hover and
selection, and focus, goal, and weekly progress bars animate toward updated
values. While a focus session runs, a small LIVE spinner indicates the active
background window tracker; it is hidden when tracking stops.
The Overview focus card has a prominent MindShield active/inactive switch.
Its state is saved as an application preference; switching it off resets the
focus timer, stops foreground-window monitoring, closes any break reminder,
and disables focus, break, work-zone, and tracking controls. Analytics and
navigation remain available, and switching back on does not start tracking
until the user explicitly starts work.
Starting work keeps the MindShield window above other windows and disables
navigation away from the current page. Pausing work or starting a manual break
removes the topmost setting and unlocks the sidebar; a timed manual break
continues independently until it resumes the saved work countdown.
`OverlayWindow` displays the 20-20-20 eye-care reminder as a topmost,
semi-transparent full-screen overlay or corner toast. Its countdown uses
`time.monotonic()`. Segoe UI remains the body/interface font; bundled Orbitron
is registered privately for the focus timer, overlay countdown, page title,
and key analytics values. The Orbitron face and its SIL Open Font License are
included with the application bundle. If private font registration is
unavailable, display text falls back to Segoe UI.

The main window and overlay use the supplied wordmark. The wordmark adapts for
dark backgrounds while retaining MindShield's original blue mark; the tray,
executable, and installer use its matching square mark. Interface typography
uses the native Segoe UI family for the Windows utility style. The focus
overview includes a session progress animation and a restrained pulsing status
badge while a work interval is running, plus a short eased fade-in when the
application opens.

## Run and package

From the project root in Windows PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe main.py
.\venv\Scripts\python.exe -m unittest discover -s tests -v
.\venv\Scripts\pyinstaller.exe --clean --noconfirm --distpath dist\branded MindShield.spec
```

The PyInstaller command creates `dist\branded\MindShield`, the input expected
by the root `MindShield.iss` Inno Setup script. Compile the installer from the
project root with Inno Setup's `ISCC.exe MindShield.iss`.

## Status against the supplied blueprint

The 18 implementation statements in sections A-F were assessed against the
source:

- **17 complete**
- **1 partial / wording mismatch:** the blueprint says both worker threads
  launch at application startup; in practice, they start when a focus or break
  action needs them.
- Demo sessions are seeded at startup only when no focus logs exist.
- **0 missing major modules**

The application behavior above is the current source of truth. Worker threads
start when a focus or break action needs them rather than idling at startup.
