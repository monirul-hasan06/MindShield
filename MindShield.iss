#define MyAppName "MindShield"
#define MyAppVersion "1.0.0"
#define MyAppSlogan "Protecting attention from distractions"

[Setup]
AppId={{8A1F3E92-7E3B-4C81-9B54-1B112D95678A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppComments={#MyAppSlogan}
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\MindShield.exe
DefaultDirName={autopf}\MindShield
DefaultGroupName={#MyAppName}
OutputBaseFilename=MindShield-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\mindshield.ico
PrivilegesRequired=admin

[Tasks]
Name: "desktopicon"; Description: "Create a &Desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "dist\branded\MindShield\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\MindShield.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\MindShield.exe"; WorkingDir: "{app}"; Tasks: desktopicon
