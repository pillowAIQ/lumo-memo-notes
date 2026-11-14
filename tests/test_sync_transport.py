"""transport.py 测试：内存假 WebDAV 服务（http.server），双客户端语义验证。

运行：python -m unittest discover -s tests
"""
from __future__ import annotations

import sys
import threading
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memo_app.sync import protocol, transport  # noqa: E402


class FakeWebDAV(BaseHTTPRequestHandler):
    """极简 WebDAV：内存 dict 存对象，PROPFIND Depth:1 列直接子项。"""

    store: dict = {}

    def log_message(self, *a):  # 静默
        pass

    def _path(self) -> str:
        return urlsplit(self.path).path

    def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _err_inject(self) -> bool:
        """路径含 /errNNN 时返回对应状态码（测试错误分类）。"""
        p = self._path()
        for code in (401, 403, 404, 409, 412, 429, 500, 507):
            if f"/err{code}" in p:
                if code == 429:
                    self.send_response(429)
                    self.send_header("Retry-After", "7")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self._send(code)
                return True
        return False

    def do_MKCOL(self):
        if self._err_inject():
            return
        p = self._path()
        # 集合由对象路径隐含存在；MKCOL 视为幂等成功
        self._send(201)

    def do_PUT(self):
        if self._err_inject():
            return
        p = self._path()
        n = int(self.headers.get("Content-Length") or 0)
        FakeWebDAV.store[p] = self.rfile.read(n)
        self._send(201)

    def do_GET(self):
        if self._err_inject():
            return
        p = self._path()
        if p in FakeWebDAV.store:
            self._send(200, FakeWebDAV.store[p], "application/octet-stream")
        else:
            self._send(404)

    def do_PROPFIND(self):
        if self._err_inject():
            return
        p = self._path().rstrip("/") or "/"
        depth = self.headers.get("Depth", "1")
        prefix = p + "/"
        entries = [p + "/"]
        for k in FakeWebDAV.store:
            if k.startswith(prefix):
                rest = k[len(prefix):]
                if "/" not in rest:
                    entries.append(k)
        # 也收集隐含的中间集合
        dirs = set()
        for k in FakeWebDAV.store:
            parts = k.strip("/").split("/")
            for i in range(1, len(parts)):
                dirs.add("/" + "/".join(parts[:i]) + "/")
        for d in dirs:
            if d.startswith(prefix) and d != p + "/":
                rest = d[len(prefix):]
                if rest.count("/") == 0 or rest.endswith("/") and "/" not in rest.rstrip("/"):
                    entries.append(d)
        if depth == "0":
            entries = [p + "/"]
            # 存在性：p 是文件 / p 是已有集合（有对象以 p/ 为前缀）
            exists = p in FakeWebDAV.store or any(
                k.startswith(p + "/") for k in FakeWebDAV.store
            )
            if not exists and p != "/dav":
                # RFC 允许的形式：207 multistatus + propstat status=404
                body = (
                    b'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
                    b'<d:response><d:href>' + p.encode() + b'/</d:href>'
                    b'<d:propstat><d:prop/>'
                    b'<d:status>HTTP/1.1 404 Not Found</d:status>'
                    b'</d:propstat></d:response></d:multistatus>'
                )
                self._send(207, body, "application/xml")
                return
        xml = ['<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">']
        for e in sorted(set(entries)):
            coll = e.endswith("/")
            inner = "<d:collection/>" if coll else ""
            size_tag = ""
            if not coll and e in FakeWebDAV.store:
                size_tag = (
                    f"<d:getcontentlength>{len(FakeWebDAV.store[e])}"
                    "</d:getcontentlength>"
                )
            xml.append(
                f"<d:response><d:href>{e}</d:href>"
                "<d:propstat><d:prop>"
                f"<d:resourcetype>{inner}</d:resourcetype>"
                f"{size_tag}"
                "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>"
                "</d:response>"
            )
        xml.append("</d:multistatus>")
        self._send(207, "".join(xml).encode(), "application/xml")


class TransportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FakeWebDAV.store = {}
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeWebDAV)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def _client(self, vault_id: str | None = None) -> transport.WebDAVClient:
        return transport.WebDAVClient(
            f"http://127.0.0.1:{self.port}/dav/",
            "user", "pass",
            vault_id=vault_id,
            allow_http=True,
            timeout=5,
        )

    VID = "12345678-1234-1234-1234-123456789abc"
    WID = "abcdefab-cdef-abcd-efab-cdefabcdefab"

    def test_https_enforced(self) -> None:
        with self.assertRaises(transport.WebDAVError):
            transport.WebDAVClient("http://example.com/dav/", "u", "p")

    def test_put_get_roundtrip(self) -> None:
        c = self._client(self.VID)
        data = b"\x00\x01encrypted-bytes-\xff"
        c.put("commits/w/1.enc", data, create_only=True)
        self.assertEqual(c.get("commits/w/1.enc"), data)
        # 存储路径应在 vault 前缀下
        self.assertIn(f"/dav/memo-sync/{self.VID}/commits/w/1.enc",
                      FakeWebDAV.store)

    def test_propfind_lists_children(self) -> None:
        c = self._client(self.VID)
        c.put("heads/a.enc", b"h1")
        c.put("heads/b.enc", b"h2")
        c.put("commits/w/1.enc", b"c1")
        entries = c.propfind("heads")
        rels = {e.rel for e in entries}
        self.assertIn("heads/a.enc", rels)
        self.assertIn("heads/b.enc", rels)
        self.assertNotIn("commits/w/1.enc", rels)
        # is_collection 与 size 解析
        by_rel = {e.rel: e for e in entries}
        self.assertTrue(by_rel["heads"].is_collection)
        self.assertFalse(by_rel["heads/a.enc"].is_collection)
        self.assertEqual(by_rel["heads/a.enc"].size, 2)

    def test_exists_and_notfound(self) -> None:
        c = self._client(self.VID)
        self.assertFalse(c.exists("heads/nope.enc"))
        c.put("heads/x.enc", b"1")
        self.assertTrue(c.exists("heads/x.enc"))

    def test_path_escape_rejected(self) -> None:
        c = self._client(self.VID)
        for bad in ("../evil", "a/../b", "a\\b"):
            with self.assertRaises(transport.WebDAVError, msg=bad):
                c.get(bad)

    def test_href_outside_vault_ignored(self) -> None:
        c = self._client(self.VID)
        c.put("heads/a.enc", b"1")
        # 手工构造含越界 href 的响应直接测解析
        body = (
            b'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
            b'<d:response><d:href>/dav/memo-sync/OTHER/heads/x.enc</d:href>'
            b'<d:propstat><d:prop><d:resourcetype/></d:prop>'
            b'<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>'
            b'<d:response><d:href>/dav/memo-sync/' + self.VID.encode() +
            b'/heads/a.enc</d:href>'
            b'<d:propstat><d:prop><d:resourcetype/></d:prop>'
            b'<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>'
            b'</d:multistatus>'
        )
        rels = {e.rel for e in c.parse_multistatus(body)}
        self.assertEqual(rels, {"heads/a.enc"})

    def test_error_classification(self) -> None:
        c = self._client(self.VID)
        with self.assertRaises(transport.AuthError):
            c.get("err401/x")
        with self.assertRaises(transport.AuthError):
            c.get("err403/x")
        with self.assertRaises(transport.RateLimitedError) as cm:
            c.get("err429/x")
        self.assertEqual(cm.exception.retry_after, 7.0)
        with self.assertRaises(transport.PreconditionFailedError):
            c.get("err412/x")
        with self.assertRaises(transport.ServerError):
            c.get("err500/x")
        with self.assertRaises(transport.QuotaError):
            c.get("err507/x")  # 507 配额不足单独归类
        # PUT 的 409 是真实冲突，绝不能转成 NotFoundError
        with self.assertRaises(transport.WebDAVError) as cm:
            c.put("err409/x.enc", b"d")
        self.assertNotIsInstance(cm.exception, transport.NotFoundError)

    def test_jianguoyun_409_as_notfound(self) -> None:
        # 坚果云：父级集合不存在时 GET/PROPFIND 返回 409
        # (AncestorsNotFound) 而非 404——必须按不存在处理
        c = self._client(self.VID)
        with self.assertRaises(transport.NotFoundError):
            c.get("err409/x.enc")
        with self.assertRaises(transport.NotFoundError):
            c.propfind("err409/dir")
        self.assertFalse(c.exists("err409/dir"))

    def test_mkcol_409_tolerated_when_exists(self) -> None:
        # 坚果云：对已存在集合 MKCOL 返回 409——验证存在则幂等成功；
        # 集合真不存在时 409 必须原样抛出
        c = self._client(self.VID)
        with unittest.mock.patch.object(c, "exists", return_value=True):
            c.mkcol("err409/col")
        with self.assertRaises(transport.WebDAVError):
            c.mkcol("err409/col")

    def test_exists_207_404_propstat(self) -> None:
        # 假服务器对不存在对象返回 207+404-propstat，exists 必须判 False
        c = self._client(self.VID)
        self.assertFalse(c.exists("heads/definitely-not-there.enc"))

    def test_init_vault_id_validated(self) -> None:
        with self.assertRaises(transport.WebDAVError):
            transport.WebDAVClient(
                f"http://127.0.0.1:{self.port}/dav/", "u", "p",
                vault_id="../escape", allow_http=True,
            )
        with self.assertRaises(transport.WebDAVError):
            transport.WebDAVClient(
                f"http://127.0.0.1:{self.port}/dav/", "u", "p",
                vault_id="x", allow_http=True,
            )

    def test_vault_id_direct_assignment_blocked(self) -> None:
        c = self._client(self.VID)
        c.put("heads/a.enc", b"1")
        # 构造后篡改属性不能改变已固定的根路径（路径在 __init__ 固化）
        c.vault_id = "../escape"
        with self.assertRaises(AttributeError):
            c.root_dir = "../escape"  # 只读 property，不可篡改
        self.assertEqual(c.get("heads/a.enc"), b"1")
        with self.assertRaises(transport.WebDAVError):
            c.set_vault("../escape")
        with self.assertRaises(transport.WebDAVError):
            c.set_vault("not-a-uuid")

    def _root_client(self) -> transport.WebDAVClient:
        """root_dir="" 客户端：rel 路径相对 base_url 根（目录浏览用）。"""
        return transport.WebDAVClient(
            f"http://127.0.0.1:{self.port}/dav/",
            "u", "p", root_dir="", allow_http=True, timeout=5,
        )

    def test_root_dir_client_layout(self) -> None:
        c = transport.WebDAVClient(
            f"http://127.0.0.1:{self.port}/dav/", "u", "p",
            root_dir="my vault/memo", allow_http=True, timeout=5,
        )
        self.assertEqual(c.root_dir, "my vault/memo")
        c.mkcol_root()
        c.put("heads/a.enc", b"1")
        self.assertIn(
            "/dav/my%20vault/memo/heads/a.enc", FakeWebDAV.store
        )
        # vault_id 与 root_dir 互斥
        with self.assertRaises(transport.WebDAVError):
            transport.WebDAVClient(
                f"http://127.0.0.1:{self.port}/dav/", "u", "p",
                vault_id=self.VID, root_dir="x", allow_http=True,
            )
        for bad in ("a//b", "a/./b", "../x", "a\\b", "a\x01b"):
            with self.assertRaises(transport.WebDAVError, msg=bad):
                transport.WebDAVClient(
                    f"http://127.0.0.1:{self.port}/dav/", "u", "p",
                    root_dir=bad, allow_http=True,
                )

    def test_list_dir_root_and_nested(self) -> None:
        c = self._root_client()
        c.put("memo-app/heads/a.enc", b"1")
        c.put("other/f.txt", b"2")
        root_entries = {e.rel: e for e in c.list_dir("") if e.rel}
        self.assertTrue(root_entries["memo-app"].is_collection)
        self.assertTrue(root_entries["other"].is_collection)
        sub = {e.rel: e for e in c.list_dir("memo-app") if e.rel}
        self.assertIn("heads", sub)
        self.assertTrue(sub["heads"].is_collection)
        # Depth:1 只到直接子项，不含更深的文件
        self.assertNotIn("heads/a.enc", sub)

    def test_jianguoyun_uses_direct_connection_with_system_proxy_fallback(self):
        import urllib.error
        import urllib.request
        proxy = unittest.mock.Mock()
        direct = unittest.mock.Mock()
        with unittest.mock.patch.object(urllib.request, "build_opener", side_effect=[proxy, direct]) as build:
            c = transport.WebDAVClient("https://dav.jianguoyun.com/dav/", "u", "p")
            self.assertEqual(build.call_args.args[0].proxies, {})
        req = urllib.request.Request(c.base_url, method="PROPFIND")
        c._open(req)
        direct.open.assert_called_once()
        proxy.open.assert_not_called()
        direct.open.side_effect = urllib.error.URLError("unreachable")
        c._open(req)
        proxy.open.assert_called_once()
        self.assertIs(c._opener, proxy)

    def test_http_error_does_not_retry_on_proxy(self):
        import urllib.error
        c = transport.WebDAVClient("https://dav.jianguoyun.com/dav/", "u", "p")
        c._opener = unittest.mock.Mock()
        c._fallback_opener = unittest.mock.Mock()
        c._opener.open.side_effect = urllib.error.HTTPError(c.base_url, 401, "Unauthorized", {}, None)
        with self.assertRaises(urllib.error.HTTPError):
            c._open(unittest.mock.Mock())
        c._fallback_opener.open.assert_not_called()

    def test_create_dir_encodes_path_and_sends_mkcol(self):
        c = self._root_client()
        with unittest.mock.patch.object(c, "_request_url") as request:
            c.create_dir("我的坚果云/new folder")
            request.assert_called_once_with("MKCOL", c.base_url + "%E6%88%91%E7%9A%84%E5%9D%9A%E6%9E%9C%E4%BA%91/new%20folder/")
        for bad in ("", "../x", "a//b", "a\\b"):
            with self.assertRaises(transport.WebDAVError):
                c.create_dir(bad)
        with self.assertRaises(transport.WebDAVError):
            c.create_dir("err409")

    def test_list_dir_rejects_bad_paths(self) -> None:
        c = self._root_client()
        for bad in ("a//b", "a/./b", "../x", "a\\b", "a\x01b"):
            with self.assertRaises(transport.WebDAVError, msg=bad):
                c.list_dir(bad)

    def test_href_pseudo_prefix_rejected(self) -> None:
        c = self._client(self.VID)
        # /x/dav/... 伪前缀必须拒绝（锚定 vault 前缀而非 find /dav/）
        body = (
            b'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
            b'<d:response><d:href>/x/dav/memo-sync/' + self.VID.encode() +
            b'/heads/evil.enc</d:href>'
            b'<d:propstat><d:prop><d:resourcetype/></d:prop>'
            b'<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>'
            b'</d:multistatus>'
        )
        self.assertEqual(c.parse_multistatus(body), [])


if __name__ == "__main__":
    unittest.main()
