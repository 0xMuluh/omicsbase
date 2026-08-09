"""Controlled open-world data acquisition into an OmicsBase study."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.config import settings
from app.services.file_inspector import inspect_file
from app.services.runner import run_command_sync
from app.services.study_manifest import build_study_manifest

logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 60
ALLOWED_URL_SCHEMES = {"http", "https"}
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
BLOCKED_METADATA_HOSTS = {
    "metadata",
    "metadata.aws.internal",
    "metadata.google.internal",
}


def _validate_public_http_url(url: str) -> None:
    """Reject URL targets that are not unambiguously public HTTP(S) hosts."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL has an invalid host or port") from exc

    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
        raise ValueError("Only http/https URLs are allowed")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("URL is missing a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are not allowed")

    hostname = parsed.hostname.rstrip(".").lower()
    if (
        hostname in BLOCKED_METADATA_HOSTS
        or hostname == "localhost"
        or hostname.endswith(".localhost")
    ):
        raise ValueError("URL host is not a public destination")

    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    try:
        addresses.add(ipaddress.ip_address(hostname.split("%", 1)[0]))
    except ValueError:
        try:
            resolved = socket.getaddrinfo(
                hostname.encode("idna").decode("ascii"),
                port,
                type=socket.SOCK_STREAM,
            )
        except (socket.gaierror, UnicodeError) as exc:
            raise ValueError("URL host could not be resolved") from exc
        for item in resolved:
            candidate = str(item[4][0]).split("%", 1)[0]
            try:
                addresses.add(ipaddress.ip_address(candidate))
            except ValueError as exc:
                raise ValueError("URL host resolved to an invalid address") from exc

    if not addresses:
        raise ValueError("URL host did not resolve to an address")
    if any(not address.is_global for address in addresses):
        raise ValueError("URL host resolves to a non-public address")


class _PublicOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Apply the same public-network policy to every redirect hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            _validate_public_http_url(newurl)
        except ValueError as exc:
            raise urllib.error.URLError(f"Unsafe redirect blocked: {exc}") from exc
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Known package datasets the agent may import without free-form network/write R.
PACKAGE_DATASETS: dict[tuple[str, str], dict[str, Any]] = {
    ("phyloseq", "GlobalPatterns"): {
        "domain_hint": "microbiome",
        "exports": ["feature_table", "metadata"],
        "description": "GlobalPatterns 16S OTU table + sample metadata",
    },
    ("phyloseq", "soilrep"): {
        "domain_hint": "microbiome",
        "exports": ["feature_table", "metadata"],
        "description": "soilrep microbiome example",
    },
}


def list_importable_datasets() -> list[dict[str, Any]]:
    return [
        {
            "package": package,
            "dataset": dataset,
            **meta,
        }
        for (package, dataset), meta in sorted(PACKAGE_DATASETS.items())
    ]


