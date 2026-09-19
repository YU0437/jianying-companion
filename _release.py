# -*- coding: utf-8 -*-
"""一次性工具：给 YU0437/jianying-companion 发 Release 并上传安装包。
用法：python _release.py 1.1.0
token 经 git credential fill 取，只在内存里用，绝不打印。全部跑完即可删。"""
import json, subprocess, sys, urllib.request, urllib.parse, hashlib, pathlib

REPO = "YU0437/jianying-companion"
VER = sys.argv[1]
TAG = f"v{VER}"
ASSET = pathlib.Path(f"installer/剪映伴侣-Setup-{VER}.exe")
assert ASSET.exists(), f"找不到 {ASSET}"

fill = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n",
                      capture_output=True, text=True)
tok = dict(l.split("=", 1) for l in fill.stdout.splitlines() if "=" in l).get("password", "")
assert tok, "没拿到凭据"
AUTH = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
        "User-Agent": "jy-release"}


def api(url, method="GET", data=None, headers=None, timeout=120):
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("User-Agent", "jy-release")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return json.loads(body) if body else {}


body = api(f"https://raw.githubusercontent.com/YU0437/jianying-companion/main/CHANGELOG.md"
           if False else f"https://api.github.com/repos/{REPO}/contents/CHANGELOG.md?ref=main",
           headers=AUTH)
import base64
md = base64.b64decode(body["content"]).decode("utf-8")
# 取本版本那一节（## [x.y.z] 到下一个 ## 为止）
start = md.index(f"## [{VER}]")
nxt = md.find("\n## [", start + 1)
section = md[start:nxt if nxt != -1 else None].strip()

rel = api(f"https://api.github.com/repos/{REPO}/releases", "POST",
          json.dumps({"tag_name": TAG, "target_commitish": "main",
                      "name": f"剪映伴侣 v{VER}", "body": section,
                      "draft": False, "prerelease": False}).encode(),
          {**AUTH, "Content-Type": "application/json"})
print(f"[ok] Release 已建: tag={rel['tag_name']}  url={rel['html_url']}")

data = ASSET.read_bytes()
name_q = urllib.parse.quote(f"JianyingCompanion-Setup-{VER}.exe", safe="")
up = api(f"https://uploads.github.com/repos/{REPO}/releases/{rel['id']}/assets?name={name_q}",
         "POST", data, {**AUTH, "Content-Type": "application/octet-stream"}, timeout=600)
print(f"[ok] 资产: {up['name']}  size={up['size']}  state={up['state']}  "
      f"md5(本地)={hashlib.md5(data).hexdigest()}")
print("[verify]", "尺寸一致" if up["size"] == ASSET.stat().st_size else "!!尺寸不一致!!")
print("RELEASE_URL=" + rel["html_url"])
