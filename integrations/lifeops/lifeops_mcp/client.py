"""Fixed-origin JSON client. Never follow an auth redirect with a service token."""

import json
import httpx
from fastmcp.exceptions import ToolError


class ApiClient:
    def __init__(self, name, base_url, header, token, *, transport=None, max_bytes=8_000_000):
        self.name, self.base_url = name, base_url
        self.headers = {header: token, "Accept": "application/json"}
        self.transport, self.max_bytes = transport, max_bytes

    async def request(self, method, path, *, body=None, params=None):
        if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path:
            raise ToolError("Invalid API path")
        try:
            async with httpx.AsyncClient(
                transport=self.transport, follow_redirects=False,
                timeout=httpx.Timeout(30, connect=5), trust_env=False,
            ) as client:
                async with client.stream(
                    method, self.base_url + path, headers=self.headers,
                    json=body, params=params,
                ) as response:
                    if response.is_redirect or response.status_code in (401, 403):
                        raise ToolError(f"{self.name}: authentication/route rejected; check service credential and machine allowlist")
                    if response.status_code >= 400:
                        raise ToolError(f"{self.name}: HTTP {response.status_code}; upstream error body withheld")
                    media = response.headers.get("content-type", "").split(";")[0].lower()
                    if media != "application/json" and not media.endswith("+json"):
                        raise ToolError(f"{self.name}: expected JSON; received a non-JSON response")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > self.max_bytes:
                            raise ToolError(f"{self.name}: response exceeds byte limit; narrow the query")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ToolError(f"{self.name}: expected a JSON object")
            if result.get("ok") is False:
                raise ToolError(f"{self.name}: application rejected request; inspect the upstream service")
            return result
        except httpx.RequestError:
            # Do not expose request URLs, headers or exception strings.
            raise ToolError(f"{self.name}: network failure; a write may already have taken effect") from None
        except (json.JSONDecodeError, UnicodeError):
            raise ToolError(f"{self.name}: invalid JSON response") from None
