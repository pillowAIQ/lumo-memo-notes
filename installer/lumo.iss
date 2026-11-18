#ifndef TestMode
#define TestMode 0
#endif
[Setup]
AppId={#AppId}
AppName=Lumo
AppVersion={#AppVersion}
AppVerName=轻笺 · Lumo {#AppVersion}
AppPublisher=pillowAIQ
AppPublisherURL=https://github.com/pillowAIQ/lumo-notes
DefaultDirName={localappdata}\Programs\Lumo
DefaultGroupName=轻笺 Lumo
PrivilegesRequired=lowest
UsePreviousAppDir=yes
UninstallDisplayIcon={app}\Lumo.exe
CloseApplications=no
RestartApplications=no
AlwaysRestart=no
WizardStyle=modern
SetupIconFile={#RepoRoot}\src\memo_app\assets\lumo.ico
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
#if Int(TestMode) == 0
AppMutex=Local\LumoInstallerRunning-v1
#endif
[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
#if Int(LangZh) != 0
Name: "zh"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#endif
[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#if Int(TestMode) == 0
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked
[Icons]
Name: "{group}\轻笺 Lumo"; Filename: "{app}\Lumo.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\轻笺 Lumo"; Filename: "{app}\Lumo.exe"; WorkingDir: "{app}"; Tasks: desktopicon
[Run]
Filename: "{app}\Lumo.exe"; Description: "启动轻笺"; Flags: nowait postinstall skipifsilent
#endif
