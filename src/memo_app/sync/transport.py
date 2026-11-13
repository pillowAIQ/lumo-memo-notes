"""WebDAV 传输层（计划 §4.1/§6）：坚果云 HTTPS + Basic Auth（第三方应用密码）。

只用标准库 urllib，实现 PROPFIND(Depth:1/0)/GET/PUT/MKCOL。

安全约束：
- 始终校验证书（urllib 默认行为，不设 verify=False）；
- 认证信息不随跨主机重定向泄露（自定义 redirect handler 剥离 Authorization）；
- 响应体有上限；路径限于 memo-sync/<vault_id>/ 根；
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

    base_url 形如 https://dav.jianguoyun.com/dav/；
    vault_id 绑定后，所有 rel_path 都是 vault 根内相对路径，
    实际 URL 前缀为 <base>/memo-sync/<vault_id>/。
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        vault_id: str | None = None,
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
        # 私有化：构造后不可被赋值绕过校验（Basic Auth 防泄露）
        self._base_url = base_url.rstrip("/") + "/"
        if vault_id is not None:
            vault_id = vault_id.lower()
            if not protocol.is_uuid(vault_id):
                raise WebDAVError("invalid vault_id")
        self.vault_id = vault_id
        self._auth = "Basic " + base64.b64encode(
            f"{username}:{password}".encode("utf-8")
        ).decode("ascii")
        self.timeout = timeout
        self.max_file = max_file
        self._opener = urllib.request.build_opener(_SafeRedirect)

    @property
    def base_url(self) -> str:
        return self._base_url

    def set_vault(self, vault_id: str) -> None:
        vault_id = vault_id.lower()
        if not protocol.is_uuid(vault_id):
            raise WebDAVError("invalid vault_id")
        self.vault_id = vault_id

    # ---------- 内部 ----------

    def _vault_prefix(self) -> str:
        """vault 在服务端的路径前缀（如 /dav/memo-sync/<vid>）。

        每次使用都校验 vault_id，防止属性被直接赋值绕过 set_vault。
        """
        if not protocol.is_uuid(self.vault_id):
            raise WebDAVError("invalid vault_id")
        mount = urllib.parse.urlsplit(self._base_url).path.rstrip("/")
        return f"{mount}/{protocol.REMOTE_ROOT}/{self.vault_id}"

    def _check_rel(self, rel_path: str) -> None:
        rel = rel_path.strip("/")
        if ".." in rel.split("/") or "\\" in rel or "\x00" in rel:
            raise WebDAVError("invalid remote path")

    def _url(self, rel_path: str) -> str:
        self._check_rel(rel_path)
        # base_url 已含挂载点（如 .../dav/），vault 前缀相对它是 memo-sync/<vid>/
        if not protocol.is_uuid(self.vault_id):
            raise WebDAVError("invalid vault_id")
        return (
            self._base_url
            + f"{protocol.REMOTE_ROOT}/{self.vault_id}/"
            + urllib.parse.quote(rel_path.strip("/"), safe="/")
        )

    def _request(
        self,
        method: str,
        rel_path: str,
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
            self._url(rel_path), data=data, headers=hdrs, method=method
        )
        try:
            resp = self._opener.open(req, timeout=self.timeout)
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

    def mkcol(self, rel_path: str) -> None:
        """创建集合；已存在（405）视为成功。"""
        try:
            self._request("MKCOL", rel_path)
        except WebDAVError as e:
            if e.status == 405:
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

    def parse_multistatus(self, body: bytes) -> list[RemoteEntry]:
        """解析 PROPFIND 207 响应，返回 vault 根内相对路径的条目。"""
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
            rel = self._href_to_vault_rel(href_el.text.strip())
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

    def _href_to_vault_rel(self, href: str) -> str | None:
        """href -> vault 根内相对路径；越界/穿越返回 None。

        href 必须位于本客户端 vault 的服务端前缀之下——
        锚定匹配，防止 "/x/dav/memo-sync/<vid>/..." 这类伪前缀绕过。
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
        prefix = self._vault_prefix()
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
