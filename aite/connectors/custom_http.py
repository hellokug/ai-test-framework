"""Generic HTTP connector driven by jinja2 templates.

Config example::

    target:
      type: custom_http
      base_url: https://api.example.com
      template:
        method: POST
        path: /v1/ask            # appended to base_url (or full url in `url`)
        body: |
          {"question": {{ case.input | tojson }}, "lang": "zh"}
      extract: data.answer        # dot-path into the response JSON

``{{ case }}`` is the TestCase; ``{{ options }}`` the InvokeOptions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import httpx
from jinja2 import Environment, StrictUndefined

from aite.connectors.base import Stopwatch, retry_async, truncate_error
from aite.core.interfaces import TargetSystem
from aite.core.registry import register_connector
from aite.core.types import TargetResponse

if TYPE_CHECKING:
    from aite.core.loader import TargetConfig
    from aite.core.types import InvokeOptions, TestCase


def extract_path(data: Any, path: str | None) -> Any:
    """Dot-path extraction with list indexing (``items.0.text``). Returns None if missing."""
    if not path:
        return data
    cur = data
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        else:
            return None
    return cur


@register_connector("custom_http")
class CustomHttpConnector(TargetSystem):
    name: ClassVar[str] = "custom_http"

    def __init__(self, config: TargetConfig) -> None:
        self.config = config
        self.template = config.template or {}
        self.jinja = Environment(undefined=StrictUndefined, autoescape=False)
        self.jinja.filters["tojson"] = _tojson
        headers = dict(config.headers or {})
        key = config.effective_api_key
        if key and not any("auth" in h.lower() for h in headers):
            headers["Authorization"] = f"Bearer {key}"
        self._client = httpx.AsyncClient(
            timeout=config.timeout_s, headers=headers,
            base_url=(config.base_url or "").rstrip("/") or None,
        )

    def _render(self, case: TestCase, options: InvokeOptions) -> dict[str, Any]:
        ctx = {"case": case.model_dump(), "options": options.model_dump(),
               "params": self.config.params or {}}
        method = (self.jinja.from_string(self.template.get("method", "POST")).render(**ctx)).strip().upper()
        url_tmpl = self.template.get("url") or self.template.get("path", "")
        url = self.jinja.from_string(url_tmpl).render(**ctx)
        headers: dict[str, str] = {}
        if self.template.get("headers"):
            import json
            rendered = self.jinja.from_string(self.template["headers"]).render(**ctx)
            headers.update(json.loads(rendered))
        body = None
        if self.template.get("body"):
            body = self.jinja.from_string(self.template["body"]).render(**ctx)
        return {"method": method, "url": url, "headers": headers, "body": body}

    async def invoke(self, case: TestCase, options: InvokeOptions) -> TargetResponse:
        watch = Stopwatch()
        try:
            req_spec = self._render(case, options)

            async def call():
                kwargs: dict[str, Any] = {"headers": req_spec["headers"] or None}
                if req_spec["body"] is not None:
                    body = req_spec["body"]
                    if isinstance(body, str) and body.strip().startswith(("{", "[")):
                        kwargs["content"] = body
                        kwargs["headers"] = {**(req_spec["headers"] or {}),
                                             "Content-Type": "application/json"}
                    elif isinstance(body, str):
                        kwargs["content"] = body
                    else:
                        kwargs["json"] = body
                return await self._client.request(req_spec["method"], req_spec["url"], **kwargs)

            resp = await retry_async(call, max_retries=self.config.max_retries)
            resp.raise_for_status()
            watch.mark_first_token()
            try:
                payload: Any = resp.json()
            except ValueError:
                payload = resp.text
            output = extract_path(payload, self.config.extract)
            return TargetResponse(
                output=output if output is not None else payload,
                timings=watch.finish(),
                raw={"status_code": resp.status_code},
            )
        except Exception as exc:  # noqa: BLE001
            return TargetResponse(error=truncate_error(exc))

    async def close(self) -> None:
        await self._client.aclose()


def _tojson(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)