def import_package_dataset(
    db,
    project,
    *,
    package: str,
    dataset: str,
    role: str = "auto",
) -> dict[str, Any]:
    """Export a known R package dataset into project uploads and refresh the study contract."""
    package = package.strip()
    dataset = dataset.strip()
    key = (package, dataset)
    if key not in PACKAGE_DATASETS:
        known = ", ".join(f"{p}::{d}" for p, d in PACKAGE_DATASETS)
        return {
            "status": "error",
            "error": f"Dataset {package}::{dataset} is not in the allowlist. Known: {known}",
        }

    upload_dir = _upload_dir(project.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    prefix = SAFE_NAME_RE.sub("_", f"{package}_{dataset}")

    with tempfile.TemporaryDirectory(prefix="omicsbase-import-") as tmp:
        tmp_path = Path(tmp)
        script = tmp_path / "export.R"
        script.write_text(_package_export_script(package, dataset, tmp_path), encoding="utf-8")
        try:
            success, run_output = run_command_sync(
                ["Rscript", script.name],
                cwd=str(tmp_path),
                timeout=120,
            )
            if not success:
                return {
                    "status": "error",
                    "error": f"Failed to import dataset: {run_output[:500]}",
                }
        except FileNotFoundError:
            return {"status": "error", "error": "Rscript is not available in this environment"}

        exported = sorted(tmp_path.glob(f"{prefix}*.csv")) + sorted(tmp_path.glob(f"{prefix}*.tsv"))
        if not exported:
            return {"status": "error", "error": "R export produced no CSV/TSV files"}

        registered = []
        for path in exported:
            dest = upload_dir / path.name
            dest.write_bytes(path.read_bytes())
            file_role = role if role != "auto" else _role_from_export_name(path.name)
            record = _register_uploaded_file(db, project, dest, file_role=file_role)
            if record.get("status") == "error":
                return record
            registered.append(record)

    manifest = _refresh_manifest(db, project)
    return {
        "status": "ok",
        "package": package,
        "dataset": dataset,
        "files": registered,
        "study_manifest": {
            "status": manifest.get("status"),
            "domain": manifest.get("domain"),
            "summary": manifest.get("summary"),
            "validations": manifest.get("validations"),
        },
    }


def fetch_url_into_study(
    db,
    project,
    *,
    url: str,
    filename: str | None = None,
    role: str = "auto",
) -> dict[str, Any]:
    """Download a remote file into project uploads (size- and scheme-limited)."""
    target_url = url.strip()
    try:
        _validate_public_http_url(target_url)
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    parsed = urlparse(target_url)

    safe_name = Path(filename or Path(parsed.path).name or "download.bin").name
    safe_name = SAFE_NAME_RE.sub("_", safe_name) or "download.bin"
    upload_dir = _upload_dir(project.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / safe_name

    try:
        request = urllib.request.Request(
            target_url,
            headers={"User-Agent": "OmicsBaseAgent/1.0"},
            method="GET",
        )
        opener = urllib.request.build_opener(_PublicOnlyRedirectHandler())
        with opener.open(request, timeout=DOWNLOAD_TIMEOUT_S) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_DOWNLOAD_BYTES:
                return {
                    "status": "error",
                    "error": f"Remote file exceeds {MAX_DOWNLOAD_BYTES} byte limit",
                }
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(1024 * 64)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    return {
                        "status": "error",
                        "error": f"Remote file exceeds {MAX_DOWNLOAD_BYTES} byte limit",
                    }
                chunks.append(chunk)
            dest.write_bytes(b"".join(chunks))
    except urllib.error.HTTPError as exc:
        return {"status": "error", "error": f"HTTP {exc.code}: {exc.reason}"}
    except urllib.error.URLError as exc:
        return {"status": "error", "error": f"URL fetch failed: {exc.reason}"}
    except Exception as exc:
        logger.exception("fetch_url_into_study failed: %s", exc)
        return {"status": "error", "error": str(exc)}

    record = _register_uploaded_file(db, project, dest, file_role=role)
    if record.get("status") == "error":
        dest.unlink(missing_ok=True)
        return record
    manifest = _refresh_manifest(db, project)
    return {
        "status": "ok",
        "url": target_url,
        "file": record,
        "study_manifest": {
            "status": manifest.get("status"),
            "domain": manifest.get("domain"),
            "summary": manifest.get("summary"),
            "validations": manifest.get("validations"),
        },
    }


def _upload_dir(project_id: str) -> Path:
    return Path(settings.projects_dir) / "uploads" / str(project_id)


def _register_uploaded_file(db, project, path: Path, *, file_role: str) -> dict[str, Any]:
    from app.models.project import UploadedFile

    summary = inspect_file(str(path))
    detected_format = summary.get("format", "unknown")
    if detected_format == "error":
        return {
            "status": "error",
            "error": f"Could not inspect {path.name}: {summary.get('error', 'unknown error')}",
        }
    if file_role == "auto":
        # Roles are agent-assigned during planning; "auto" has no heuristic.
        file_role = "other"

    # Replace prior upload with same name for this project.
    existing = (
        db.query(UploadedFile)
        .filter(
            UploadedFile.project_id == project.id,
            UploadedFile.original_name == path.name,
        )
        .first()
    )
    if existing:
        db.delete(existing)
        db.commit()

    uploaded = UploadedFile(
        project_id=project.id,
        file_role=file_role,
        original_name=path.name,
        detected_format=detected_format,
        file_summary=summary,
        file_path=str(path),
    )
    db.add(uploaded)
    db.commit()
    db.refresh(uploaded)
    return {
        "status": "ok",
        "id": str(uploaded.id),
        "name": uploaded.original_name,
        "role": uploaded.file_role,
        "format": uploaded.detected_format,
        "dimensions": (uploaded.file_summary or {}).get("dimensions"),
        "columns": ((uploaded.file_summary or {}).get("columns") or [])[:30],
    }


def _refresh_manifest(db, project) -> dict[str, Any]:
    from app.models.project import UploadedFile

    files = db.query(UploadedFile).filter(UploadedFile.project_id == project.id).all()
    project.files = files
    manifest = build_study_manifest(files)
    project.study_manifest = manifest
    db.commit()
    db.refresh(project)
    return manifest


def _role_from_export_name(name: str) -> str:
    lower = name.lower()
    if "metadata" in lower or "sample_data" in lower:
        return "metadata"
    if "tax" in lower:
        return "taxonomy"
    if "otu" in lower or "feature" in lower or "counts" in lower or "abundance" in lower:
        return "feature_table"
    return "other"


def _package_export_script(package: str, dataset: str, out_dir: Path) -> str:
    prefix = SAFE_NAME_RE.sub("_", f"{package}_{dataset}")
    out = str(out_dir).replace("\\", "/")
    return f"""
suppressPackageStartupMessages({{
  if (!requireNamespace("{package}", quietly = TRUE)) {{
    stop("Package {package} is not installed")
  }}
  library({package})
}})

data("{dataset}", package = "{package}", envir = environment())
obj <- get("{dataset}")
prefix <- "{prefix}"
outdir <- "{out}"

write_df <- function(df, path) {{
  df <- as.data.frame(df, stringsAsFactors = FALSE)
  utils::write.csv(df, path, row.names = TRUE)
}}

if (inherits(obj, "phyloseq")) {{
  otu <- as(phyloseq::otu_table(obj), "matrix")
  if (!phyloseq::taxa_are_rows(obj)) otu <- t(otu)
  write_df(otu, file.path(outdir, paste0(prefix, "_feature_table.csv")))
  meta <- as(phyloseq::sample_data(obj), "data.frame")
  write_df(meta, file.path(outdir, paste0(prefix, "_metadata.csv")))
  if (!is.null(phyloseq::tax_table(obj, errorIfNULL = FALSE))) {{
    tax <- as(phyloseq::tax_table(obj), "matrix")
    write_df(tax, file.path(outdir, paste0(prefix, "_taxonomy.csv")))
  }}
}} else if (is.data.frame(obj) || is.matrix(obj)) {{
  write_df(obj, file.path(outdir, paste0(prefix, "_table.csv")))
}} else {{
  stop(paste("Unsupported object class:", paste(class(obj), collapse = "/")))
}}
"""
