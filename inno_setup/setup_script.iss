; Inno Setup Script for MindShield
#define MyAppName "MindShield"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Md. Monirul Hasan & Team"
#define MyAppExeName "MindShield.exe"

[Setup]
AppId={{8A1F3E92-7E3B-4C81-9B54-1B112D95678A}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppComments=Protecting attention from distractions
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=D:\Academic\Section 7\SDP 2\MindShield\installer_output
OutputBaseFilename=MindShield_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\mindshield.ico
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "D:\Academic\Section 7\SDP 2\MindShield\dist\branded\MindShield\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "D:\Academic\Section 7\SDP 2\MindShield\dist\branded\MindShield\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent