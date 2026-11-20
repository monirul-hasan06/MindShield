# MindShield

MindShield is a Windows desktop focus app. It provides customizable focus timers, timed breaks, an optional reversible app focus guard, named work-zone profiles, distraction minimization, a system tray, searchable session history with CSV export, local productivity analytics, and encrypted local settings and activity logs.

## Requirements and development

- Windows 10 or later
- Python 3.14 (the version used to validate this project)
- Inno Setup 6 for building the installer

From the repository root, create and activate a virtual environment, install dependencies, and run the app:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

Run the test suite with:

```powershell
python -m unittest discover -s tests -v
```

## Privacy and stored data

Window monitoring runs only during a work session. MindShield reads only the active window details needed by enabled work-zone rules; matching a browser URL or an Explorer folder can also require reading accessible URL/path fields. Rules are enabled individually and use OR matching: any enabled rule can match. Optional suppression minimizes windows that do not match. The focus guard can keep the selected executable in front and minimize windows matching configured blocked rules. Window titles, URLs, and process details are used for matching and are not written to the activity database.

Settings, including your profile name, work-zone profiles, focus logs, and scheduled-break logs are stored locally in a SQLCipher-encrypted database. The database key is protected with Windows DPAPI for the current Windows user. No account, cloud sync, or telemetry is used. A new database starts without demo activity. Existing records are retained.

Create and restore backups from **Settings → Privacy & local data**. A backup contains the encrypted database and its DPAPI-protected key, not a plaintext key. It can be restored only by the same Windows user account that created it. Restoring replaces the current activity history and settings; export the current data first if you may need it. MindShield closes after a successful restore so the restored settings are loaded on the next launch.

For a packaged install, user data is kept outside the application folder at `%LOCALAPPDATA%\MindShield`. Uninstalling the app does not remove that user data.
When running from source, the database is stored as `mindshield.db` in the repository root (the file is git-ignored); the DPAPI-protected key remains under the current user's LocalAppData.

## Build and install

Build the one-folder application from the repository root. Install the build tools first; they are not required to run the app from source:

```powershell
python -m pip install -r requirements-build.txt
pyinstaller MindShield.spec
```

The output is `dist\MindShield`. Compile `MindShield.iss` with Inno Setup 6 to create `installer_output\MindShield_Setup.exe`. The installer keeps the existing machine-wide install location and requires administrator privileges to preserve the established upgrade path. Application data remains per-user under LocalAppData. `inno_setup\setup_script.iss` includes the root installer script for compatibility with existing build instructions.

## Current behavior and limitations

- Work-zone profiles can match window titles, process names, executable paths, browser URLs, and Explorer folder paths where Windows exposes them through accessibility controls. Each rule is either a substring (**Contains**) or a whole word/path segment (**Whole word**). Existing rules stay substring matches.
- Dashboard, Profile & Analytics, Settings, onboarding, and nested rule lists support eased vertical scrolling with the mouse wheel/touchpad, click-and-drag, or Page Up/Page Down/Home/End keys.
- Distraction suppression is optional and disabled by default.
- Set the focus and automatic eye-care interval from 1 to 240 minutes on the Dashboard or in Settings. The eye-care break length is separately adjustable in seconds. Starting work with a custom Dashboard interval saves it as the next default.
- The optional focus guard uses a selected `.exe` as the work app. It minimizes other applications and can also minimize matching blocked windows within the selected app. It is reversible: pausing or stopping releases the guard, and breaks temporarily suspend it. When a break ends, MindShield activates or relaunches the selected app.
- The focus guard is best-effort, not an operating-system lock. It checks the active window, cannot control secure Windows shortcuts or reliably inspect background browser tabs, and may minimize the whole browser when its active tab matches a blocked rule. URL checks depend on what the browser exposes through Windows accessibility controls.
- Start manual 5-, 10-, or 15-minute breaks from the Dashboard. Pause and resume scheduled or manual breaks without losing the remaining break time, or return to work early. An early-ended break remains incomplete in break history; manual breaks resume the saved focus time, while scheduled breaks start a new focus interval.
- Demo mode shortens work and break intervals for demonstrations; it is not enabled by default.
- Choose Light, Dark, or System appearance from either the Dashboard or Settings, and Small, Default, or Large text from Settings; Dark remains the default. The dashboard and settings theme controls stay synchronized.
- Enable **Reduce motion** in Settings to use immediate scrolling and timer progress updates and to disable onboarding and break-overlay fades. It is off by default.
- Press **Ctrl+Shift+Space** to pause or resume active work and breaks, including while the window is hidden in the system tray. The shortcut is ignored while a MindShield text field is focused. A second launch shows the window that is already running.
- First-run onboarding asks for a name used in the Dashboard's time-of-day greeting. Its feature walkthrough can be skipped and replayed from Settings; you can update your name there as well. Break reminders can show the eye-care prompt, a stretch prompt, a hydration prompt, or no prompt.
- Session history can be filtered to the last 7 days, last 30 days, or all history, and searched by work-zone name. CSV export uses the same filters and includes only date, start time, duration in seconds, and work-zone name. Dashboard totals use those recorded seconds, and each saved segment keeps the time work actually started.
- The productivity profile totals recorded work-segment time and completed manual or scheduled breaks. Paused time is excluded; active work is committed when paused, stopped, transitioned to a break, or explicitly exiting. Closing the window to the system tray keeps the session running; a crash or forced shutdown can still leave the current segment unrecorded. An invalid saved setting falls back to its default instead of blocking startup. Diagnostic logs are written to `%LOCALAPPDATA%\MindShield\mindshield.log`.
- Use **Profile & Analytics → Reset Analytics** to clear saved focus totals, break history, and session history while keeping settings and work-zone profiles. MindShield asks for confirmation; active work restarts its tracked duration from zero, and an in-progress break remains tracked.
- MindShield currently targets Windows; it does not provide cloud sync or cross-device data transfer.

## Potential future improvements

1. Add named focus presets for different routines.
2. Expand break analytics to distinguish completed breaks from breaks ended early.
3. Consider localization after the user-facing workflows are stable.
