"""Admin-only DSAT Alert Analyzer APIs (RBAC via /gldash session)."""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.auth.session import read_session
from app.auth.users import role_gte

# GreenLake/ (sibling of gldashboard_bundle)
_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import dsatApp as dsat  # noqa: E402

router = APIRouter()


def _require_admin(request: Request) -> dict:
    user = read_session(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required at /gldash/login")
    if not role_gte(user.get("role", "viewer"), "admin"):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


@router.post("/analyze")
async def analyze(request: Request, files: list[UploadFile] = File(...)):
    _require_admin(request)
    items: list[tuple[str, bytes]] = []
    for upload in files:
        if not upload or not upload.filename:
            continue
        data = await upload.read()
        items.append((upload.filename, data))
    result = dsat.analyze_upload_files(items)
    status = int(result.pop("status", 200))
    if status >= 400:
        return JSONResponse(result, status_code=status)
    return result


@router.get("/history")
async def history(request: Request, limit: int = 50):
    _require_admin(request)
    limit = min(max(limit, 1), 200)
    items = dsat._list_alerts(limit=limit)
    return {"items": items, "total": len(dsat._list_alerts())}


@router.get("/engineers")
async def engineers(request: Request):
    _require_admin(request)
    return dsat._engineer_rollup()


@router.get("/dashboard")
async def dashboard(request: Request):
    _require_admin(request)
    return dsat.build_dashboard_payload()


@router.get("/export.csv")
async def export_csv(request: Request):
    _require_admin(request)
    rows = dsat._flat_case_rows()
    buf = io.StringIO()
    fieldnames = [
        "id",
        "uploaded_at",
        "case_id",
        "engineer",
        "account_name",
        "product_description",
        "delivery_type",
        "interview_date",
        "response_id",
        "overall_satisfaction",
        "speed_of_access",
        "timeliness_of_updates",
        "time_to_resolve",
        "professionalism",
        "additional_feedback",
        "aruba_support_feedback",
        "other_aspects_feedback",
        "email_subject",
        "email_from",
        "email_date",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="dsat_powerbi_export.csv"'},
    )


@router.get("/export.xlsx")
async def export_xlsx(request: Request):
    _require_admin(request)
    import pandas as pd

    rows = dsat._flat_case_rows()
    rollup = dsat._engineer_rollup()
    eng_rows = [
        {"engineer": e["engineer"], "dsat_count": e["dsat_count"]}
        for e in rollup["engineers"]
    ]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="DSAT_Cases", index=False)
        pd.DataFrame(eng_rows).to_excel(writer, sheet_name="By_Engineer", index=False)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="dsat_powerbi_export.xlsx"'},
    )


@router.get("/report/{entry_id}")
async def report(request: Request, entry_id: str):
    _require_admin(request)
    from werkzeug.utils import secure_filename

    data = dsat._get_alert(secure_filename(entry_id))
    if not data:
        raise HTTPException(status_code=404, detail="Report not found.")
    return data
