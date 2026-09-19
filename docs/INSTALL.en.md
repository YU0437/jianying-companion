# Installation guide (step by step)

[Back to README](../README.en.md) ｜ [简体中文](安装教程.md)

---

## Contents

- [Step 0: Check the two prerequisites](#step-0-check-the-two-prerequisites)
- [Step 1: Download the installer](#step-1-download-the-installer)
- [Step 2: Get past the "Unknown publisher" warning](#step-2-get-past-the-unknown-publisher-warning)
- [Step 3: Choose the install location](#step-3-choose-the-install-location)
- [Step 4: Setup finishes (it configures itself)](#step-4-setup-finishes-it-configures-itself)
- [Step 5: First launch](#step-5-first-launch)
- [Step 6: Run it once](#step-6-run-it-once)
- [Uninstalling](#uninstalling)
- [If it won't install, or something's wrong](#if-it-wont-install-or-somethings-wrong)

---

## Step 0: Check the two prerequisites

| Check | How |
|---|---|
| You have **Jianying Pro** (the desktop version, not mobile/web) | "剪映专业版" appears in the Start menu |
| Jianying has been **opened at least once** | The first launch creates the config file the companion reads to locate your drafts |

> If you haven't opened it yet, that's fine — open Jianying once, then start the companion.
> (The companion re-detects its environment on every launch.)
> The companion **does not include Jianying** and cannot edit video; it's just a "press keys for you" helper.

## Step 1: Download the installer

From the project's [Releases](../../releases) page, download the latest:

```
剪映伴侣-Setup-1.0.0.exe      (~22 MB)
```

## Step 2: Get past the "Unknown publisher" warning

After double-clicking, Windows will very likely show a blue dialog:

> **Windows protected your PC**
> This app comes from an unknown publisher

**This is normal.** There is exactly one reason: the author **has no code-signing certificate**
(they cost several hundred to a thousand-plus per year for individuals). Every unsigned personal
tool shows this. It does not mean it's a virus.

What to do:

1. Click **"More info"**
2. A **"Run anyway"** button appears — click it

If you don't see "More info", click the small text at the top-left; or right-click the installer →
Properties → tick **"Unblock"** at the bottom → OK, then double-click it again.

> If **antivirus** (or Windows Defender) blocks it: this tool does exactly two things —
> **simulate key presses** and **move windows**. It reads and writes no video files and has no
> network code. It is safe to allow/trust.

## Step 3: Choose the install location

The installer defaults to:

```
%LOCALAPPDATA%\JianyingCompanion
```

(usually `C:\Users\<you>\AppData\Local\JianyingCompanion`)

**Keep the default.** Here's why:

> The companion stores its config file and run log inside its own program folder.
> If you install it to a directory that needs administrator rights to write, such as
> `C:\Program Files\...`, **saving will fail on every launch** (any hotkey changes or draft-folder
> choices you make would be lost).

The installer **actively blocks** this — as soon as you pick an unwritable directory it tells you
to go back to the default.

## Step 4: Setup finishes (it configures itself)

Near the end of setup, the companion **runs its environment detection once** and tells you the result:

- **Found where Jianying is installed** → written into the config
- **Found where your drafts live** → written into the config
  (it doesn't guess common paths — it **reads Jianying's own setting**, so it still finds a
  non-default draft location)
- **Jianying not installed** → it says so plainly (**it will not block the install**, but you do
  need to install and open Jianying once)

**You have nothing to configure.** Click "Finish" and you're ready.

## Step 5: First launch

After installing:

- Setup asks whether to **open the manual** (recommended)
- Launch "剪映伴侣" from the Start menu / desktop shortcut

On first launch it **runs an environment self-check again** and reports the result on the floating
ball:

- All good → "体检通过 · 可以开始用了"
- Something missing → the ball expands and spells out what's missing

You'll also see a **small ball** appear in a screen corner (docked at Jianying's bottom-right; if
Jianying isn't running, at your screen's bottom-right). That's it — unobtrusive until you hover.

## Step 6: Run it once

1. Open Jianying and go into the editing page of **the draft you want to process**
2. **Select** the clips you want pre-composed on the timeline (box-select or Ctrl-click)
3. **Left-click the ball**

Then follow what the ball says:

| Ball shows | What you do |
|---|---|
| 执行中… / 等待渲染 87s | Nothing — just wait |
| **请双击草稿「XXX」卡片打开…** | Go to Jianying's home page and **double-click** the card it names |
| **请手动把预合成文件拖进时间线** | Press **Home** on the timeline first, then drag the file in |
| **等你导出** | Export normally in Jianying (no VIP prompt) |
| Export done | **Left-click the ball** → confirm → the draft is restored automatically |

> You can **abort** at any time: left-click the ball (it asks first) or right-click →
> "中止本次流程（自动还原）". Aborting **automatically restores the draft** — it never leaves you
> with a half-modified draft.

## Uninstalling

Uninstalling **does not delete**:

- Your config (`伴侣配置.json`)
- The draft backups on your Desktop (`Desktop\剪映预合成导出\_备份\`)

To clean up completely: after uninstalling, delete the install folder and the
"剪映预合成导出" folder on your Desktop.

> ★ If the companion previously wrote a "pre-compose" hotkey into Jianying, uninstalling will
> **not** remove it. To remove it: launch the companion once → right-click → "还原剪映快捷键设置",
> then uninstall. (Or delete that entry yourself in Jianying → Settings → Shortcuts.)

## If it won't install, or something's wrong

| Symptom | Cause and fix |
|---|---|
| Double-clicking does nothing | See [Step 2](#step-2-get-past-the-unknown-publisher-warning); or antivirus silently blocked it |
| Installed but won't launch | Reinstall to the default directory (you probably picked a directory needing admin rights) |
| No ball appears | Check `%LOCALAPPDATA%\JianyingCompanion\运行日志.txt`; or use the right-click menu → "打开运行日志" |
| "Jianying Pro not found" | Right-click the ball → "设置剪映路径…" and pick `JianyingPro.exe` |
| "Draft folder not found" | Right-click the ball → "设置草稿目录…" (usually `…\Projects\com.lveditor.draft`) |
| Import says "unsupported media format" | In Jianying, click **back to the draft list** (top-left) to make it save once, then reopen the draft and drag the file in |

More troubleshooting in the [README FAQ](../README.en.md#faq).
