# Development & build

[Back to README](../README.en.md) ｜ [简体中文](开发与构建.md)

---

## Contents

- [Setup](#setup)
- [Code layout](#code-layout)
- [Tests](#tests)
- [Full release pipeline](#full-release-pipeline)
- [Pitfalls we actually hit](#pitfalls-we-actually-hit)
- [Hard rules when changing this code](#hard-rules-when-changing-this-code)

---

## Setup

```bat
python -m venv C:\path\to\envs\tk
C:\path\to\envs\tk\Scripts\pip install -r requirements.txt
:: Just to run it (no installer): the minimal set is  pip install pillow
```

| Needed | Notes |
|---|---|
| Python **3.10+** (developed on 3.13) | `tkinter` required (bundled in the official Windows installer) |
| `pillow` | The **only required** third-party runtime dependency (screen capture, pixel comparison) |
| `pywinauto` | **Optional but recommended**: the fallback path that clicks the taskbar button when another window covers Jianying. Its `import` is wrapped in `try`, so it runs without it |
| `pyinstaller` | Build-time only |
| `pyflakes` | Static check only |
| Inno Setup 6 | Only for building the installer ([download](https://jrsoftware.org/isdl.php)) |

> ★ Create the venv with a Python that **has tkinter** (e.g. an interpreter installed from
> python.org), otherwise the GUI tests can't run.

## Code layout

| File | Responsibility |
|---|---|
| `jy_core.py` | **The core.** Window enumeration, key injection, pixel self-checks, backup/restore, the whole pipeline. No GUI code — it can be run and tested on its own |
| `剪映伴侣.py` | **The GUI.** A tk floating ball + right-click menu + a queue-driven event loop. Can be embedded into Jianying's window via `SetParent` |
| `使用说明.txt` | The end-user manual shipped with the installer (**keep it in sync with behaviour changes**) |
| `JianyingCompanion.spec` | PyInstaller config (onefile, UPX off, version info, icon) |
| `剪映伴侣安装包.iss` | Inno Setup script |

One architectural rule: **the GUI does no business logic.** Every "did it actually work?" decision
lives in `jy_core`; the GUI only pushes `status_cb / finished_cb` into a queue and renders them.

## Tests

```bat
python test_guard.py            # main regression (red lines + strings + shape + behaviour)
python test_gui_smoke.py        # GUI smoke test (really opens a tk window)
python test_settle.py           # file-settle detection
python _test_backup_restore.py  # offline: backup/restore + red-line guards
```

**All four must be green before packaging.**

| Test | How it's written | What makes it fail |
|---|---|---|
| `test_guard.py` | Three kinds of assertions mixed: ① **shape** (names/constants present, source contains a given line) ② **behaviour** (really calls pure functions, or runs the pipeline with stubs) ③ **strings** (key messages must really exist) | Renaming functions, deleting messages, changing order |
| `test_gui_smoke.py` | Really constructs `Companion()`, stubbing out everything that touches the outside world | GUI broken, menu indices drifted, modal dialogs not stubbed |
| `test_settle.py` | Uses real files to test the settle detection | Settle logic regressed (e.g. falling back to "size only") |
| `_test_backup_restore.py` | Fully offline, with `core.config_path` pointed at a temp dir | A red line was broken (e.g. it started touching artifact files) |

> ⚠️ **The two test files have different `check()` signatures:**
> - `test_guard.check(name, got, want)` — the second argument is the **actual value**, compared internally
> - `test_gui_smoke.check(name, cond, extra)` — the second argument is a **boolean condition**
>
> Copying an assertion from `test_guard` into `test_gui_smoke` easily produces `check("x", rc, 0)`
> — `rc=0` is falsy, so the test **falsely fails**; conversely `check("x", rc)` is **always true
> (a false green)**.

## Full release pipeline

**The order is not negotiable.** In particular, 5 must come before 6.

```bat
:: 1) Compile
python -m py_compile jy_core.py 剪映伴侣.py

:: 2) Static check (only the single "local variable 'e' unused" warning in jy_core is allowed)
python -m pyflakes jy_core.py 剪映伴侣.py test_guard.py test_gui_smoke.py _verify_exe.py

:: 3) All four test suites
python test_guard.py && python test_gui_smoke.py && python test_settle.py && python _test_backup_restore.py

:: 4) Build the exe
python -m PyInstaller JianyingCompanion.spec --noconfirm --distpath dist --workpath build

:: 5) Verify the exe (names / constants / strings / default config really inside it)
python _verify_exe.py

:: 6) Deploy locally + relaunch and verify (md5 comparison)
python 重启伴侣.py

:: 7) Sync the shipped manual
copy /Y 使用说明.txt "%LOCALAPPDATA%\JianyingCompanion\使用说明.txt"

:: 8) Build the installer (★ must be after step 4)
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" 剪映伴侣安装包.iss
```

What `_verify_exe.py` does: it reads the produced exe directly, unpacks the PyInstaller archive, and
deserialises the code objects of the main script and `jy_core`, then asserts:

- This batch's new **function names** are really present
- This batch's new **string constants** are really **exact constants** (not substrings)
- A few key **default config values** are correct (guards against "I changed `default_config()` but
  the exe still has the old value")
- The pipeline stage table is **contiguous** (the progress bar never jumps backwards)
- **None** of the blacklisted functions are present

> ★ This step matters: `py_compile` and the unit tests only prove the **source** is right — not that
> the copy **inside the exe** is right. A packaging config missing a hidden import, or forgetting to
> rebuild, yields a "deployment looked successful but not one line of new code runs" result.

### Silent install self-test (optional, but worth it)

```bat
"%LOCALAPPDATA%\Temp\jc_e2e\...\剪映伴侣-Setup-1.0.0.exe" /VERYSILENT /SUPPRESSMSGBOXES ^
  /NORESTART /NOICONS "/GROUP=jcE2E" "/DIR=%LOCALAPPDATA%\Temp\jc_e2e"
```

Then check: the config contains only the whitelisted keys, the path values are right, and the result
file was deleted. Then uninstall: the user config is **kept**, the log/result files are cleaned up.

## Pitfalls we actually hit

These are real, not theoretical:

### 1) ★★★ PyInstaller first, ISCC second

If you build the installer first and rebuild the exe afterwards, **the installer still embeds the
old exe**. Your E2E test then exercises the previous build, with a very confusing symptom
(the new feature "doesn't work" — because it was never packaged).
Fixed order: **source → tests → PyInstaller → _verify_exe → ISCC → then E2E**.

### 2) ★★ Inno Setup can only read the result file into an `AnsiString`

The second parameter of `LoadStringFromFile(Path, S)` **must be an `AnsiString` variable**; passing a
`String` is a **compile-time** `Type mismatch`. The pattern:

```pascal
var A: AnsiString; S: String;
begin
  LoadStringFromFile(ExpandConstant('{app}\_result.txt'), A);
  S := A;
end;
```

It decodes bytes as **ANSI**, so the file must be written in the **system ANSI** codepage
(GBK on Chinese Windows). The robust pattern: put a few **ASCII-only markers** at the top
(`JIANYING=1` / `DRAFT=1`) and test them with `Pos()`, with any non-ASCII prose in a separate block.

### 3) ★ Pascal block comments `{ }` do not nest

Putting `{app}` inside a `{ }` comment **closes the comment early** → `Syntax error`.
Never write `{` or `}` constants inside comments.

### 4) ★ CLI subcommands must come before the single-instance guard

An install-time subcommand such as `--autosetup`, if placed after `ensure_single_instance()`, gets
swallowed by the guard when an older build is already running (it exits and does nothing).
`__main__` order: **CLI subcommand → selftest → single-instance guard**.

### 5) ★ `Path("C:") / "x"` is a **drive-relative** path

It means "x inside the current directory on drive C", not `C:\x`. Write `Path(f"{drv}:/")`.

### 6) ★ Cross-line implicit concatenation produces **one merged constant**

```python
msg = ("line one\n"
       "line two\n")        # the constant is "line one\nline two\n", not the two pieces
```

So constant verification must use the **full concatenated result**, or fall back to a **substring**
check (and when using a substring, make sure that sentence appears **exactly once** in the whole
source — otherwise a comment or docstring gives you a false green).

### 7) ★ Source-scanning assertions falsely fail when a comment mentions the target line

Using `src.index("keyword")` to locate something can land on a comment that also mentions the
keyword. Use `rindex()`, or compare within a slice of the relevant block.

## Hard rules when changing this code

1. **Never touch the red line**: artifact files (`Resources\combination\*.mp4`) are **not modified by
   a single byte**. The only permitted writes are `.json` files inside the draft folder.
   `test_guard` and `_test_backup_restore` will catch you.
2. **A behaviour change must come with an assertion that goes red first**: write the assertion that
   proves the old behaviour is wrong, then change the code to make it green. Otherwise you can't
   demonstrate that you actually changed anything.
3. **No silent failures**: anything you can't do must return `ok=False` and propagate, so the UI can
   tell the user the truth. **Pretending to succeed** is the most serious bug class in this codebase.
4. **Any entry point mentioned in a message must really exist in the menu**, with the same name
   (there are tests guarding this).
5. **Changing `default_config()` ≠ changing the effective value**: existing users already have the
   old value written to disk, so verification must look at **all three layers** (exe default /
   deployed config / final effective value).
6. **Never `sleep` on the main thread waiting for an external program**: it freezes the floating
   ball, and the user sees "I clicked and nothing happened". All waiting goes to worker threads.
7. **Self-rescheduling `after()` loops** must re-register in a `finally` block and catch **broad
   exceptions** — otherwise a single malformed message kills the whole chain and the ball stops
   updating forever.
