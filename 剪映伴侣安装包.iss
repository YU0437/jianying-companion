; Inno Setup 脚本：剪映伴侣安装包
; 编译：ISCC.exe 剪映伴侣安装包.iss

[Setup]
AppName=剪映伴侣
; ★ 显式 AppId：升级/重装时 Inno 靠它认"这是同一个程序"。
;   不写的话默认用 AppName，中文名一旦改了就会变成"两个程序"。
AppId={{8F3A6C21-4B7D-4E5A-9C1E-2D5B7A90431F}
AppVersion=1.3.0
VersionInfoVersion=1.3.0.0
VersionInfoDescription=剪映伴侣 - 预合成导出辅助
AppPublisher=JianyingCompanion
; ★ 默认装到用户目录（不是 Program Files）—— 理由见下面 [Code] 里的说明：
;   伴侣的**配置和运行日志就写在程序目录里**，装在需要管理员权限的地方
;   会导致"每次启动都静默保存失败"。
DefaultDirName={localappdata}\JianyingCompanion
DefaultGroupName=剪映伴侣
UninstallDisplayIcon={app}\剪映伴侣.exe
OutputBaseFilename=剪映伴侣-Setup-1.3.0
OutputDir=installer
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
SetupIconFile=app.ico

[Languages]
Name: "chinese"; MessagesFile: "ChineseSimplified.isl"

[Files]
Source: "dist\JianyingCompanion.exe"; DestDir: "{app}"; DestName: "剪映伴侣.exe"; Flags: ignoreversion
; ★ 给别人用的东西必须带一份"先读我"（对方不看聊天记录，只看安装包）
Source: "使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
; ★★ 这里**故意不预置** 伴侣配置.json（第九批定的规矩）：
;   `load_config()` = 内置默认值 + 配置文件覆盖。安装时若把当时的默认值写死成文件，
;   以后改默认值就**永远到不了老用户**（他们那份文件一直覆盖着）。空着 = 永远跟最新默认。
; ★ 但这不等于"让用户自己去配"（第十六批更正）：装完会由**伴侣自己**跑一次
;   `剪映伴侣.exe --autosetup`，把**这台机器上测出来的**两项写进配置 ——
;   剪映装在哪、草稿放在哪（草稿位置是**读剪映自己的设置**拿到的，用户改过也认得出）。
;   那是代码猜不到的"机器事实"，不写才会逼用户去点菜单。
;   注意它只合并这几个键（见 jy_core.AUTOSETUP_KEYS），**不会**把默认值抄成文件。

[Icons]
Name: "{group}\剪映伴侣"; Filename: "{app}\剪映伴侣.exe"
Name: "{group}\使用说明"; Filename: "{app}\使用说明.txt"
Name: "{group}\卸载剪映伴侣"; Filename: "{uninstallexe}"
Name: "{autodesktop}\剪映伴侣"; Filename: "{app}\剪映伴侣.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加："

[Run]
Filename: "{app}\剪映伴侣.exe"; Description: "立即启动剪映伴侣"; Flags: postinstall nowait skipifsilent
; ★ 第十三批：先把"说明书"摆到手上。别人拿到安装包时脑子里没有上下文，
;   说明书就在同一个目录里 —— 给他一个勾选框，比让他去开始菜单里翻强。
Filename: "{app}\使用说明.txt"; Description: "打开使用说明（建议先看一眼）"; Flags: postinstall shellexec skipifsilent unchecked

[UninstallDelete]
; ★★★ 绝对不要写成 `filesandordirs; Name: "{app}"`（2026-09-18 第九批修正）。
;   两个原因：
;     ① 用户的 伴侣配置.json 就存在 {app} 里，里面记着**草稿备份路径**和
;        **剪映快捷键备份路径** —— 删了它，「还原草稿」「还原快捷键」双双失效，
;        而用户根本不知道发生了什么；
;     ② Inno 在**升级**（装新版本）时会**先跑旧版的卸载程序**，所以那条
;        filesandordirs 等于"每次升级都清空用户配置"。
;   只删我们自己的垃圾文件；配置留着（想彻底清干净，用户自己删文件夹）。
Type: files; Name: "{app}\运行日志.txt"
Type: files; Name: "{app}\运行日志.txt.1"
; ★ 自动配置的结果文件（第十六批）：装完提示完就删了，这里再兜一次
Type: files; Name: "{app}\_自动配置结果.txt"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: dirifempty; Name: "{app}"

[Code]
{ ============================================================ 安装时的「环境设置」
  ★ 2026-09-19 第十三批。用户原话：
    「记得是以安装包的形式发给用户，安装的时候要把环境给用户设置好」。
  ★★ 2026-09-19 第十六批（本段据此重写）。用户原话：
    「我觉得最好是安装时自动帮用户搞的很好，不让用户多余操作」。

  现实是：这份东西是**发给别人的**，对方机器上什么情况都可能。
    · 伴侣 **不含剪映**，也 **不需要 Python / tkinter**（PyInstaller 已经打包进去了）；
    · 但**没有剪映就一点活也干不了** —— 他要是没装，点一百次「一键导出」也是白点；
    · 剪映还可能装在**自定义目录**（不在默认位置），这时伴侣第一次跑要自己找，
      找不到就会报"没检测到剪映"，用户一头雾水。

  所以安装包在装完之后替他做三件事：
    ① **让伴侣自己去认环境**（`剪映伴侣.exe --autosetup`，静默、不开窗）：
       剪映装在哪、草稿放在哪，认到就写进 伴侣配置.json；
       ★ 草稿位置是**读剪映自己的设置**（`currentCustomDraftPath`）拿到的，
         所以用户在剪映里改过草稿位置也照样认得出 —— 这才是"不用多余操作"。
    ② 把认到的结果**念给用户听**（读 `_自动配置结果.txt`，念完删掉）；
    ③ 一个都没认到时，把话说清楚（给一条能照做的提示，**不阻断安装**）。

  ★ 其余的"环境"仍然是**运行时**自动配好的，安装包不要去代劳（会把状态写歪）：
    · 预合成快捷键 → 第一次点「一键导出」时自动绑，并**先备份**用户原设置；
    · 桌面导出目录 → 第一次跑的时候自动建；
    · 环境体检 → 第一次启动会弹一次完整报告。
}

const
  { 目录项属性里的"这是个文件夹"位。Inno Pascal 不保证有 FILE_ATTRIBUTE_DIRECTORY
    这个常量，直接写数值最稳。 }
  ATTR_DIRECTORY = 16;
  { 伴侣 `--autosetup` 写的结果文件（名字要和 jy_core.AUTOSETUP_RESULT 一致）。 }
  AUTOSETUP_RESULT = '_自动配置结果.txt';
  { 目录试写探针名（见 DirWritable） }
  WRITE_PROBE = '_writetest.tmp';

{ 剪映常见的几个落点，和 jy_core.find_jianying_exe() 保持一致（别只查一处）。 }
function HasJianyingUnder(const Root: String): Boolean;
var
  FR: TFindRec;
begin
  Result := False;
  if not DirExists(Root) then
    Exit;
  if FindFirst(Root + '\*', FR) then
  begin
    try
      repeat
        if ((FR.Attributes and ATTR_DIRECTORY) <> 0)
           and (FR.Name <> '.') and (FR.Name <> '..') then
        begin
          if FileExists(Root + '\' + FR.Name + '\JianyingPro.exe') then
          begin
            Result := True;
            Break;
          end;
        end;
      until not FindNext(FR);
    finally
      FindClose(FR);
    end;
  end;
end;

function JianyingFound(): Boolean;
var
  Local, PF: String;
begin
  Local := ExpandConstant('{localappdata}');
  PF := ExpandConstant('{pf}');
  Result :=
    FileExists(Local + '\JianyingPro\Apps\JianyingPro.exe')
    or FileExists(Local + '\Programs\JianyingPro\JianyingPro.exe')
    or FileExists(PF + '\JianyingPro\JianyingPro.exe');
  if not Result then
    Result := HasJianyingUnder(Local + '\JianyingPro\Apps');
end;

{ 目录能不能写 —— 伴侣要往程序目录里写 伴侣配置.json / 运行日志.txt。
  ★ 试写一个临时文件来判断（Inno 的 Pascal Script 里没有 FileCreate，
    用 SaveStringToFile 的返回值更省事也更准）。 }
function DirWritable(const Dir: String): Boolean;
var
  Probe: String;
begin
  Result := False;
  if not DirExists(Dir) then
  begin
    if not ForceDirectories(Dir) then
      Exit;
  end;
  Probe := AddBackslash(Dir) + WRITE_PROBE;
  Result := SaveStringToFile(Probe, 'ok', False);
  if Result then
    DeleteFile(Probe);
end;

{ ① 选目录时预检查：写不进去就当场说清楚，别等装到一半才炸。
    （装在 Program Files 这类地方，伴侣**每次启动都会静默保存失败** ——
      配置和日志都写不进去，用户还看不出哪里不对。） }
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    { ★ 静默安装（/VERYSILENT，批量部署/自动化用）没有人可交互，
      弹窗会把安装卡死 —— 那种场景下直接放行。 }
    if WizardSilent() then
      Exit;
    if not DirWritable(WizardDirValue()) then
    begin
      MsgBox('这个目录写不进去：' + #13#10 + WizardDirValue() + #13#10 + #13#10 +
             '剪映伴侣的「配置」和「运行日志」就保存在程序目录里，' + #13#10 +
             '装在写不进去的地方（比如 Program Files）会导致它每次启动都保存失败。' + #13#10 + #13#10 +
             '建议就用默认目录（当前用户的应用数据目录，一定能写）。',
             mbError, MB_OK);
      Result := False;
    end;
  end;
end;

{ 读伴侣 `--autosetup` 写的自动配置结果。

  ★ 落文件而不是看 stdout：exe 是 GUI 子系统、没有控制台，Exec 拿不到它的输出。
  ★★ 变量**必须是 `AnsiString`**：Inno 的 `LoadStringFromFile` 只接受 AnsiString
     形参，传 `String` 会直接 `Type mismatch`（编译期就红，实测 2026-09-19）。
     对应地，伴侣那边是**按系统 ANSI/GBK 写**这个文件的（见 jy_core.write_autosetup_result）。
  ★ 开头是 ASCII 标记（`JIANYING=1` / `DRAFT=1`），空行之后才是给人看的中文：
    标记既能被 `Pos` 稳稳匹配，正文又能直接念给用户。 }
function AutosetupText(): String;
var
  A: AnsiString;
  P: Integer;
begin
  Result := '';
  if not LoadStringFromFile(ExpandConstant('{app}') + '\' + AUTOSETUP_RESULT, A) then
    Exit;
  Result := A;
  P := Pos(#13#10#13#10, Result);
  if P > 0 then
    Result := Copy(Result, P + 4, Length(Result));
  Result := Trim(Result);
end;

function AutosetupFoundJianying(): Boolean;
var
  A: AnsiString;
  S: String;
begin
  Result := False;
  if not LoadStringFromFile(ExpandConstant('{app}') + '\' + AUTOSETUP_RESULT, A) then
    Exit;
  S := A;
  Result := Pos('JIANYING=1', S) > 0;
end;

{ ② 装完让伴侣**自己去认环境**（用户要求"不要让用户多余操作"）。
   静默跑（SW_HIDE）+ 等它退出：它是 GUI 程序，一般 2~4 秒。
   ★ 结果写在安装目录下的 AUTOSETUP_RESULT 里，下面照着念给用户听。
   ★ 绿色安装（/VERYSILENT）也照跑 —— 这一步没有窗口、没有弹框，不会卡住。
   ★ 失败不影响安装：认不到就是"下次启动再认"，返回值不用管。 }
procedure RunAutosetup();
var
  RC: Integer;
begin
  Exec(ExpandConstant('{app}') + '\剪映伴侣.exe', '--autosetup',
       ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, RC);
end;

{ ③ 认完把结果告诉用户；一个都没认到就把话说明白。**不阻断安装**。 }
procedure CurStepChanged(CurStep: TSetupStep);
var
  Msg, Auto: String;
  FoundJY: Boolean;
begin
  if CurStep <> ssPostInstall then
    Exit;
  RunAutosetup();
  FoundJY := AutosetupFoundJianying();
  Auto := AutosetupText();
  { ★ 结果文件是"交接用"的：念完就删（同样的内容在 运行日志.txt 里有一份，
    真出问题时看那个）。[UninstallDelete] 里再兜一道，防止异常中断留下它。 }
  DeleteFile(ExpandConstant('{app}') + '\' + AUTOSETUP_RESULT);
  { ★ 静默安装下 `MsgBox` 会弹出来把过程卡住（它不受 /VERYSILENT 抑制） ——
    这种场景没有人可交互，只落日志。 }
  if WizardSilent() then
    Exit;
  if FoundJY then
  begin
    Msg := '已经替你配好了，装完就能直接用（你不用做任何设置）：' + #13#10#13#10 + Auto;
    MsgBox(Msg, mbInformation, MB_OK);
    Exit;
  end;
  { 伴侣这会儿没认到剪映：可能是没装，也可能只是还没打开过。
    先看 Pascal 这边的常见落点；都没有才提示（提示里说明"伴侣会自己再认一遍"）。 }
  if JianyingFound() then
  begin
    MsgBox('剪映已经装好了。伴侣下次启动时会自己认到它，你不用做任何设置。',
           mbInformation, MB_OK);
    Exit;
  end;
  Msg :=
    '没有在这台电脑上找到「剪映专业版」。' + #13#10#13#10 +
    '剪映伴侣本身「不含剪映」，也不自带任何剪辑功能 —— 它只是替你按键、摆窗口。' + #13#10 +
    '所以没有剪映的话，它一点活也干不了。' + #13#10#13#10 +
    '请这样做：' + #13#10 +
    '  1. 先装好「剪映专业版」，并至少打开过一次；' + #13#10 +
    '  2. 打开你平时剪的那个草稿，停在「草稿编辑页」；' + #13#10 +
    '  3. 再点伴侣上的「一键导出」。' + #13#10#13#10 +
    '（装好之后伴侣会自己认到剪映和草稿位置，你不需要填任何路径。）';
  MsgBox(Msg, mbInformation, MB_OK);
end;
