; ARGOS Universal OS — Inno Setup Script
#define MyAppName "ARGOS Universal OS"
#define MyAppVersion "2.1.4"
#define MyAppPublisher "ARGOS"
#define MyAppExeName "ARGOS.exe"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\ARGOS
DefaultGroupName=ARGOS
OutputDir=installer\Output
OutputBaseFilename=ARGOS_Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

[Files]
Source: "dist\ARGOS.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ARGOS"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall ARGOS"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch ARGOS"; Flags: nowait postinstall skipifsilent
