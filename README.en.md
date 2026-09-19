# Jianying Companion

[简体中文](README.md) ｜ **English**

> A floating ball that presses the hotkeys for you after Jianying Pro's "pre-compose" step.
> **It only simulates keyboard/mouse input and moves windows — it never touches, modifies, or decrypts any video file.**

---

## ⚠️ Read this first (disclaimer)

- This project does **not** crack, bypass, or decrypt any encryption or VIP check in Jianying,
  and it does **not** modify any video file. The only thing it does is
  **press keyboard shortcuts and move windows on your own machine, for you**.
- Whether a VIP prompt appears, and whether exporting works, depends on Jianying itself and your
  account — this project **does not change** any of that. It only turns a dozen manual clicks
  into a single click.
- Please use it only on **drafts you own the rights to**, and make sure your usage complies with
  Jianying's terms of service and your local laws.
- This project is **not affiliated with, authorized by, or endorsed by**
  Jianying / CapCut (Shenzhen Lianmeng Technology / ByteDance).
- Provided "as is", without warranty of any kind. **Always back up your drafts first**
  (the companion backs them up itself — see below).

---

## What it is

Jianying's "pre-compose" (packing a selection into a composite clip before exporting) can trigger
a VIP restriction in some situations. But **pre-compose itself is a built-in, free feature**
(`Ctrl+A` → `Alt+G` to make a compound clip → `Alt+H` to pre-compose). The painful part is all the
manual busywork around it:

1. Back up the draft first (so you can roll back)
2. Select all → make compound clip → pre-compose
3. **Force Jianying to save the draft once** (otherwise, after restarting, Jianying treats the
   pre-compose cache as an ordinary video and reports "unsupported media format")
4. Fully quit Jianying, reopen it, and get back to **that same** draft
5. Clear the original timeline
6. Open the output folder and drag the pre-composed video into the timeline
7. After exporting, **restore the draft to its pre-pre-compose state**

That's a dozen-odd steps, and it's easy to get one wrong (wrong draft, forgot to save, forgot to
press Home…). This tool is a floating ball: one click does steps 1–6, and step 7 becomes
"one click to restore".

## What it never does (hard limits)

These are enforced by tests in the repo (`test_guard.py` scans the source and asserts them):

| Never | Detail |
|---|---|
| ❌ Decode / transcode / decrypt / rewrite / delete **any video file** | The pre-compose artifact `<GUID>_video.mp4` is **not touched — not one byte** |
| ❌ Crack VIP, forge licences, patch Jianying | No such code exists |
| ❌ Upload any of your data | The program **has no network code at all** |
| ⚠️ Only writes `.json` inside the draft folder | Restoring requires writing draft metadata back — the only place it writes files |

The **two** cases where it writes files — both backed up beforehand, both reported to you:

1. **Backing up draft metadata** → `Desktop\剪映预合成导出\_备份\<draft>_<time>_预合成前\` (`.json` only)
2. **Writing back when restoring** → copies that backup back into the draft folder
   (and takes a `_还原前` snapshot first, just in case)

## How it works

```
You click the ball
   ↓
Back up draft .json ──→ Desktop _备份\
   ↓
