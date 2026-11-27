"""固定源、有界响应；网络写操作绝不自动重试。"""
import json
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class TransportError(RuntimeError):
    def __init__(self, category, status=None, uncertain=False):
        self.category, self.status, self.uncertain = category, status, uncertain
        super().__init__("外部请求失败：" + category + ("（HTTP " + str(status) + "）" if status else ""))


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class HTTPClient:
    def __init__(self, base_url, timeout=30, max_bytes=8*1024*1024, max_retries=2,
                 allow_loopback_http=False, retry_delay=0.2):
        parsed = urlsplit(base_url)
        if (parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname
                or not (parsed.scheme == "https" or (allow_loopback_http and parsed.scheme == "http"
                        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}))):
            raise TransportError("unsafe_origin")
        self.base_url = base_url.rstrip("/") + "/"
        self.origin = (parsed.scheme, parsed.netloc)
        self.timeout, self.max_bytes, self.max_retries = timeout, max_bytes, max_retries
        self.retry_delay = retry_delay
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, query=None, headers=None, body=None):
        method = method.upper()
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or path.startswith("//") or ".." in parsed.path.split("/"):
            raise TransportError("unsafe_path")
        url = self.base_url + parsed.path.lstrip("/")
        params = dict(parse_qsl(parsed.query))
        params.update(query or {})
        if params:
            url += "?" + urlencode(params)
        attempts = self.max_retries + 1 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                request = Request(url, data=body, headers={"Accept": "application/json", **(headers or {})}, method=method)
                with self.opener.open(request, timeout=self.timeout) as response:
                    raw = response.read(self.max_bytes + 1)
                    if len(raw) > self.max_bytes:
                        raise TransportError("response_limit", uncertain=method != "GET")
                    return Response(response.status, dict(response.headers), raw)
            except HTTPError as exc:
                status = exc.code
                exc.close()
                retry = method == "GET" and status in {408, 429, 500, 502, 503, 504}
                if retry and attempt + 1 < attempts:
                    time.sleep(self.retry_delay * (attempt + 1)); continue
                category = "redirect_rejected" if 300 <= status < 400 else "http"
                raise TransportError(category, status, uncertain=method != "GET" and (status >= 500 or status in {408, 429})) from None
            except (URLError, TimeoutError, OSError):
                if method == "GET" and attempt + 1 < attempts:
                    time.sleep(self.retry_delay * (attempt + 1)); continue
                raise TransportError("network", uncertain=method != "GET") from None

    def json(self, method, path, query=None, headers=None, payload=None):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        response = self.request(method, path, query, {**(headers or {}), "Content-Type": "application/json; charset=utf-8"}, body)
        try:
            return json.loads(response.body.decode("utf-8-sig"))
        except (ValueError, UnicodeError):
            raise TransportError("invalid_json", response.status, uncertain=method.upper() != "GET") from None
