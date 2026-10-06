; MV 播放器 Windows 安装程序(Inno Setup 6)
; 由 packaging\build_windows.ps1 调用:iscc /DAppVersion=… /DSourceDir=… /DOutputDir=… /DIconFile=…

#define AppName "MV 播放器"
#define AppExe "MVPlayer.exe"

[Setup]
AppId={{6F1C2A4E-8B7D-4C35-9E0A-2D5B7C9E4F18}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=leozhang8654
AppPublisherURL=https://github.com/leozhang8654/mv-player
AppSupportURL=https://github.com/leozhang8654/mv-player/issues
AppUpdatesURL=https://github.com/leozhang8654/mv-player/releases
; 装在用户目录,不需要管理员权限
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\MVPlayer
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=MVPlayer-Windows-x64-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "chs"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只删程序自己的文件;曲库和视频(%USERPROFILE%\Videos\MV播放器)保留
Type: filesandordirs; Name: "{app}\_internal"
