import time
from pathlib import Path
import yaml

def _inspect_project_report(pdir: Path) -> dict:
    project_title = "Omics Analysis Pipeline"
    ordered_chapters = []
    quarto_yml = pdir / "_quarto.yml"
    if quarto_yml.is_file():
        try:
            with open(quarto_yml, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            project_title = (
                cfg.get("website", {}).get("title")
                or cfg.get("book", {}).get("title")
                or cfg.get("title")
                or project_title
            )
            def extract_items(node):
                if isinstance(node, list):
                    for sub in node:
                        extract_items(sub)
                elif isinstance(node, dict):
                    href = node.get("href") or node.get("file")
                    text = node.get("text") or node.get("title")
                    if href and (href.endswith(".qmd") or href.endswith(".md")):
                        ordered_chapters.append({"file": href, "title": text})
                    for k in ("menu", "contents", "chapters", "left", "right"):
                        if k in node:
                            extract_items(node[k])
                elif isinstance(node, str) and (node.endswith(".qmd") or node.endswith(".md")):
                    ordered_chapters.append({"file": node, "title": None})

            extract_items(cfg.get("website", {}).get("navbar", {}))
            extract_items(cfg.get("website", {}).get("sidebar", {}))
            extract_items(cfg.get("book", {}).get("chapters", []))
            extract_items(cfg.get("chapters", []))
        except Exception:
            pass

    # If title not found from _quarto.yml, try markdown files (e.g. Statistical Analysis Plan or README)
    if project_title == "Omics Analysis Pipeline":
        for md_path in pdir.glob("*.md"):
            try:
                lines = md_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                for line in lines[:10]:
                    stripped = line.strip()
                    if stripped.startswith("# "):
                        candidate = stripped[2:].strip()
                        if candidate and len(candidate) > 3:
                            project_title = candidate
                            break
                if project_title != "Omics Analysis Pipeline":
                    break
            except Exception:
                pass
        if project_title == "Omics Analysis Pipeline":
            for md_path in pdir.glob("*Analysis Plan*.md"):
                project_title = md_path.stem
                break

    site_dir = pdir / "_site"
    has_site = (site_dir / "index.html").is_file()

    # Discover all .qmd files recursively (including pages/ and subdirectories)
    all_qmd = {}
    for p in sorted(pdir.rglob("*.qmd")):
        if "_site" in p.parts or ".git" in p.parts:
            continue
        rel_str = str(p.relative_to(pdir))
        all_qmd[rel_str] = p

    seen = set()
    chapters = []

    for item in ordered_chapters:
        f_raw = item["file"]
        qpath = all_qmd.get(f_raw)
        if not qpath:
            for rel, p in all_qmd.items():
                if rel.endswith(f_raw) or p.name == f_raw:
                    qpath = p
                    break
        if qpath and qpath not in seen:
            seen.add(qpath)
            rel_str = str(qpath.relative_to(pdir))
            title = item.get("title")
            if not title:
                try:
                    txt = qpath.read_text(encoding="utf-8", errors="ignore")
                    if txt.startswith("---"):
                        parts = txt.split("---", 2)
                        if len(parts) >= 3:
                            fm = yaml.safe_load(parts[1])
                            if isinstance(fm, dict):
                                title = fm.get("title")
                except Exception:
                    pass
            if not title:
                title = qpath.stem.replace("_", " ").title()

            rel_html = qpath.relative_to(pdir).with_suffix(".html")
            compiled = (site_dir / rel_html).is_file()
            if compiled:
                status = "completed"
            elif site_dir.is_dir() and (time.time() - site_dir.stat().st_mtime) < 30:
                status = "running"
            else:
                status = "queued"
            chapters.append({
                "name": rel_str,
                "title": title,
                "compiled": compiled,
                "status": status,
                "html_path": str(rel_html) if compiled else None,
                "type": "chapter"
            })

    # Any remaining .qmd files in project not in navbar/sidebar
    for rel_str, qpath in all_qmd.items():
        if qpath in seen:
            continue
        seen.add(qpath)
        title = None
        try:
            txt = qpath.read_text(encoding="utf-8", errors="ignore")
            if txt.startswith("---"):
                parts = txt.split("---", 2)
                if len(parts) >= 3:
                    fm = yaml.safe_load(parts[1])
                    if isinstance(fm, dict):
                        title = fm.get("title")
        except Exception:
            pass
        if not title:
            title = qpath.stem.replace("_", " ").title()
        rel_html = qpath.relative_to(pdir).with_suffix(".html")
        compiled = (site_dir / rel_html).is_file()
        if compiled:
            status = "completed"
        elif site_dir.is_dir() and (time.time() - site_dir.stat().st_mtime) < 30:
            status = "running"
        else:
            status = "queued"
        chapters.append({
            "name": rel_str,
            "title": title,
            "compiled": compiled,
            "status": status,
            "html_path": str(rel_html) if compiled else None,
            "type": "chapter"
        })

    # Discover R pipeline scripts alongside Quarto chapters
    scripts = []
    r_dir = pdir / "R"
    if r_dir.is_dir():
        for r_script in sorted(r_dir.glob("*.R")):
            stem = r_script.stem.replace("_", " ").title()
            rel_str = str(r_script.relative_to(pdir))
            log_file = pdir / "output" / f"{r_script.stem}.log"
            status = "queued"
            if log_file.is_file():
                try:
                    txt = log_file.read_text(errors="ignore")
                    if "Execution halted" in txt or "Error in " in txt or "Error: " in txt:
                        status = "failed"
                    elif (time.time() - log_file.stat().st_mtime) < 45:
                        status = "running"
                    else:
                        status = "completed"
                except Exception:
                    status = "completed"
            elif has_site:
                status = "completed"
            else:
                out_res = pdir / "output" / "results"
                if out_res.is_dir() and any(out_res.glob(f"*{r_script.stem}*.rds")):
                    status = "completed"
                elif out_res.is_dir() and any(out_res.glob("*.rds")):
                    status = "completed"

            scripts.append({
                "name": r_script.name,
                "title": stem,
                "path": rel_str,
                "status": status,
                "type": "script"
            })

    figures = []
    fig_dirs = [
        "results", "figures", "plots",
        "output/figures", "output/plots", "output/results", "output",
        "."
    ]
    for dname in fig_dirs:
        sub = pdir / dname
        if sub.is_dir():
            for ext in ("*.png", "*.jpg", "*.jpeg", "*.svg", "*.webp"):
                for f in sorted(sub.glob(ext)):
                    if f.name.startswith("."):
                        continue
                    rel = str(f.relative_to(pdir))
                    if not any(x["path"] == rel for x in figures):
                        figures.append({
                            "name": f.name,
                            "path": rel,
                            "size": f.stat().st_size
                        })

    tables = []
    tbl_dirs = [
        "results", "tables", "data",
        "output/results", "output/tables", "output",
        "."
    ]
    for dname in tbl_dirs:
        sub = pdir / dname
        if sub.is_dir():
            for ext in ("*.csv", "*.tsv", "*.xlsx", "*.rds"):
                for t in sorted(sub.glob(ext)):
                    if t.name.startswith("."):
                        continue
                    rel = str(t.relative_to(pdir))
                    if not any(x["path"] == rel for x in tables):
                        tables.append({
                            "name": t.name,
                            "path": rel,
                            "size": t.stat().st_size
                        })

    return {
        "project_title": project_title,
        "has_site": has_site,
        "chapters": chapters,
        "scripts": scripts,
        "figures": figures,
        "tables": tables
    }

