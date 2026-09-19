# 第三方资源声明 / Third-Party Notices

本仓库主体（`jy_core.py`、`剪映伴侣.py`、`JianyingCompanion.spec`、`剪映伴侣安装包.iss`、
测试与文档等）以 **MIT License** 发布，版权归本仓库作者所有，见 [`LICENSE`](LICENSE)。

下面列出仓库里**不是**本仓库作者原创、随仓库一起分发的文件。

---

## 1. `ChineseSimplified.isl` —— Inno Setup 简体中文语言文件

- **用途**：安装包（`剪映伴侣安装包.iss`）的 `[Languages]` 段引用它来显示中文安装向导界面。
  它是**构建期资源**，不会被打包进最终安装产物里额外分发。
- **来源**：Inno Setup 用户贡献翻译（unofficial translations）
  <https://jrsoftware.org/files/istrans/> →
  上游维护仓库 <https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation>
  （文件头注释署名 Maintained by Zhenghan Yang）。
- **许可**：MIT License，Copyright (c) 2019 - 2020 kirakira。全文如下：

```
MIT License

Copyright (c) 2019 - 2020 kirakira

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

> 如果你想改成别的语言、或完全去掉中文向导：把 `ChineseSimplified.isl` 删掉，
> 并把 `.iss` 里 `[Languages]` 段改成 `Name: "english"; MessagesFile: "compiler:Default.isl"` 即可。

---

## 2. 运行时依赖（不随仓库分发，由 PyInstaller 打进 exe）

| 组件 | 许可 | 说明 |
|---|---|---|
| Python | PSF License | 解释器 |
| tkinter / Tcl-Tk | PSF / Tcl-Tk License | 界面 |
| pywinauto（含 comtypes、pywin32） | BSD-3-Clause / MIT / PSF | 窗口与控件识别 |

这些库只出现在 `requirements.txt` 与 PyInstaller 产物中，仓库本身不包含其源码。
打包分发时请遵守各自的许可条款。

---

## 3. 关于「剪映 / CapCut」

本项目与剪映（JianyingPro / CapCut）的开发者**没有任何关联**，未获得其授权、也未获其背书。
「剪映」「JianyingPro」等名称与商标归其各自所有者。
本项目**不包含**剪映的任何程序文件、也不修改它们的任何内容 —— 详见 `README.md` 顶部的免责声明。
