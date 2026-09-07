"""Serve the prebuilt OmicsBase frontend and inject the host theme bridge.

The source patch is applied and checked at build time. This module never edits
hashed assets or relies on minified JavaScript identifiers.
"""
import json
from functools import lru_cache
from pathlib import Path
import openhands.server.static
from starlette.responses import FileResponse, HTMLResponse, Response
from starlette.types import Scope

FRONTEND_CONTRACT = 1


def validate_frontend(directory: Path) -> None:
    marker = directory / "omicsbase-build.json"
    if not marker.is_file():
        raise RuntimeError("Missing OmicsBase frontend build. Run scripts/build_openhands.py and use its image.")
    metadata = json.loads(marker.read_text())
    if metadata.get("contract") != FRONTEND_CONTRACT:
        raise RuntimeError("Incompatible OmicsBase frontend contract; rebuild the OpenHands image.")


@lru_cache(maxsize=8)
def _embedded_html(index_path: str, index_mtime: int, theme_mtime: int) -> str:
    content = Path(index_path).read_text(encoding="utf-8")
    theme = Path(__file__).with_name("theme.js").read_text(encoding="utf-8")
    injection = '<script id="omicsbase-theme-bridge">' + theme + '</script>'
    return content.replace('</head>', injection + '</head>', 1) if '</head>' in content else injection + content


class OmicsBaseSPAStaticFiles(openhands.server.static.SPAStaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if isinstance(response, FileResponse) and str(response.path).endswith('index.html'):
            index = Path(response.path)
            theme = Path(__file__).with_name("theme.js")
            return HTMLResponse(_embedded_html(str(index), index.stat().st_mtime_ns, theme.stat().st_mtime_ns), status_code=response.status_code)
        return response


def install_frontend(directory: Path = Path('/app/frontend/build')) -> None:
    validate_frontend(directory)
    openhands.server.static.SPAStaticFiles = OmicsBaseSPAStaticFiles
