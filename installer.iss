; Nan Sentinel（南哨）— Inno Setup 安装脚本
; 用 Inno Setup 6 打开此文件，点击 Compile 即可生成安装程序

#define MyAppName "Nan Sentinel（南哨）"
#define MyAppVersion "0.4.0"
#define MyAppPublisher "Lyrics-nju"
#define MyAppExeName "AI_Console_Launcher.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\AIConsole
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=installer_output
OutputBaseFilename=AIConsole_Setup_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; 主程序文件
Source: "dist_output_exe\AI_Console_EXE\AI_Console_Launcher.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist_output_exe\AI_Console_EXE\api_server.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist_output_exe\AI_Console_EXE\scraper_agent.exe"; DestDir: "{app}"; Flags: ignoreversion
; 配置和前端
Source: "dist_output_exe\AI_Console_EXE\config.yaml"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "dist_output_exe\AI_Console_EXE\dist\*"; DestDir: "{app}\dist"; Flags: ignoreversion recursesubdirs createallsubdirs
; NapCat
Source: "dist_output_exe\AI_Console_EXE\NapCat_Portable\*"; DestDir: "{app}\NapCat_Portable"; Flags: ignoreversion recursesubdirs createallsubdirs
; 说明文件
Source: "dist_output_exe\AI_Console_EXE\使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Dirs]
; 用户数据目录（%APPDATA%/AIConsole）— 卸载时保留
Name: "{userappdata}\AIConsole"; Flags: uninsneveruninstall

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// 卸载时提示是否保留用户数据
function InitializeUninstall(): Boolean;
begin
  Result := MsgBox('是否保留用户数据（消息记录、收藏、配置）？' + #13#10 + #13#10 +
    '选择"是"保留数据，下次安装时自动恢复。' + #13#10 +
    '选择"否"彻底删除所有数据。',
    mbConfirmation, MB_YESNO) = IDYES;
end;
