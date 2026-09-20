# -*- coding: utf-8 -*-
"""`github.com` 连不上时，用 GitHub 的 **Git Data API** 把本地 main 推上去。

为什么需要它（2026-09-20 实测）：
  · 本机 `git push` 报 `Failed to connect to github.com:443`（21 秒超时）；
  · 但同一时刻 `api.github.com` 是 **200**、`uploads.github.com` 是 **302**
    —— 也就是**只有 github.com 这一个 host 不通**（应用层的 host 级封锁/抖动），
    不是断网，也不是证书问题（证书问题会报 CRYPT_E_NO_REVOCATION_CHECK）。
  · 所以附件照样能传（`_release.py` 走 uploads.github.com），只有 **git 传输**被卡住。

本工具把"一次 push"拆成 Git Data API 的四步（blob → tree → commit → 更新 ref）。
★★ 核心自检：每一步都把服务器返回的 SHA 和**本地对象的 SHA 对一遍**，
   全对才算成功。对上了就意味着远端那个提交与本地**逐字节相同**（同一个 SHA），
   本地/远端不会分叉；对不上就**中止**（宁可没推，也不能推一个"内容悄悄不同"的提交）。

★ 内容一律从**本地 git 对象库**取（`git cat-file blob`），**不要读工作区文件** ——
  仓库开了 CRLF→LF 归一化，工作区是 CRLF、git 里存的是 LF；读工作区会让 blob SHA 不同，
  于是 tree/commit SHA 全不一样（表面"推成功了"，实际内容与本地不一致）。

用法:
    python _push_api.py            # 推送
    python _push_api.py --dry      # 只做四步里的前三步（不更新 ref），用来验证 SHA 对不对
"""
import base64
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _release as R  # noqa: E402  （复用它的 curl+token：本机 Python 连不上 GitHub）

BRANCH = "main"


def git(*args, raw=False):
    p = subprocess.run(["git"] + list(args), cwd=str(HERE),
                       capture_output=True)
    if p.returncode != 0:
        sys.exit("git %s 失败: %s" % (" ".join(args),
                                      p.stderr.decode("utf-8", "replace").strip()))
    return p.stdout if raw else p.stdout.decode("utf-8").strip()


def head_meta():
    sha = git("rev-parse", "HEAD")
    return {
        "sha": sha,
        "tree": git("rev-parse", "HEAD^{tree}"),
        "parent": git("rev-parse", "HEAD^"),
        "message": git("log", "-1", "--format=%B"),
        "author_name": git("show", "-s", "--format=%an", "HEAD"),
        "author_email": git("show", "-s", "--format=%ae", "HEAD"),
        "author_date": git("show", "-s", "--format=%aI", "HEAD"),
        "committer_name": git("show", "-s", "--format=%cn", "HEAD"),
        "committer_email": git("show", "-s", "--format=%ce", "HEAD"),
        "committer_date": git("show", "-s", "--format=%cI", "HEAD"),
    }


def changed_paths(parent, sha):
    """改动过的**完整路径**（用 -z 拿，避免中文被转义成 \\345\\274\\200 那种形式）。"""
    out = git("diff-tree", "-r", "-z", "--name-only", parent, sha, raw=True)
    return [p.decode("utf-8") for p in out.split(b"\0") if p]


def ls_tree(sha):
    """{path: (mode, blob_sha)}"""
    out = git("ls-tree", "-r", "-z", sha, raw=True)
    d = {}
    for rec in out.split(b"\0"):
        if not rec:
            continue
        meta, _, path = rec.partition(b"\t")
        mode, _ty, bsha = meta.split()
        d[path.decode("utf-8")] = (mode.decode("ascii"), bsha.decode("ascii"))
    return d


def blob_b64(blob_sha):
    """从**对象库**取原始字节（不是工作区文件，见文件头的警告）。"""
    raw = git("cat-file", "blob", blob_sha, raw=True)
    return base64.b64encode(raw).decode("ascii")


