; packaging/installer/ultrarag.iss — Inno Setup 脚本（产物为 URStaff）
; 由 build_windows.ps1 调用：ISCC.exe packaging\installer\ultrarag.iss
; VERSION 通过环境变量传入（GetEnv）

[Setup]
AppName=URStaff
AppVersion={#GetEnv('VERSION')}
DefaultDirName={autopf}\URStaff
DefaultGroupName=URStaff
OutputDir=..\out
OutputBaseFilename=URStaff-setup
SetupIconFile=..\assets\staffdeck.ico
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=lowest

[Files]
; PyInstaller onedir 产物整体安装
Source: "..\out\staffdeck\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\URStaff"; Filename: "{app}\staffdeck.exe"
Name: "{commondesktop}\URStaff"; Filename: "{app}\staffdeck.exe"

[Run]
Filename: "{app}\staffdeck.exe"; Description: "启动 URStaff"; Flags: postinstall nowait skipifsilent
