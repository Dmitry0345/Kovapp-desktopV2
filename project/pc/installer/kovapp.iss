; Установщик Kovapp для Windows 10 и 11 (64 бита). Inno Setup 6.7.
; Собирается командой build_installer.cmd из папки pc: сначала программа (dist\Kovapp), затем этот сценарий.
; Результат: dist\KovappSetup-<версия>.exe — всё внутри, интернет при установке не нужен.

#ifndef DistDir
  #define DistDir AddBackslash(SourcePath) + "..\dist\Kovapp"
#endif
#ifndef OutDir
  #define OutDir AddBackslash(SourcePath) + "..\dist"
#endif
#define AppExe "Kovapp.exe"
#if !FileExists(AddBackslash(DistDir) + AppExe)
  #error Нет dist\Kovapp\Kovapp.exe: сначала соберите программу (build.cmd)
#endif
; числовая версия — из самого Kovapp.exe (tools\version_info.txt → сборка Nuitka)
#define AppVer GetVersionNumbersString(AddBackslash(DistDir) + AppExe)
; текстовая версия (beta3-v12-pc) приходит из build_installer.cmd (/DVerText) — у Nuitka строковой версии в exe нет
#ifdef VerText
  #define AppVerText VerText
#else
  #define AppVerText GetStringFileInfo(AddBackslash(DistDir) + AppExe, "ProductVersion")
#endif
#if AppVerText == ""
  #define AppVerText AppVer
#endif
; постоянный код программы: по нему установщик находит прежнюю версию (менять нельзя)
#define AppGuid "{D705E653-030C-4120-A771-0010FE29AB4C}"
#define Img AddBackslash(SourcePath) + "images\"