Send keys: Ctrl+A → Alt+G → Alt+H        (Jianying's own free features)
   ↓
Wait for the artifact to settle + wait for Jianying to commit the "pre-compose registration"
   ↓
Kill Jianying → relaunch → wait for you to double-click that same draft
   ↓
Send keys to clear the timeline (Ctrl+A → Del, followed by a pixel self-check; reports failure if nothing changed)
   ↓
Open Resources\combination and select the artifact → you drag it in and export
   ↓
After exporting → click the ball → the draft is restored to its "before pre-compose" state
```

Every step is verified with **pixel self-checks / file fingerprints / window state**.
**If it can't do something, it says so** — it never pretends to succeed.

## Requirements

| Item | Requirement |
|---|---|
| OS | Windows 10 / 11 (x64) |
| Jianying | Jianying Pro (**tested on 11.4.1.14443**), opened **at least once** |
| Runtime | **Nothing to install when using the installer** (Python and deps are bundled) |
| From source | Python 3.10+ (developed on 3.13), `tkinter`; the only third-party dep is **Pillow** |

## Installation

### Option 1 — Download the installer (recommended for end users)

1. Grab `剪映伴侣-Setup-x.y.z.exe` from [Releases](../../releases)
2. Run it. Windows may show "Windows protected your PC / Unknown publisher" — that's because
   there is **no code-signing certificate**, which is normal for small personal tools. Click
   "More info" → "Run anyway"
3. **It works right after installing**: during setup the companion detects its environment
   (where Jianying is installed, where your drafts live) and writes it into the config —
   **you don't have to configure anything**
   - The draft location isn't guessed: it **reads Jianying's own setting**
     (Jianying → Settings → Draft location)
4. Installs to `%LOCALAPPDATA%\JianyingCompanion` by default (this location **needs no admin
   rights**, so the config and log can actually be written; the installer blocks directories like
   `Program Files` where writes would fail)

Step-by-step guide: [docs/INSTALL.en.md](docs/INSTALL.en.md).

### Option 2 — Run from source

```bat
git clone https://github.com/<your-name>/jianying-companion.git
cd jianying-companion
pip install pillow
python 剪映伴侣.py
```

### Option 3 — Build it yourself

See [docs/BUILD.en.md](docs/BUILD.en.md) (full PyInstaller + Inno Setup pipeline).

## Quick start (3 steps)

1. Open Jianying Pro and go into the editing page of **the draft you want to process**
2. **Select** the clips you want pre-composed on the timeline (box-select or Ctrl-click)
3. Click "一键导出" on the ball and follow the on-screen prompts

> Jianying **doesn't have to be running**: clicking will launch it, wait until it's ready, and
> then continue automatically — **no second click needed**.

## UI and controls

Normally it's a **46px ball** docked at Jianying's bottom-right corner, out of the timeline's way.
Hover to smoothly expand it into a bar with the full message; when it needs you to do something,
it expands by itself.

| Action | Effect |
|---|---|
| **Left-click the ball** | Start; after it finishes, clicking again = **restore the draft** |
| **Left-click while running** | **Abort this run** (asks first), then **restores automatically** |
| **Right-click the ball** | Menu (restore / abort / settings / self-check / open log…) |
| Hover | Expand for the full message; collapses ~0.3s after you leave |

### Want to stop halfway? (Yes — and it restores for you)

You can stop in both phases, and **stopping automatically restores the draft** — you'll never be
left with a half-modified draft:

- **While it's running** (ball shows "执行中…") → left-click the ball → confirm
  It stops immediately (even the longest wait, "waiting for render" up to 120s) → restores the
  draft → reopens Jianying on that draft
- **After it finished and is waiting for you to export** →
  right-click → "中止本次流程（自动还原）"

Two details:

- If you abort **before the pre-compose keys were even sent**, the draft was never touched — the
  companion just stops. It will **not** close Jianying and will **not** write any file.
- If this run produced no backup, it will **honestly tell you it could not restore** and suggest
  `Ctrl+Z` — it never pretends to have cleaned up.

> The "abort" menu item is **greyed out** when there's nothing to abort. If you can click it,
> it really will stop.

### After exporting

The ball shows "等你导出 · 导出完点我 → 还原草稿".
**Left-click the ball** → it asks "are you done exporting?" → on confirm, the draft is restored to
its pre-pre-compose state.

### Two tips when dragging the file in

- Press **Home** on the timeline first. Jianying drops the clip at the **playhead**, not where you
  click with the mouse.
- **Don't use `Ctrl+V`** — Jianying doesn't accept it; you must really drag.

## Side effects (and how to undo them)

| Action | Backup / how to undo |
|---|---|
| Backs up draft metadata to Desktop | At `Desktop\剪映预合成导出\_备份\` (openable from the menu) |
| Writes a "pre-compose" hotkey into **your Jianying config** (default `Alt+H`) | Your original settings are backed up first; right-click → "还原剪映快捷键设置" |
| **Kills and relaunches Jianying** | Required: the pre-compose file only lands after Jianying fully exits |
| **Clears the current timeline** (real delete, not "disable") | Backed up beforehand, restorable in one click; a failed clear is caught by the self-check and stops the run |

## FAQ

<details>
<summary><b>"Unsupported media format" — what now?</b></summary>

That happens when Jianying was **not made to save the draft**. Normally the companion forces a save
after pre-compose (so the registration lands in `draft_content.json`). If that step failed,
Jianying treats the pre-compose cache as a plain video after restart.

Manual fix: in Jianying, click **back to the draft list** (top-left) so it saves once, then reopen
the draft and drag the file in again.
</details>

<details>
<summary><b>"Jianying Pro not found"?</b></summary>

The lookup order is: ① running processes → ② the path remembered by auto-config →
③ a few common install locations. Portable builds or custom install directories may miss all three.

Right-click the ball → "设置剪映路径…" and pick `JianyingPro.exe`.
**You usually don't need this** — it auto-detects at install time and on every launch.
</details>

<details>
<summary><b>"Draft folder not found"?</b></summary>

The companion **reads the draft location from Jianying's own setting**
(`currentCustomDraftPath` in `%LOCALAPPDATA%\JianyingPro\User Data\Config\globalSetting`),
so it still finds it if you moved your drafts.

If it really can't: right-click → "设置草稿目录…" and point at it
(usually `…\Projects\com.lveditor.draft`).
</details>

<details>
<summary><b>Antivirus flags it?</b></summary>

This tool does exactly two things: **simulate key presses** and **move windows**. It reads and
writes no video files and has no network code. False positives come from the
"simulated input" behaviour plus the lack of code signing. It's safe to allow.
</details>

<details>
<summary><b>It seems stuck — where do I look?</b></summary>

- Right-click → "环境体检": tells you what's missing on this machine
- Right-click → "打开运行日志（发给作者）": attach this file when reporting a problem
- Right-click → "打开使用说明": the full manual shipped with the installer
- Right-click → "中止本次流程": stop at any time, with automatic restore
</details>

## Repository layout

```
jianying-precomp/
├── jy_core.py                  # Core: windows/keys/pixel checks/backup & restore/pipeline
├── 剪映伴侣.py                  # GUI: floating ball + menu + event loop
├── 使用说明.txt                 # End-user manual shipped with the installer (Chinese)
├── JianyingCompanion.spec       # PyInstaller spec
├── 剪映伴侣安装包.iss            # Inno Setup installer script
├── version_info.txt / app.ico   # exe version info and icon
├── 重启伴侣.py                  # Dev helper: kill → replace → relaunch → verify
├── test_guard.py                # Main regression suite: red lines + shape + behaviour
├── test_gui_smoke.py            # GUI smoke test (really opens a tk window once)
├── test_settle.py               # File-settle detection
├── _test_backup_restore.py      # Offline: backup/restore + red-line guards
├── _verify_exe.py               # Post-build: verify the exe really contains the code/strings
└── docs/                        # Install guide & build guide (CN + EN)
```

## Development and testing

```bat
python test_guard.py           # red lines + strings + shape + behaviour (main suite)
python test_gui_smoke.py       # GUI smoke test
python test_settle.py
python _test_backup_restore.py
```

The full release pipeline (including packaging and verification) is in
[docs/BUILD.en.md](docs/BUILD.en.md).

> Note: the GUI test **really opens a tk window** (destroyed a few seconds later), so run it in an
> environment **with a desktop session** — not in headless CI. `test_settle.py` and
> `_test_backup_restore.py` are offline.

## License

[MIT](LICENSE)

## Trademarks

"Jianying", "JianyingPro", "CapCut" and related names/trademarks belong to their respective owners.
This project is **not affiliated with** them and is neither authorized nor endorsed by them.
