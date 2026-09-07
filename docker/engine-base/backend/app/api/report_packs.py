"""Read-only discovery of administrator-approved adaptive ReportPacks."""

from fastapi import APIRouter, HTTPException

from app.services.report_pack import ReportPackError
from app.services.spawner import list_report_packs


router = APIRouter(prefix="/api/report-packs", tags=["report-packs"])


@router.get("")
def report_pack_catalog():
    """List safe pack IDs and capabilities without exposing server paths."""
    try:
        return {"report_packs": list_report_packs()}
    except ReportPackError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