[Setup]
AppId={#StringChange(AppGuid, "{", "{{")}
AppName=Kovapp
AppVersion={#AppVer}
AppVerName=Kovapp {#AppVerText}
AppPublisher=Kovapp
AppComments=Задачи, проекты, склад, оборудование и мессенджер
UninstallDisplayName=Kovapp
UninstallDisplayIcon={app}\{#AppExe}
VersionInfoVersion={#AppVer}
VersionInfoCompany=Kovapp
VersionInfoDescription=Установка Kovapp
VersionInfoProductName=Kovapp
VersionInfoProductTextVersion={#AppVerText}
; Windows 10/11 x64, сразу в Program Files, с правами администратора
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
DefaultDirName={autopf}\Kovapp
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableReadyPage=yes
ShowLanguageDialog=no
; открытый Kovapp закрывается перед обновлением (см. также [Code])
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
SetupLogging=yes
OutputDir={#OutDir}
OutputBaseFilename=KovappSetup-{#AppVerText}
SetupIconFile=..\images\installer_icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
; оформление: как экран входа Kovapp; светлое или тёмное — по теме Windows
WizardStyle=modern dynamic windows11 hidebevels
WizardBackImageFile={#Img}back_596.png,{#Img}back_796.png,{#Img}back_994.png,{#Img}back_1193.png,{#Img}back_1272.png,{#Img}back_1471.png,{#Img}back_1630.png
WizardBackImageFileDynamicDark={#Img}back_dark_596.png,{#Img}back_dark_796.png,{#Img}back_dark_994.png,{#Img}back_dark_1193.png,{#Img}back_dark_1272.png,{#Img}back_dark_1471.png,{#Img}back_dark_1630.png
WizardImageFile={#Img}wizard_202.png,{#Img}wizard_269.png,{#Img}wizard_336.png,{#Img}wizard_403.png,{#Img}wizard_430.png,{#Img}wizard_498.png,{#Img}wizard_534.png
WizardImageFileDynamicDark={#Img}wizard_dark_202.png,{#Img}wizard_dark_269.png,{#Img}wizard_dark_336.png,{#Img}wizard_dark_403.png,{#Img}wizard_dark_430.png,{#Img}wizard_dark_498.png,{#Img}wizard_dark_534.png
WizardSmallImageFile={#Img}small_58.png,{#Img}small_77.png,{#Img}small_97.png,{#Img}small_116.png,{#Img}small_124.png,{#Img}small_143.png,{#Img}small_159.png
WizardSmallImageFileDynamicDark={#Img}small_dark_58.png,{#Img}small_dark_77.png,{#Img}small_dark_97.png,{#Img}small_dark_116.png,{#Img}small_dark_124.png,{#Img}small_dark_143.png,{#Img}small_dark_159.png
#ifdef Sign
SignTool=kovsign
SignedUninstaller=yes
#endif

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Messages]
WelcomeLabel1=Установка Kovapp
WelcomeLabel2=[name/ver]: задачи, проекты, склад, оборудование и мессенджер в одном окне.%n%nПрограмма будет установлена в папку «Program Files». Всё нужное уже внутри установщика — интернет не понадобится.
WizardSelectTasks=Значки для запуска
SelectTasksDesc=Где разместить значки Kovapp?
SelectTasksLabel2=Отметьте нужное и нажмите «Установить».
InstallingLabel=Подождите, Kovapp устанавливается на компьютер.
FinishedHeadingLabel=Kovapp установлен
FinishedLabelNoIcons=Программа готова к работе.
FinishedLabel=Программа готова к работе. Открыть её можно значком Kovapp.
ClickFinish=Нажмите «Завершить», чтобы закрыть окно установки.
UninstallAppTitle=Удаление
UninstallAppFullTitle=Удаление — %1
WizardUninstalling=Удаление
ConfirmUninstall=Удалить %1 с этого компьютера?%n%nНастройки (адрес сервера, тема, масштаб) останутся и пригодятся при повторной установке.
UninstallStatusLabel=Подождите, %1 удаляется с компьютера.
UninstalledAll=%1 удалён с компьютера.
UninstalledMost=%1 удалён.%n%nНекоторые файлы удалить не удалось — их можно удалить вручную.

[CustomMessages]
ru.GroupIcons=Создать значки Kovapp:
ru.TaskStartMenu=В меню «Пуск»
ru.TaskDesktop=На рабочем столе
ru.RunApp=Запустить Kovapp
ru.AppRunning=Kovapp сейчас открыт. Чтобы продолжить, его нужно закрыть.%n%nЗакрыть Kovapp сейчас?
ru.AppStillRunning=Kovapp не закрыт. Закройте программу и запустите установку снова.
ru.NewerInstalled=На компьютере уже стоит более новая версия Kovapp (%1).%n%nВсё равно установить версию %2?
ru.NoMediaFeatures=В этой Windows нет компонентов мультимедиа (выпуск N).%n%nKovapp установится и будет работать, но видео внутри программы не откроется. Чтобы это исправить, добавьте «Пакет компонентов мультимедиа» (Media Feature Pack): «Параметры» → «Дополнительные компоненты», затем перезагрузите компьютер.

[Tasks]
Name: "startmenu"; Description: "{cm:TaskStartMenu}"; GroupDescription: "{cm:GroupIcons}"
Name: "desktopicon"; Description: "{cm:TaskDesktop}"; GroupDescription: "{cm:GroupIcons}"

[InstallDelete]
; при обновлении старая версия убирается целиком (файлы Nuitka лежат прямо в папке программы, без _internal;
; настройки хранятся в %LOCALAPPDATA%\KovApp и не затрагиваются)
Type: filesandordirs; Name: "{app}\*"
; значок, с которого сняли отметку при обновлении, убирается
Type: files; Name: "{autoprograms}\Kovapp.lnk"; Tasks: not startmenu
Type: files; Name: "{autodesktop}\Kovapp.lnk"; Tasks: not desktopicon

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Kovapp"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Comment: "Kovapp {#AppVerText}"; Tasks: startmenu
Name: "{autodesktop}\Kovapp"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Comment: "Kovapp {#AppVerText}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:RunApp}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Code]
const
  AppExeName = '{#AppExe}';

// Процессы Kovapp.exe из папки программы (если путь не прочитать — тоже считаем своими)
function AppPids(): String;
var
  Locator, Service, Items, Item: Variant;
  I: Integer;
  Path, Mine: String;
begin
  Result := '';
  Mine := Lowercase(ExpandConstant('{app}\') + AppExeName);
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\CIMV2');
    Items := Service.ExecQuery('SELECT ProcessId, ExecutablePath FROM Win32_Process WHERE Name = ''' + AppExeName + '''');
    for I := 0 to Items.Count - 1 do
    begin
      Item := Items.ItemIndex(I);
      Path := '';
      try
        if not VarIsNull(Item.ExecutablePath) then
          Path := Item.ExecutablePath;
      except
        Path := '';
      end;
      if (Path = '') or (Lowercase(Path) = Mine) then
        Result := Result + ' /PID ' + IntToStr(Item.ProcessId);
    end;
  except
    Log('Проверка запущенного Kovapp не удалась: ' + GetExceptionMessage);
    Result := '';
  end;
  if Result <> '' then
    Log('Kovapp запущен:' + Result);
end;

// Закрыть Kovapp: сначала как кнопкой «закрыть», через 6 с — принудительно
function CloseApp(): Boolean;
var
  Pids: String;
  Code, Tries: Integer;
begin
  Pids := AppPids();
  if Pids = '' then
  begin
    Result := True;
    Exit;
  end;
  Exec(ExpandConstant('{sys}\taskkill.exe'), Pids, '', SW_HIDE, ewWaitUntilTerminated, Code);
  Tries := 0;
  while (AppPids() <> '') and (Tries < 12) do
  begin
    Sleep(500);
    Tries := Tries + 1;
  end;
  Pids := AppPids();
  if Pids <> '' then
  begin
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T' + Pids, '', SW_HIDE, ewWaitUntilTerminated, Code);
    Sleep(1000);
  end;
  Result := AppPids() = '';
end;

function AskCloseApp(): Boolean;
begin
  Result := True;
  if AppPids() = '' then
    Exit;
  if SuppressibleMsgBox(CustomMessage('AppRunning'), mbConfirmation, MB_YESNO, IDYES) = IDYES then
    Result := CloseApp()
  else
    Result := False;
end;

function InitializeSetup(): Boolean;
var
  Installed: String;
  Old, New: Int64;
begin
  Result := True;
  if RegQueryStringValue(HKLM64, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppGuid}_is1', 'DisplayVersion', Installed)
     and StrToVersion(Installed, Old) and StrToVersion('{#AppVer}', New) and (ComparePackedVersion(Old, New) > 0) then
    Result := SuppressibleMsgBox(FmtMessage(CustomMessage('NewerInstalled'), [Installed, '{#AppVer}']), mbConfirmation, MB_YESNO, IDNO) = IDYES;
  if Result and not FileExists(ExpandConstant('{sys}\mfplat.dll')) then
    SuppressibleMsgBox(CustomMessage('NoMediaFeatures'), mbInformation, MB_OK, IDOK);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not AskCloseApp() then
    Result := CustomMessage('AppStillRunning');
end;

function InitializeUninstall(): Boolean;
begin
  Result := AskCloseApp();
  if not Result then
    SuppressibleMsgBox(CustomMessage('AppStillRunning'), mbError, MB_OK, IDOK);
end;

// без страницы «Всё готово»: на странице значков кнопка «Установить»
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpSelectTasks then
    WizardForm.NextButton.Caption := SetupMessage(msgButtonInstall)
  else if CurPageID = wpFinished then
    WizardForm.NextButton.Caption := SetupMessage(msgButtonFinish)
  else
    WizardForm.NextButton.Caption := SetupMessage(msgButtonNext);
end;