def main():
    dry = "--dry" in sys.argv
    m = head_meta()
    print("本地 HEAD :", m["sha"])
    print("  tree    :", m["tree"])
    print("  parent  :", m["parent"])

    tok = R.token()
    st, me = R.api(tok, "/user")
    if st != 200:
        sys.exit("token 不可用: %s" % me)
    print("登录为    :", me.get("login"))

    st, ref = R.api(tok, "/repos/%s/git/ref/heads/%s" % (R.REPO, BRANCH))
    if st != 200:
        sys.exit("取远端 ref 失败: %s" % ref)
    remote_sha = ref["object"]["sha"]
    print("远端 main :", remote_sha)

    # —— 只允许快进（parent 必须正好是远端当前位置）——
    if remote_sha != m["parent"]:
        sys.exit("!! 远端 main 不是本地 HEAD 的父提交（%s != %s）：拒绝推，先手工看"
                 % (remote_sha, m["parent"]))
    st, rc = R.api(tok, "/repos/%s/git/commits/%s" % (R.REPO, remote_sha))
    if st != 200:
        sys.exit("取远端提交失败: %s" % rc)
    if rc["tree"]["sha"] != git("rev-parse", m["parent"] + "^{tree}"):
        sys.exit("!! 远端父提交的 tree 与本地不一致，拒绝推")
    print("父提交 tree 两边一致 ✓")

    # —— ① 逐个文件建 blob，并要求返回的 SHA == 本地 blob SHA ——
    paths = changed_paths(m["parent"], m["sha"])
    local_tree = ls_tree(m["sha"])
    entries = []
    for p in paths:
        mode, bsha = local_tree.get(p) or (None, None)
        if not bsha:
            sys.exit("!! 本地树里找不到 %r" % p)
        st, r = R.api(tok, "/repos/%s/git/blobs" % R.REPO, "POST",
                      {"content": blob_b64(bsha), "encoding": "base64"})
        if st not in (200, 201):
            sys.exit("建 blob 失败 %s: %s" % (p, r))
        if r["sha"] != bsha:
            sys.exit("!! blob SHA 不一致 %s: 远端 %s != 本地 %s" % (p, r["sha"], bsha))
        entries.append({"path": p, "mode": mode, "type": "blob", "sha": bsha})
        print("  blob ✓ %-28s %s" % (p, bsha[:12]))

    # —— ② 建 tree，要求 SHA == 本地 tree SHA ——
    st, tr = R.api(tok, "/repos/%s/git/trees" % R.REPO, "POST",
                   {"base_tree": rc["tree"]["sha"], "tree": entries})
    if st not in (200, 201):
        sys.exit("建 tree 失败: %s" % tr)
    if tr["sha"] != m["tree"]:
        sys.exit("!! tree SHA 不一致：远端 %s != 本地 %s（内容不同，已中止）"
                 % (tr["sha"], m["tree"]))
    print("tree ✓", tr["sha"])

    # —— ③ 建 commit（作者/提交者/时间全部照抄本地），要求 SHA == 本地 HEAD ——
    payload = {
        "message": m["message"],
        "tree": tr["sha"],
        "parents": [m["parent"]],
        "author": {"name": m["author_name"], "email": m["author_email"],
                   "date": m["author_date"]},
        "committer": {"name": m["committer_name"], "email": m["committer_email"],
                      "date": m["committer_date"]},
    }
    st, cm = R.api(tok, "/repos/%s/git/commits" % R.REPO, "POST", payload)
    if st not in (200, 201):
        sys.exit("建 commit 失败: %s" % cm)
    if cm["sha"] != m["sha"]:
        sys.exit("!! commit SHA 不一致：远端 %s != 本地 %s（已中止，远端 ref 没动）"
                 % (cm["sha"], m["sha"]))
    print("commit ✓", cm["sha"])

    if dry:
        print("\n--dry：前三步全对，未更新 ref。")
        return 0

    # —— ④ 更新 ref（force=False：远端若已被别人推进会失败，不覆盖）——
    st, up = R.api(tok, "/repos/%s/git/refs/heads/%s" % (R.REPO, BRANCH),
                   "PATCH", {"sha": cm["sha"], "force": False})
    if st not in (200, 201):
        sys.exit("更新 ref 失败: %s" % up)
    print("ref ✓", up["object"]["sha"])

    # —— 复核 + 把本地 origin/main 对齐（github.com 不通，git fetch 做不了）——
    st, ref2 = R.api(tok, "/repos/%s/git/ref/heads/%s" % (R.REPO, BRANCH))
    got = ref2["object"]["sha"]
    if got != m["sha"]:
        sys.exit("!! 复核失败：远端 main = %s" % got)
    git("update-ref", "refs/remotes/origin/%s" % BRANCH, m["sha"])
    print("\n✅ 已推送，远端 main =", got, "（本地 origin/%s 已对齐）" % BRANCH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
