"""WebDAV 传输层（计划 §4.1/§6）：坚果云 HTTPS + Basic Auth（第三方应用密码）。

只用标准库 urllib，实现 PROPFIND(Depth:1/0)/GET/PUT/MKCOL。

安全约束：
- 始终校验证书（urllib 默认行为，不设 verify=False）；
- 认证信息不随跨主机重定向泄露（自定义 redirect handler 剥离 Authorization）；
- 响应体有上限；路径限于 vault 根（用户自选 root_dir 或旧版 memo-sync/<vault_id>/）；
- 不假定 MOVE/条件写/原子重命名/分页能力——阶段 0 实测后再依赖。
"""
from __future__ import annotations

import base64
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from . import protocol

DEFAULT_TIMEOUT = 30

PROPFIND_PROPS = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:">'
    "<d:prop><d:resourcetype/><d:getetag/><d:getcontentlength/>"
    "<d:displayname/></d:prop></d:propfind>"
)

DAV = "{DAV:}"


class WebDAVError(Exception):
    """传输层错误基类。status 为 HTTP 状态码（网络层错误为 0）。"""

    def __init__(self, message: str, status: int = 0, retry_after: float = 0.0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class AuthError(WebDAVError):
    """401/403：凭据失效或权限不足，引导用户处理，勿高频重试。"""


class NotFoundError(WebDAVError):
    pass


class RateLimitedError(WebDAVError):
    """429/限流；retry_after 秒后再试（来自 Retry-After 头）。"""


class PreconditionFailedError(WebDAVError):
    """412：条件写失败（If-None-Match/If-Match 不满足）。"""


class ServerError(WebDAVError):
    """5xx：可指数退避重试。"""


class QuotaError(WebDAVError):
    """507 Insufficient Storage：云端配额不足，需用户处理而非重试。"""


def _raise_for_status(code: int, retry_after: float = 0.0) -> None:
    if code in (401, 403):
        raise AuthError(f"WebDAV auth failed ({code})", code)
    if code == 404:
        raise NotFoundError("not found", code)
    if code == 412:
        raise PreconditionFailedError("precondition failed", code)
    if code == 429:
        raise RateLimitedError("rate limited", code, retry_after)
    if code == 507:
        raise QuotaError("remote quota exceeded", code)
    if code >= 500:
        raise ServerError(f"server error {code}", code)
    if code >= 400:
        raise WebDAVError(f"HTTP {code}", code)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """重定向时跨主机剥离 Authorization 头，防凭据泄露。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        # 仅 scheme+netloc 完全一致才保留 Authorization；
        # https->http 同主机降级也必须剥离（凭据会走明文）
        old = urllib.parse.urlsplit(req.full_url)
        new_u = urllib.parse.urlsplit(newurl)
        if (old.scheme, old.netloc) != (new_u.scheme, new_u.netloc):
            new.headers.pop("Authorization", None)
        return new


@dataclass
class RemoteEntry:
    href: str          # 原始 href
    rel: str           # 相对 vault 根的路径（"" 表示 vault 根本身）
    is_collection: bool
    etag: str | None = None
    size: int | None = None


class WebDAVClient:
    """单 vault 的 WebDAV 客户端。

    base_url 形如 https://dav.jianguoyun.com/dav/。
    vault 根目录两种来源（互斥）：
    - root_dir：用户自选远程目录（如 "我的坚果云/memo"），
      "" 表示 WebDAV 根；
    - vault_id（旧布局兼容）：固定 memo-sync/<vault_id>/。
    所有 rel_path 都是 vault 根内相对路径。
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        vault_id: str | None = None,
        root_dir: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_file: int = protocol.MAX_REMOTE_FILE,
        allow_http: bool = False,
    ) -> None:
        scheme = base_url.split("://", 1)[0].lower()
        if "?" in base_url or "#" in base_url:
            # 空 query/fragment 无法用 urlsplit 区分，直接拒绝字面字符
            raise WebDAVError("invalid base_url")
        parts = urllib.parse.urlsplit(base_url)
        # 拒绝把凭据写进 URL、query/fragment、缺 host
        if (
            parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or not parts.hostname
        ):
            raise WebDAVError("invalid base_url")
        if scheme != "https":
            # allow_http 仅用于本地测试：限定 loopback，防止误用到生产
            if not (allow_http and scheme == "http"
                    and parts.hostname in ("127.0.0.1", "localhost", "::1")):
                raise WebDAVError("坚果云 WebDAV 必须使用 HTTPS")
        if vault_id is not None and root_dir is not None:
            raise WebDAVError("vault_id and root_dir are mutually exclusive")
        # 私有化：构造后不可被赋值绕过校验（Basic Auth 防泄露）
        self._base_url = base_url.rstrip("/") + "/"
        self._legacy_vault = vault_id is not None and root_dir is None
        if root_dir is not None:
            self._root = self._validate_root_dir(root_dir)
            self.vault_id = None
        elif vault_id is not None:
            vault_id = vault_id.lower()
            if not protocol.is_uuid(vault_id):
                raise WebDAVError("invalid vault_id")
            self.vault_id = vault_id
            self._root = f"{protocol.REMOTE_ROOT}/{vault_id}"
        else:
            self.vault_id = None
            self._root = ""
        self._root_segs = [s for s in self._root.split("/") if s]
        self._auth = "Basic " + base64.b64encode(
            f"{username}:{password}".encode("utf-8")
        ).decode("ascii")
        self.timeout = timeout
        self.max_file = max_file
        self._opener = urllib.request.build_opener(_SafeRedirect)
        self._fallback_opener = None
        # 国内坚果云直连，避免绕经系统代理增加数秒延迟；其他 WebDAV
        # 保持系统代理设置。直连网络失败时仍可回退到系统代理。
        if parts.hostname.lower() == "dav.jianguoyun.com":
            self._fallback_opener = self._opener
            self._opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _SafeRedirect,
            )

    def _open(self, req):
        try:
            return self._opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError:
            # HTTP 错误说明已连到服务器，不切换网络重复请求。
            raise
        except urllib.error.URLError:
            if self._fallback_opener is None:
                raise
            self._opener = self._fallback_opener
            self._fallback_opener = None
            return self._opener.open(req, timeout=self.timeout)

    @staticmethod
    def _validate_root_dir(root_dir: str) -> str:
        """规范化用户自选远程目录：去首尾 /，逐段校验，返回 "" 或 a/b/c。

        不裁剪空白——" foo" 与 "foo" 是不同目录，静默别名会选错
        vault 位置；段首尾空白直接拒绝。
        """
        rel = root_dir.strip("/")
        if not rel:
            return ""
        segs = rel.split("/")
        for s in segs:
            if (
                not s
                or s != s.strip()
                or s in (".", "..")
                or "\\" in s
                or "\x00" in s
                or any(ord(c) < 0x20 for c in s)
            ):
                raise WebDAVError("invalid root_dir")
        return "/".join(segs)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def root_dir(self) -> str:
        """vault 根目录（base_url 相对路径，"" = WebDAV 根）。"""
        return self._root

    def set_vault(self, vault_id: str) -> None:
        if not self._legacy_vault:
            raise WebDAVError("client root is fixed at construction")
        vault_id = vault_id.lower()
        if not protocol.is_uuid(vault_id):
            raise WebDAVError("invalid vault_id")
        self.vault_id = vault_id
        self._root = f"{protocol.REMOTE_ROOT}/{vault_id}"
        self._root_segs = [protocol.REMOTE_ROOT, vault_id]

    # ---------- 内部 ----------

    def _mount_path(self) -> str:
        return urllib.parse.urlsplit(self._base_url).path.rstrip("/")

    def _vault_prefix(self) -> str:
        """vault 在服务端的路径前缀（如 /dav/memo-sync/<vid> 或 /dav/a/b）。"""
        root = "/".join(self._root_segs)
        if not root:
            return self._mount_path()
        return f"{self._mount_path()}/{root}"

    def _check_rel(self, rel_path: str) -> None:
        rel = rel_path.strip("/")
        if not rel:
            return
        for s in rel.split("/"):
            if (
                not s or s in (".", "..")
                or "\\" in s or "\x00" in s
                or any(ord(c) < 0x20 for c in s)
            ):
                raise WebDAVError("invalid remote path")

    def _url(self, rel_path: str) -> str:
        self._check_rel(rel_path)
        # base_url 已含挂载点（如 .../dav/），vault 前缀相对它是 root_segs
        parts = [
            urllib.parse.quote(s, safe="") for s in self._root_segs
        ]
        rel = urllib.parse.quote(rel_path.strip("/"), safe="/")
        if rel:
            parts.append(rel)
            return self._base_url + "/".join(parts)
        # 空 rel = vault 根集合本身：需要尾斜杠
        suffix = "/".join(parts)
        return self._base_url + (suffix + "/" if suffix else "")

    def _request(
        self,
        method: str,
        rel_path: str,
        *,
        data: bytes | None = None,
        headers: dict | None = None,
        depth: int | None = None,
    ) -> tuple[int, bytes, dict]:
        return self._request_url(
            method, self._url(rel_path),
            data=data, headers=headers, depth=depth,
        )

    def _request_url(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict | None = None,
        depth: int | None = None,
    ) -> tuple[int, bytes, dict]:
        hdrs = {"Authorization": self._auth}
        if headers:
            hdrs.update(headers)
        if depth is not None:
            hdrs["Depth"] = str(depth)
        req = urllib.request.Request(
            url, data=data, headers=hdrs, method=method
        )
        try:
            resp = self._open(req)
            return resp.status, self._read_capped(resp), dict(resp.headers)
        except urllib.error.HTTPError as e:
            retry = 0.0
            ra = e.headers.get("Retry-After") if e.headers else None
            if ra:
                try:
                    retry = float(ra)
                except ValueError:
                    retry = 0.0
            try:
                e.read(4096)
            except Exception:
                pass
            # 坚果云兼容：父级集合不存在时 GET/PROPFIND 返回
            # 409(AncestorsNotFound) 而非标准 404——按不存在处理
            if e.code == 409 and method in ("GET", "PROPFIND"):
                raise NotFoundError("not found", e.code) from e
            _raise_for_status(e.code, retry)
            # 3xx/1xx 等 _raise_for_status 未覆盖的码：统一包装为 WebDAVError，
            # 保证所有失败都可被调用方按 WebDAVError 处理
            raise WebDAVError(f"HTTP {e.code}", e.code) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise WebDAVError(f"network error: {type(e).__name__}") from e

    def _read_capped(self, resp) -> bytes:
        body = resp.read(self.max_file + 1)
        if len(body) > self.max_file:
            raise WebDAVError("remote file exceeds size limit")
        return body

    # ---------- WebDAV 操作 ----------

    def mkcol_root(self) -> None:
        """确保 vault 根目录（含各级父目录）存在；逐段 MKCOL 幂等。

        已存在（405）或坚果云的 409+存在性验证均视为成功。
        """
        for i in range(1, len(self._root_segs) + 1):
            url = (
                self._base_url
                + "/".join(
                    urllib.parse.quote(s, safe="")
                    for s in self._root_segs[:i]
                )
                + "/"
            )
            req = urllib.request.Request(
                url, method="MKCOL",
                headers={"Authorization": self._auth},
            )
            try:
                resp = self._open(req)
                resp.read(4096)
            except urllib.error.HTTPError as e:
                try:
                    e.read(4096)
                except Exception:
                    pass
                if e.code == 405:
                    continue
                if e.code == 409 and self._collection_exists(url):
                    continue
                _raise_for_status(e.code)
                raise WebDAVError(f"HTTP {e.code}", e.code) from e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                raise WebDAVError(
                    f"network error: {type(e).__name__}"
                ) from e

    def _collection_exists(self, url: str) -> bool:
        """对绝对 URL 做 Depth:0 PROPFIND，判断集合是否存在。"""
        req = urllib.request.Request(
            url,
            method="PROPFIND",
            data=PROPFIND_PROPS.encode("utf-8"),
            headers={
                "Authorization": self._auth,
                "Depth": "0",
                "Content-Type": "application/xml; charset=utf-8",
            },
        )
        try:
            resp = self._open(req)
            resp.read(4096)
            # 合规 PROPFIND 响应是 207 multistatus；其他 2xx 不认
            return resp.status == 207
        except urllib.error.HTTPError:
            return False
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def mkcol(self, rel_path: str) -> None:
        """创建集合；已存在（405/坚果云 409）视为成功。"""
        try:
            self._request("MKCOL", rel_path)
        except WebDAVError as e:
            if e.status == 405:
                return
            if e.status == 409 and self.exists(rel_path):
                return
            raise

    def propfind(
        self, rel_path: str, *, depth: int = 1
    ) -> list[RemoteEntry]:
        """PROPFIND 列出集合内容；rel 为 vault 根内相对路径。"""
        self._check_rel(rel_path)
        _, body, _ = self._request(
            "PROPFIND",
            rel_path,
            data=PROPFIND_PROPS.encode("utf-8"),
            headers={"Content-Type": "application/xml; charset=utf-8"},
            depth=depth,
        )
        return self.parse_multistatus(body)

    def create_dir(self, dir_path: str) -> None:
        """Create one directory under the WebDAV mount; never create parents."""
        rel = self._validate_root_dir(dir_path)
        if not rel:
            raise WebDAVError("folder name is required")
        url = self._base_url + "/".join(
            urllib.parse.quote(s, safe="") for s in rel.split("/")
        ) + "/"
        # Do not treat 405/conflict as successful creation of a new directory.
        self._request_url("MKCOL", url)

    def list_dir(self, dir_path: str) -> list[RemoteEntry]:
        """PROPFIND Depth:1 列出 base_url 根下的目录（远程目录选择器用）。

        dir_path 相对 base_url 根（"" = 根目录）；返回条目 rel
        相对 dir_path（直接子项名称，目录无尾斜杠）。
        """
        # 与 _validate_root_dir 同强度：拒绝空段/./../控制字符，
        # 避免 URL 规范化与 anchor 不一致导致静默空列表
        rel = self._validate_root_dir(dir_path)
        segs = rel.split("/") if rel else []
        url = self._base_url + "/".join(
            urllib.parse.quote(s, safe="") for s in segs
        )
        if segs:
            url += "/"
        _, body, _ = self._request_url(
            "PROPFIND",
            url,
            data=PROPFIND_PROPS.encode("utf-8"),
            headers={"Content-Type": "application/xml; charset=utf-8"},
            depth=1,
        )
        anchor = self._mount_path()
        if rel:
            anchor += "/" + rel
        return self.parse_multistatus(body, anchor=anchor)

    def exists(self, rel_path: str) -> bool:
        # Depth:0 成功应返回恰好自身一条 entry；
        # 服务器整体 404 或 207+404-propstat 两种形式都判不存在
        try:
            return len(self.propfind(rel_path, depth=0)) > 0
        except NotFoundError:
            return False

    def get(self, rel_path: str) -> bytes:
        _, body, _ = self._request("GET", rel_path)
        return body

    def put(
        self,
        rel_path: str,
        data: bytes,
        *,
        create_only: bool = False,
    ) -> None:
        """上传对象。create_only=True 时带 If-None-Match: *。

        若服务端不支持条件写而返回成功，调用方需自行 GET 校验
        同路径内容是否为同一批次（计划 §4.2）。
        """
        headers = {"Content-Type": "application/octet-stream"}
        if create_only:
            headers["If-None-Match"] = "*"
        self._request("PUT", rel_path, data=data, headers=headers)

    # ---------- XML 解析 ----------

    def parse_multistatus(
        self, body: bytes, *, anchor: str | None = None
    ) -> list[RemoteEntry]:
        """解析 PROPFIND 207 响应，返回相对 anchor 的路径条目。

        anchor 缺省为 vault 前缀；list_dir 传入被列目录的绝对路径。
        """
        anchor = self._vault_prefix() if anchor is None else anchor
        # 拒绝 DTD/实体声明：远端 XML 不可信，ElementTree 不解析外部实体
        # 但内部实体仍有放大风险，直接拒绝（body 已有 16MB 上限）
        upper = body.upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            raise WebDAVError("PROPFIND response contains DTD")
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            raise WebDAVError("invalid PROPFIND XML response") from e
        if root.tag.rsplit("}", 1)[-1] != "multistatus":
            raise WebDAVError("unexpected PROPFIND response root")
        entries: list[RemoteEntry] = []
        saw_response = False
        for resp_node in root.iter(f"{DAV}response"):
            saw_response = True
            href_el = resp_node.find(f"{DAV}href")
            if href_el is None or not href_el.text:
                # RFC 要求 response 必须有 href；缺失说明响应畸形，不能静默丢
                raise WebDAVError("PROPFIND response missing href")
            rel = self._href_to_rel(href_el.text.strip(), anchor)
            if rel is None:
                continue
            # 仅保留含 2xx propstat 的 response：
            # RFC 允许服务器对不存在资源返回 207+404 propstat，
            # 不过滤会产生"幻影条目"导致 exists() 误判
            good_prop = None
            for ps in resp_node.findall(f"{DAV}propstat"):
                status_el = ps.find(f"{DAV}status")
                st = status_el.text if status_el is not None else ""
                parts = st.split()
                ok = (
                    len(parts) >= 2
                    and parts[1].isdigit()
                    and 200 <= int(parts[1]) < 300
                )
                if ok:
                    prop_el = ps.find(f"{DAV}prop")
                    if prop_el is not None:
                        good_prop = prop_el
                        break
            if good_prop is None:
                continue
            # collection 是 resourcetype 的子节点，不是 prop 的直接子节点
            is_coll = (
                good_prop.find(f"{DAV}resourcetype/{DAV}collection")
                is not None
            )
            etag = None
            size = None
            et = good_prop.find(f"{DAV}getetag")
            if et is not None and et.text:
                etag = et.text.strip().strip('"')
            cl = good_prop.find(f"{DAV}getcontentlength")
            if cl is not None and cl.text:
                try:
                    size = int(cl.text)
                except ValueError:
                    size = None
                if size is not None and size < 0:
                    size = None
            entries.append(RemoteEntry(href_el.text.strip(), rel, is_coll, etag, size))
        if not saw_response:
            # 无 response 的 multistatus 多半是命名空间不符/异常响应，
            # 不能当成"空目录"放行（可能触发误初始化）
            raise WebDAVError("PROPFIND returned no responses")
        return entries

    def _href_to_rel(self, href: str, anchor: str) -> str | None:
        """href -> 相对 anchor 的路径；越界/穿越返回 None。

        href 必须位于 anchor 锚定的服务端前缀之下——
        防止 "/x/dav/..." 这类伪前缀绕过。
        """
        try:
            parts = urllib.parse.urlsplit(href)
        except ValueError:
            return None
        # 绝对 URI 必须与本客户端同源；协议相对 //host/path 也拒绝
        if parts.netloc:
            base = urllib.parse.urlsplit(self._base_url)
            if (parts.scheme, parts.netloc) != (base.scheme, base.netloc):
                return None
        path = urllib.parse.unquote(parts.path)
        prefix = anchor
        if path == prefix:
            return ""
        if not path.startswith(prefix + "/"):
            return None
        rel = path[len(prefix) + 1:].strip("/")
        if ".." in rel.split("/") or "\\" in rel or "\x00" in rel:
            return None
        if any(ord(ch) < 0x20 for ch in rel):
            return None
        return rel
