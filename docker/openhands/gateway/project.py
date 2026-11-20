"""Report rendering where the report's files live: in the Reports container or job.

Same answer as the notes engine's /api/projects/<id>/render, under
/api/omicsbase/projects/<id>/render. Status comes from the report viewer (report.py).
"""
import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from gateway.auth import require_user
from gateway.core import PROJECTS_ROOT, PROJECT_ID, ROIHU

router = APIRouter(tags=["project"])

def _report_dir(project_id: str, user_id) -> Path:
    """On Roihu a report is PROJECTS_ROOT/<id>; with several users, PROJECTS_ROOT/users/<user>/<id>."""
    if not PROJECT_ID.match(project_id):
        raise HTTPException(status_code=400, detail="Invalid project id")
    return PROJECTS_ROOT / project_id if ROIHU else PROJECTS_ROOT / "users" / str(user_id) / project_id


def _scaffold(project_dir: Path, project_id: str) -> None:
    """The starting site the notes engine creates for an empty report."""
    quarto_yml = project_dir / "_quarto.yml"
    if not quarto_yml.exists():
        quarto_yml.write_text(f"""project:
  type: website
  output-dir: _site

website:
  title: "OmicsBase Study: {project_id}"
  navbar:
    left:
      - href: index.qmd
        text: Overview

format:
  html:
    theme: cosmo
    toc: true
    code-fold: show
""")
    index_qmd = project_dir / "index.qmd"
    if not index_qmd.exists():
        index_qmd.write_text(f"""---
title: "Downstream Omics Study Report"
subtitle: "Study: {project_id}"
date: today
format:
  html:
    toc: true
---

## Overview

This publication-grade report is generated and maintained by OmicsBase.

```{{r}}
#| echo: true
#| warning: false
cat("R and Bioconductor runtime initialized.\\n")
```
""")


@router.post("/api/omicsbase/projects/{project_id}/render")
async def project_render(request: Request, project_id: str):
    project_dir = _report_dir(project_id, require_user(request))
    project_dir.mkdir(parents=True, exist_ok=True)
    if not (project_dir / "_quarto.yml").exists() and not list(project_dir.glob("*.qmd")):
        _scaffold(project_dir, project_id)
    proc = await asyncio.create_subprocess_exec(
        "quarto", "render", str(project_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(project_dir),
    )
    stdout, stderr = await proc.communicate()
    has_site = (project_dir / "_site" / "index.html").is_file()
    return JSONResponse({
        "success": proc.returncode == 0 and has_site,
        "exit_code": proc.returncode,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "has_site": has_site,
    })
