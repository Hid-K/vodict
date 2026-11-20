; Inno Setup: инсталлятор Vodict (per-user, без прав администратора).
; Обновление: новый setup запускается поверх старой установки — файлы
; перезаписываются (ignoreversion), ярлыки сохраняются. Конфигурация,
; история диктовок и модели лежат ВНЕ папки установки и не трогаются.
;
; Версия передаётся из CI: ISCC /DMyAppVersion=v1.2.3 installer.iss
#ifndef MyAppVersion
#define MyAppVersion "0.0.0"
#endif

#define MyAppName "Vodict"
#define MyAppExeName "Vodict.exe"
; Стабильный AppId: по нему Inno распознаёт прежнюю установку при обновлении
#define MyAppId "{{8A2C5D4E-1F3B-4A67-9E05-2C7B8D4F1A93}}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
; per-user установка: %LOCALAPPDATA%\Programs\Vodict, без UAC
DefaultDirName={localappdata}\Programs\Vodict
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=Vodict-setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Запускать при входе в Windows"; \
    GroupDescription: "Автозапуск:"; Flags: unchecked

[Files]
Source: "..\dist\Vodict\*"; DestDir: "{app}"; \
    Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; \
    Description: "{cm:LaunchProgram,{#MyAppName}}"; \
    Flags: nowait postinstall skipifsilent

[Uninstall]
; Пользовательские данные (конфиг, история, модели) сознательно
; не удаляются: %LOCALAPPDATA%\vodict и ~/Vodict
