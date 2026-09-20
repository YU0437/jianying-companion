# -*- coding: utf-8 -*-
"""把 CHANGELOG 里某一版的内容发成 GitHub Release，并附上安装包。

为什么这么写（三个都是本机踩出来的，别"优化"掉）：

1. **传输走 curl，不走 urllib。** 本机 Python 连 `github.com` / `api.github.com`
   一律 `[SSL: UNEXPECTED_EOF_WHILE_READING]`（连 baidu 是好的，所以不是断网），
   而 curl 能拿到 200 —— 两台 Python（3.13.12 / miniconda 3.0.18）都一样，
   换 TLS 版本也没用。所以脚本里所有请求都 `subprocess` 调 curl。
2. **curl 必须带 `--ssl-no-revoke`。** 不带就 `CRYPT_E_NO_REVOCATION_CHECK`：
   GitHub 的证书链在这台机器上查不了吊销（CRL/OCSP 出不去），schannel 直接拒连。
   同理 `git push` 要加 `-c http.sslVerify=false`（见 CHANGELOG 的"发版"一节）。
3. **本机没有 `gh`。** 所以自己拼 REST。

凭据优先级：环境变量 `GH_TOKEN` → `git credential fill`（GCM 里存的 OAuth token，
不会打印、不会落盘）。

用法：
    python _release.py v1.6.0 installer/剪映伴侣-Setup-1.6.0.exe
    python _release.py v1.6.0 installer/剪映伴侣-Setup-1.6.0.exe --no-upload
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = "YU0437/jianying-companion"
API = "https://api.github.com"
CURL = ["curl", "-sS", "--ssl-no-revoke", "-H", "User-Agent: jianying-companion-release"]
# 附件名统一 ASCII：中文名在部分下载器/浏览器上会被转义成乱码
ASSET_PREFIX = "JianyingCompanion-Setup-"


def token():
    t = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if t:
        return t.strip()
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, cwd=str(ROOT),
        env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
    ).stdout
    m = re.search(r"^password=(.+)$", out, re.M)
    if not m:
        sys.exit("拿不到 token：既没有 GH_TOKEN，git credential 也没存")
    return m.group(1).strip()


def curl(args, body=None):
    """跑一次 curl，返回 (exitcode, http_status, body_bytes)。

    正文写临时文件、状态码从 `-w` 拿：直接读 stdout 会把 JSON 和状态码混在一起，
    而靠 body 里的 message 猜状态码在 422 和 400 之间会猜错。
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".out") as f:
        tmp = f.name
    try:
        p = subprocess.run(CURL + args + ["-o", tmp, "-w", "%{http_code}"],
                           input=body, capture_output=True, cwd=str(ROOT))
        blob = Path(tmp).read_bytes()
        try:
            code = int(p.stdout.decode("ascii").strip() or 0)
        except ValueError:
            code = 0
        return p.returncode, code, blob
    finally:
        Path(tmp).unlink(missing_ok=True)


def api(tok, url, method="GET", data=None, raw=None, ctype="application/json"):
    if not url.startswith("http"):
        url = API + url
    args = ["-X", method, "-H", "Authorization: Bearer " + tok,
            "-H", "Accept: application/vnd.github+json"]
    body = None
    if raw is not None:
        args += ["-H", "Content-Type: " + ctype, "--data-binary", "@-"]
        body = raw
    elif data is not None:
        args += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
        body = json.dumps(data).encode("utf-8")
    args.append(url)
    rc, code, out = curl(args, body)
    if rc != 0:
        sys.exit("curl 失败 rc=%d: %s" % (rc, out[:300].decode("utf-8", "replace")))
    try:
        j = json.loads(out)
    except Exception:
        return code, out.decode("utf-8", "replace")
    if code >= 300 and isinstance(j, dict):
        print("  API %s %s → %s %s" % (method, url, code, j.get("message", "")))
    return code, j


def changelog_section(ver):
    """从 CHANGELOG.md 里抠出 `## [x.y.z]` 到下一个 `## [` 之间的正文。"""
    txt = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(r"^## \[%s\][^\n]*\n(.*?)(?=^## \[|\Z)" % re.escape(ver), txt, re.M | re.S)
    return m.group(0).strip() if m else ""


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    tag, exe = sys.argv[1], sys.argv[2]
    ver = tag.lstrip("v")
    do_upload = "--no-upload" not in sys.argv
    path = Path(exe)
    if not path.is_absolute():
        path = ROOT / path
    name = ASSET_PREFIX + ver + ".exe"

    tok = token()
    st, me = api(tok, "/user")
    if st != 200:
        sys.exit("token 不可用: %s" % me)
    print("登录为", me.get("login"))

    body = changelog_section(ver)
    if not body:
        sys.exit("CHANGELOG 里找不到 [%s] 段落" % ver)
    # 标题行形如 `## [1.6.0] — 2026-09-20 正文` → Release 名用 `v1.6.0 正文`
    head = re.sub(r"^#+\s*\[[\d.]+\]\s*—?\s*\d{4}-\d{2}-\d{2}\s*", "",
                  body.splitlines()[0]).strip()
    rname = (tag + " " + head).strip() or tag

    st, rel = api(tok, "/repos/%s/releases/tags/%s" % (REPO, tag))
    if st == 404:
        st, rel = api(tok, "/repos/%s/releases" % REPO, method="POST", data={
            "tag_name": tag, "target_commitish": "main",
            "name": rname, "body": body, "draft": False, "prerelease": False,
        })
        print("新建 Release", st)
    else:
        print("Release 已存在，同步标题与正文")
        st, rel = api(tok, "/repos/%s/releases/%d" % (REPO, rel["id"]), method="PATCH",
                      data={"name": rname, "body": body})
    if st >= 300:
        sys.exit("Release 失败: %s %s" % (st, rel))
    print("Release:", rel["html_url"], "|", rel["name"])

    if not do_upload:
        return
    if not path.exists():
        sys.exit("找不到安装包 " + str(path))
    for a in rel.get("assets", []):
        if a["name"] == name:
            api(tok, "/repos/%s/releases/assets/%d" % (REPO, a["id"]), method="DELETE")
            print("覆盖旧附件", name)
    print("上传 %.2fMB → %s" % (path.stat().st_size / 1048576.0, name))
    # upload_url 在 **uploads.github.com** 上（另一个 host），把 {?name,label} 模板换成实名
    url = rel["upload_url"].split("{")[0] + "?name=" + name
    st, res = api(tok, url, method="POST", raw=path.read_bytes(),
                  ctype="application/octet-stream")
    if st >= 300 or not isinstance(res, dict) or "browser_download_url" not in res:
        sys.exit("上传失败: %s %s" % (st, res))
    print("附件已上传:", res["browser_download_url"])


if __name__ == "__main__":
    main()
