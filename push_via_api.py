"""通过 GitHub Git Data API 推送仓库内容（github.com 不可达时的备用通道）。

api.github.com 可达但 github.com git 端口被重置时使用。
本地 git 历史保留在本地；远端得到一个包含全部文件的初始提交。
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import urllib.request

REPO = "pillowAIQ/memo-app"
API = "https://api.github.com"


def token() -> str:
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, check=True,
    ).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    sys.exit("no github credential")


def api(method: str, path: str, tok: str, payload: dict | None = None):
    req = urllib.request.Request(
        f"{API}{path}",
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"token {tok}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> None:
    tok = token()

    # 空仓库不支持 git/blobs：先用 contents API 提交 README 制造初始提交
    try:
        api("GET", f"/repos/{REPO}/git/ref/heads/main", tok)
        print("repo already has main, skip init")
    except Exception:
        readme = open("README.md", "rb").read()
        api("PUT", f"/repos/{REPO}/contents/README.md", tok, {
            "message": "Initial commit",
            "content": base64.b64encode(readme).decode(),
        })
        print("created initial commit via contents API")

    files = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.split()

    tree = []
    for i, path in enumerate(files, 1):
        data = open(path, "rb").read()
        blob = api("POST", f"/repos/{REPO}/git/blobs", tok, {
            "content": base64.b64encode(data).decode(),
            "encoding": "base64",
        })
        tree.append({
            "path": path,
            "mode": "100644",
            "type": "blob",
            "sha": blob["sha"],
        })
        print(f"[{i}/{len(files)}] {path}", flush=True)

    t = api("POST", f"/repos/{REPO}/git/trees", tok, {"tree": tree})
    head = api("GET", f"/repos/{REPO}/git/ref/heads/main", tok)["object"]["sha"]
    c = api("POST", f"/repos/{REPO}/git/commits", tok, {
        "message": "memo app: PySide6 memo tool with categories, widget, quick capture",
        "tree": t["sha"],
        "parents": [head],
    })
    api("PATCH", f"/repos/{REPO}/git/refs/heads/main", tok, {"sha": c["sha"]})
    print(f"done: {len(files)} files -> {c['sha'][:8]}")


if __name__ == "__main__":
    main()
