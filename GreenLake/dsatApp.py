"""DSAT Alert Analyzer — store Outlook .msg alerts in SQLite + disk files."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

dsat_bp = Blueprint("dsat", __name__)

BASE_DIR = Path(__file__).resolve().parent
STORE_DIR = BASE_DIR / "data" / "dsat_uploads"
DB_PATH = BASE_DIR / "data" / "dsat.db"
INDEX_PATH = STORE_DIR / "index.json"  # legacy — migrated once into SQLite


@dsat_bp.before_request
def _lock_unauthenticated_flask_dsat():
    """Session cookie is scoped to /gldash — Flask APIs stay locked; use FastAPI mentors routes."""
    return (
        jsonify(
            {
                "error": "DSAT Alert Analyzer requires Platform Tools login. Open /gldash/mentors/dsat after signing in at /login.",
                "login": "/login",
                "api": "/gldash/api/dsat/",
            }
        ),
        401,
    )

CASE_FIELDS = [
    ("case_id", r"Case ID\s*-?\s*(.+?)(?=\n|Product Description|$)"),
    ("product_description", r"Product Description\s*-?\s*(.+?)(?=\n|Delivery Type|$)"),
    ("delivery_type", r"Delivery Type\s*-?\s*(.+?)(?=\n|Account Name|$)"),
    ("account_name", r"Account Name\s*-?\s*(.+?)(?=\n|Case Owner|$)"),
    ("case_owner", r"Case Owner\s*-?\s*(.+?)(?=\n|Case Owner Manager|$)"),
    ("case_owner_manager", r"Case Owner Manager\s*-?\s*(.+?)(?=\n|Interview Date|$)"),
    ("interview_date", r"Interview Date\s*-?\s*(.+?)(?=\n|Kindly find|Response ID|$)"),
    ("response_id", r"Response ID:\s*(\S+)"),
]


def _ensure_store() -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _get_db() -> sqlite3.Connection:
    _ensure_store()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dsat_alerts (
            id                        TEXT PRIMARY KEY,
            uploaded_at               TEXT NOT NULL,
            original_filename         TEXT,
            stored_filename           TEXT,
            stored_path               TEXT,
            subject                   TEXT,
            case_id                   TEXT,
            account_name              TEXT,
            case_owner                TEXT,
            case_owner_manager        TEXT,
            product_description       TEXT,
            delivery_type             TEXT,
            interview_date            TEXT,
            response_id               TEXT,
            overall_satisfaction      TEXT,
            likelihood_to_recommend   TEXT,
            speed_of_access           TEXT,
            timeliness_of_updates     TEXT,
            time_to_resolve           TEXT,
            professionalism           TEXT,
            additional_feedback       TEXT,
            aruba_support_feedback    TEXT,
            other_aspects_yes_no      TEXT,
            other_aspects_feedback    TEXT,
            email_from                TEXT,
            email_to                  TEXT,
            email_date                TEXT,
            body_text                 TEXT,
            report_json               TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dsat_owner ON dsat_alerts(case_owner)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dsat_uploaded ON dsat_alerts(uploaded_at)"
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_dsat_case_id_unique
        ON dsat_alerts(case_id)
        WHERE case_id IS NOT NULL AND TRIM(case_id) != ''
        """
    )
    conn.commit()
    _dedupe_case_ids(conn)
    _migrate_legacy_json(conn)
    return conn


def _normalize_case_id(value: str | None) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _find_by_case_id(conn: sqlite3.Connection, case_id: str) -> sqlite3.Row | None:
    cid = _normalize_case_id(case_id)
    if not cid:
        return None
    return conn.execute(
        "SELECT * FROM dsat_alerts WHERE TRIM(case_id) = ? ORDER BY uploaded_at DESC LIMIT 1",
        (cid,),
    ).fetchone()


def _dedupe_case_ids(conn: sqlite3.Connection) -> None:
    """Keep one row per case_id (newest upload); delete older duplicates."""
    rows = conn.execute(
        """
        SELECT case_id, id, uploaded_at
        FROM dsat_alerts
        WHERE case_id IS NOT NULL AND TRIM(case_id) != ''
        ORDER BY case_id, uploaded_at DESC
        """
    ).fetchall()
    seen: set[str] = set()
    to_delete: list[str] = []
    for row in rows:
        cid = _normalize_case_id(row["case_id"])
        if not cid:
            continue
        if cid in seen:
            to_delete.append(row["id"])
        else:
            seen.add(cid)
    for entry_id in to_delete:
        conn.execute("DELETE FROM dsat_alerts WHERE id = ?", (entry_id,))
    if to_delete:
        conn.commit()


def _migrate_legacy_json(conn: sqlite3.Connection) -> None:
    """One-time import of older index.json / report.json uploads into SQLite."""
    count = conn.execute("SELECT COUNT(*) FROM dsat_alerts").fetchone()[0]
    if count:
        return
    if not INDEX_PATH.is_file():
        return
    try:
        items = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    for item in items:
        entry_id = item.get("id") or ""
        if not entry_id:
            continue
        report_path = STORE_DIR / secure_filename(entry_id) / "report.json"
        payload = {"meta": item, "report": {}}
        if report_path.is_file():
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        meta = payload.get("meta") or item
        report = payload.get("report") or {}
        _insert_alert(conn, meta, report, commit=False)
    conn.commit()


def _row_to_meta(row: sqlite3.Row | dict) -> dict:
    d = dict(row)
    return {
        "id": d.get("id") or "",
        "original_filename": d.get("original_filename") or "",
        "stored_filename": d.get("stored_filename") or "",
        "stored_path": d.get("stored_path") or "",
        "uploaded_at": d.get("uploaded_at") or "",
        "subject": d.get("subject") or "",
        "case_id": d.get("case_id") or "",
        "account_name": d.get("account_name") or "",
        "case_owner": d.get("case_owner") or "",
        "overall_satisfaction": d.get("overall_satisfaction") or "",
        "likelihood_to_recommend": d.get("likelihood_to_recommend") or "",
    }


def _insert_alert(
    conn: sqlite3.Connection,
    meta: dict,
    report: dict,
    *,
    commit: bool = True,
) -> None:
    case = report.get("case") or {}
    email = report.get("email") or {}
    ratings = {r.get("aspect", ""): r.get("rating", "") for r in (report.get("aspect_ratings") or [])}
    case_id = _normalize_case_id(case.get("case_id") or meta.get("case_id") or "")
    meta = dict(meta)
    meta["case_id"] = case_id
    if case_id:
        report = dict(report)
        report_case = dict(case)
        report_case["case_id"] = case_id
        report["case"] = report_case
        # Enforce one row per case: drop any other ids for this case_id
        conn.execute(
            "DELETE FROM dsat_alerts WHERE TRIM(case_id) = ? AND id != ?",
            (case_id, meta.get("id")),
        )
    payload = json.dumps({"meta": meta, "report": report})
    conn.execute(
        """
        INSERT OR REPLACE INTO dsat_alerts (
            id, uploaded_at, original_filename, stored_filename, stored_path,
            subject, case_id, account_name, case_owner, case_owner_manager,
            product_description, delivery_type, interview_date, response_id,
            overall_satisfaction, likelihood_to_recommend,
            speed_of_access, timeliness_of_updates, time_to_resolve, professionalism,
            additional_feedback, aruba_support_feedback, other_aspects_yes_no,
            other_aspects_feedback, email_from, email_to, email_date, body_text,
            report_json
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            meta.get("id"),
            meta.get("uploaded_at") or datetime.now(timezone.utc).isoformat(),
            meta.get("original_filename") or "",
            meta.get("stored_filename") or "",
            meta.get("stored_path") or "",
            meta.get("subject") or email.get("subject") or "",
            case_id,
            case.get("account_name") or meta.get("account_name") or "",
            case.get("case_owner") or meta.get("case_owner") or "",
            case.get("case_owner_manager") or meta.get("case_owner_manager") or "",
            case.get("product_description") or "",
            case.get("delivery_type") or "",
            case.get("interview_date") or "",
            case.get("response_id") or "",
            report.get("overall_satisfaction") or meta.get("overall_satisfaction") or "",
            report.get("likelihood_to_recommend") or meta.get("likelihood_to_recommend") or "",
            ratings.get("Speed of access to a support engineer", ""),
            ratings.get("Timeliness of status updates", ""),
            ratings.get("Time taken to resolve your issue", ""),
            ratings.get("Professionalism of the support team", ""),
            report.get("additional_feedback") or "",
            report.get("aruba_support_feedback") or "",
            report.get("other_aspects_yes_no") or "",
            report.get("other_aspects_feedback") or "",
            email.get("from") or "",
            email.get("to") or "",
            email.get("date") or "",
            report.get("body_text") or "",
            payload,
        ),
    )
    if commit:
        conn.commit()


def _list_alerts(limit: int | None = None) -> list[dict]:
    conn = _get_db()
    try:
        sql = "SELECT * FROM dsat_alerts ORDER BY uploaded_at DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [_row_to_meta(row) for row in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def _get_alert(entry_id: str) -> dict | None:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT report_json FROM dsat_alerts WHERE id = ?",
            (entry_id,),
        ).fetchone()
        if not row:
            return None
        return json.loads(row["report_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    finally:
        conn.close()


def _clean(value: str | None) -> str:
    if not value:
        return ""
    text = value.replace("\xa0", " ").replace("\r", "")
    text = re.sub(r"^[\s\uf0b7\u2022\u25cf\u00b7\u00bb\x95\x97\uf0a7•▪◦●»]+", "", text)
    if text and not text[0].isalnum():
        text = re.sub(r"^[^A-Za-z0-9]+", "", text, count=1)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(" -\t\n\ufeff")


def _extract_field(body: str, pattern: str) -> str:
    match = re.search(pattern, body, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return _clean(match.group(1).split("\n")[0])


def _extract_section(body: str, start: str, ends: list[str]) -> str:
    start_re = re.escape(start)
    end_re = "|".join(re.escape(e) for e in ends)
    match = re.search(
        rf"{start_re}\s*(.*?)(?=(?:{end_re})|$)",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    return _clean(match.group(1))


def _extract_ratings(body: str) -> list[dict]:
    aspects = [
        "Speed of access to a support engineer",
        "Timeliness of status updates",
        "Time taken to resolve your issue",
        "Professionalism of the support team",
    ]
    labels = [
        "Completely dissatisfied",
        "Dissatisfied",
        "Neutral",
        "Satisfied",
        "Completely satisfied",
    ]
    ratings: list[dict] = []
    for aspect in aspects:
        rating = ""
        for line in body.splitlines():
            if aspect.lower() not in line.lower():
                continue
            if not re.match(rf"^\s*{re.escape(aspect)}\b", line, re.I):
                continue
            cells = line.split("\t")
            for idx, cell in enumerate(cells[1:]):
                if "x" in cell.lower() and idx < len(labels):
                    rating = labels[idx]
                    break
            if rating:
                break
        ratings.append({"aspect": aspect, "rating": rating or "Not captured"})
    return ratings


def parse_dsat_body(body: str) -> dict:
    text = _clean(body)
    case: dict[str, str] = {}
    for key, pattern in CASE_FIELDS:
        case[key] = _extract_field(text, pattern)

    overall = ""
    overall_match = re.search(
        r"How satisfied are you with your recent technical support experience\?\s*(.+?)(?=\n\s*Please share|$)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if overall_match:
        for line in overall_match.group(1).splitlines():
            cleaned = _clean(line)
            if cleaned:
                overall = cleaned
                break
    if not overall:
        invite = re.search(
            r"This is for Invitation only\s*(.+?)(?=\n|How satisfied|$)",
            text,
            re.I | re.S,
        )
        if invite:
            for line in invite.group(1).splitlines():
                cleaned = _clean(line)
                if cleaned:
                    overall = cleaned
                    break

    additional_feedback = _extract_section(
        text,
        "Please share any additional feedback based on your selection.",
        [
            "Please rate the following aspects",
            "Do you have any additional feedback about your Aruba support experience?",
        ],
    )
    aruba_feedback = _extract_section(
        text,
        "Do you have any additional feedback about your Aruba support experience?",
        [
            "Do you have any feedback regarding other aspects",
            "Based on this support experience",
        ],
    )
    other_feedback = _extract_section(
        text,
        "Please provide additional information",
        ["Based on this support experience", "Aruba B&F"],
    )
    other_yes_no = _extract_field(
        text,
        r"Do you have any feedback regarding other aspects of HPE Networking outside of your support experience\?\s*(.+?)(?=\n|Please provide|$)",
    )
    recommend = _extract_field(
        text,
        r"Based on this support experience, how likely are you to recommend HPE to a friend or colleague\?\s*(.+?)(?=\n|Aruba B&F|$)",
    )

    return {
        "case": case,
        "overall_satisfaction": overall,
        "additional_feedback": additional_feedback,
        "aspect_ratings": _extract_ratings(text),
        "aruba_support_feedback": aruba_feedback,
        "other_aspects_yes_no": other_yes_no,
        "other_aspects_feedback": other_feedback,
        "likelihood_to_recommend": recommend,
        "body_text": text,
    }


def _read_msg(path: Path) -> dict:
    import extract_msg

    msg = extract_msg.Message(str(path))
    try:
        subject = _clean(msg.subject or "")
        sender = _clean(msg.sender or "")
        to = _clean(msg.to or "")
        date = msg.date
        if hasattr(date, "isoformat"):
            date_str = date.isoformat()
        else:
            date_str = _clean(str(date or ""))
        body = msg.body or ""
        if not body and getattr(msg, "htmlBody", None):
            raw = msg.htmlBody
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            body = re.sub(r"<[^>]+>", " ", raw)
        report = parse_dsat_body(body)
        report["email"] = {
            "subject": subject,
            "from": sender,
            "to": to,
            "date": date_str,
        }
        return report
    finally:
        msg.close()


def analyze_upload_files(file_items: list[tuple[str, bytes]]) -> dict:
    """Process uploaded .msg bytes; returns payload dict (may include error).

    One case_id → one DB row. Re-uploading the same case updates the existing entry.
    """
    if not file_items:
        return {"error": "Please upload at least one .msg file.", "status": 400}

    _ensure_store()
    reports: list[dict] = []
    errors: list[str] = []
    created = 0
    updated = 0
    conn = _get_db()

    try:
        for original, data in file_items:
            original = original or "upload.msg"
            if not original.lower().endswith(".msg"):
                errors.append(f"{original}: unsupported type (use Outlook .msg)")
                continue

            safe_name = secure_filename(original) or "alert.msg"
            if not safe_name.lower().endswith(".msg"):
                safe_name += ".msg"

            tmp_id = "tmp-" + uuid.uuid4().hex[:12]
            tmp_dir = STORE_DIR / tmp_id
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = tmp_dir / safe_name
            tmp_path.write_bytes(data)

            try:
                report = _read_msg(tmp_path)
                case = report.get("case", {})
                case_id = _normalize_case_id(case.get("case_id", ""))

                existing = _find_by_case_id(conn, case_id) if case_id else None
                if existing:
                    entry_id = existing["id"]
                    is_update = True
                else:
                    entry_id = (
                        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                        + "-"
                        + uuid.uuid4().hex[:8]
                    )
                    is_update = False

                entry_dir = STORE_DIR / entry_id
                entry_dir.mkdir(parents=True, exist_ok=True)
                stored_path = entry_dir / safe_name
                stored_path.write_bytes(data)
                try:
                    for p in tmp_dir.iterdir():
                        p.unlink(missing_ok=True)
                    tmp_dir.rmdir()
                except OSError:
                    pass

                if case_id:
                    report.setdefault("case", {})["case_id"] = case_id

                report_meta = {
                    "id": entry_id,
                    "original_filename": original,
                    "stored_filename": safe_name,
                    "stored_path": str(stored_path.relative_to(BASE_DIR)),
                    "uploaded_at": datetime.now(timezone.utc).isoformat(),
                    "subject": report.get("email", {}).get("subject", ""),
                    "case_id": case_id,
                    "account_name": case.get("account_name", ""),
                    "case_owner": case.get("case_owner", ""),
                    "case_owner_manager": case.get("case_owner_manager", ""),
                    "overall_satisfaction": report.get("overall_satisfaction", ""),
                    "likelihood_to_recommend": report.get("likelihood_to_recommend", ""),
                    "updated": is_update,
                }
                (entry_dir / "report.json").write_text(
                    json.dumps({"meta": report_meta, "report": report}, indent=2),
                    encoding="utf-8",
                )
                _insert_alert(conn, report_meta, report, commit=False)

                # Same case in this batch: keep only the latest in the response list
                if case_id:
                    reports = [
                        r
                        for r in reports
                        if _normalize_case_id((r.get("meta") or {}).get("case_id")) != case_id
                    ]
                reports.append({"meta": report_meta, "report": report})
                if is_update:
                    updated += 1
                else:
                    created += 1
            except Exception as exc:
                errors.append(f"{original}: {exc}")
                try:
                    for p in tmp_dir.iterdir():
                        p.unlink(missing_ok=True)
                    tmp_dir.rmdir()
                except OSError:
                    pass
        conn.commit()
    finally:
        conn.close()

    if not reports and errors:
        return {"error": errors[0], "errors": errors, "status": 400}

    return {
        "reports": reports,
        "errors": errors,
        "summary": {
            "processed": len(reports),
            "created": created,
            "updated": updated,
            "failed": len(errors),
            "stored": True,
            "unique_by_case": True,
            "db": str(DB_PATH.relative_to(BASE_DIR)),
        },
        "engineers": _engineer_rollup(),
        "status": 200,
    }


def _engineer_rollup(items: list[dict] | None = None) -> dict:
    enriched = items if items is not None else _list_alerts()
    by_engineer: dict[str, dict] = {}

    for item in enriched:
        engineer = (item.get("case_owner") or "").strip() or "Unknown engineer"
        bucket = by_engineer.setdefault(
            engineer,
            {
                "engineer": engineer,
                "dsat_count": 0,
                "cases": [],
            },
        )
        bucket["dsat_count"] += 1
        bucket["cases"].append(
            {
                "id": item.get("id"),
                "case_id": item.get("case_id") or "",
                "account_name": item.get("account_name") or "",
                "overall_satisfaction": item.get("overall_satisfaction") or "",
                "likelihood_to_recommend": item.get("likelihood_to_recommend") or "",
                "uploaded_at": item.get("uploaded_at") or "",
                "subject": item.get("subject") or "",
            }
        )

    engineers = sorted(
        by_engineer.values(),
        key=lambda e: (-e["dsat_count"], e["engineer"].lower()),
    )
    return {
        "engineers": engineers,
        "total_engineers": len(engineers),
        "total_alerts": len(enriched),
    }


@dsat_bp.route("/api/dsat/history", methods=["GET"])
def dsat_history():
    limit = min(int(request.args.get("limit", 50)), 200)
    items = _list_alerts(limit=limit)
    return jsonify({"items": items, "total": len(_list_alerts())})


@dsat_bp.route("/api/dsat/engineers", methods=["GET"])
def dsat_engineers():
    return jsonify(_engineer_rollup())


def _flat_case_rows() -> list[dict]:
    conn = _get_db()
    try:
        rows_out: list[dict] = []
        for row in conn.execute(
            "SELECT * FROM dsat_alerts ORDER BY uploaded_at DESC"
        ).fetchall():
            d = dict(row)
            rows_out.append(
                {
                    "id": d.get("id") or "",
                    "uploaded_at": d.get("uploaded_at") or "",
                    "case_id": d.get("case_id") or "",
                    "engineer": d.get("case_owner") or "Unknown engineer",
                    "account_name": d.get("account_name") or "",
                    "product_description": d.get("product_description") or "",
                    "delivery_type": d.get("delivery_type") or "",
                    "interview_date": d.get("interview_date") or "",
                    "response_id": d.get("response_id") or "",
                    "overall_satisfaction": d.get("overall_satisfaction") or "",
                    "likelihood_to_recommend": d.get("likelihood_to_recommend") or "",
                    "speed_of_access": d.get("speed_of_access") or "",
                    "timeliness_of_updates": d.get("timeliness_of_updates") or "",
                    "time_to_resolve": d.get("time_to_resolve") or "",
                    "professionalism": d.get("professionalism") or "",
                    "additional_feedback": d.get("additional_feedback") or "",
                    "aruba_support_feedback": d.get("aruba_support_feedback") or "",
                    "other_aspects_feedback": d.get("other_aspects_feedback") or "",
                    "email_subject": d.get("subject") or "",
                    "email_from": d.get("email_from") or "",
                    "email_date": d.get("email_date") or "",
                }
            )
        return rows_out
    finally:
        conn.close()


def _count_rating(rows: list[dict], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        label = (row.get(field) or "Not captured").strip() or "Not captured"
        counts[label] = counts.get(label, 0) + 1
    return counts


_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "when", "at", "by",
    "for", "with", "about", "against", "between", "into", "through", "during",
    "before", "after", "above", "below", "to", "from", "up", "down", "in", "out",
    "on", "off", "over", "under", "again", "further", "once", "here", "there",
    "all", "any", "both", "each", "few", "more", "most", "other", "some", "such",
    "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very", "can",
    "will", "just", "don", "should", "now", "is", "are", "was", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "of", "as", "it",
    "its", "this", "that", "these", "those", "i", "we", "you", "he", "she", "they",
    "them", "their", "our", "your", "my", "me", "us", "him", "her", "what", "which",
    "who", "whom", "how", "why", "where", "would", "could", "should", "also", "get",
    "got", "using", "used", "use", "please", "kindly", "dear", "all", "based",
    "selection", "additional", "information", "feedback", "support", "experience",
    "recent", "technical", "hpe", "aruba", "networking", "case", "alert", "dsat",
    "invitation", "response", "survey", "customer", "team", "issue", "one", "two",
    "new", "old", "able", "want", "wanted", "never", "still", "need", "needs",
    "know", "like", "make", "made", "take", "took", "provide", "provided",
    "http", "https", "www", "com", "net", "org", "html", "htm", "url", "ping",
    "metadata", "partneridpid", "federation", "mailto",
}


def _tokenize_feedback(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", (text or "").lower())
    return [w for w in words if w not in _STOPWORDS and not w.isdigit()]


def _extract_keywords(rows: list[dict], *, top_n: int = 20) -> dict:
    """Find repetitive words and 2-word phrases across customer comments."""
    fields = (
        "additional_feedback",
        "aruba_support_feedback",
        "other_aspects_feedback",
    )
    # document frequency (comments containing term) + total term frequency
    uni_df: dict[str, int] = {}
    uni_tf: dict[str, int] = {}
    bi_df: dict[str, int] = {}
    bi_tf: dict[str, int] = {}
    comment_count = 0

    for row in rows:
        chunks = [str(row.get(f) or "").strip() for f in fields]
        text = " ".join(c for c in chunks if c)
        # Drop URLs so host fragments don't dominate keywords
        text = re.sub(r"https?://\S+", " ", text)
        if not text.strip():
            continue
        comment_count += 1
        tokens = _tokenize_feedback(text)
        seen_uni: set[str] = set()
        seen_bi: set[str] = set()
        for tok in tokens:
            uni_tf[tok] = uni_tf.get(tok, 0) + 1
            if tok not in seen_uni:
                uni_df[tok] = uni_df.get(tok, 0) + 1
                seen_uni.add(tok)
        for i in range(len(tokens) - 1):
            phrase = f"{tokens[i]} {tokens[i + 1]}"
            bi_tf[phrase] = bi_tf.get(phrase, 0) + 1
            if phrase not in seen_bi:
                bi_df[phrase] = bi_df.get(phrase, 0) + 1
                seen_bi.add(phrase)

    def _rank(df: dict[str, int], tf: dict[str, int], n: int) -> list[dict]:
        # Prefer terms that repeat across comments; break ties with raw frequency
        items = [
            {
                "keyword": k,
                "count": df.get(k, 0),
                "mentions": tf.get(k, 0),
                "comments": df.get(k, 0),
            }
            for k in df
        ]
        items.sort(key=lambda x: (-x["count"], -x["mentions"], x["keyword"]))
        return items[:n]

    return {
        "comments_scanned": comment_count,
        "top_keywords": _rank(uni_df, uni_tf, top_n),
        "top_phrases": _rank(bi_df, bi_tf, top_n),
    }


@dsat_bp.route("/api/dsat/dashboard", methods=["GET"])
def dsat_dashboard():
    return jsonify(build_dashboard_payload())


def build_dashboard_payload() -> dict:
    rows = _flat_case_rows()
    by_engineer: dict[str, int] = {}
    by_account: dict[str, int] = {}
    by_satisfaction: dict[str, int] = {}
    by_delivery: dict[str, int] = {}

    for row in rows:
        eng = row["engineer"] or "Unknown engineer"
        acct = row["account_name"] or "Unknown account"
        sat = row["overall_satisfaction"] or "Unspecified"
        delivery = row["delivery_type"] or "Unspecified"
        by_engineer[eng] = by_engineer.get(eng, 0) + 1
        by_account[acct] = by_account.get(acct, 0) + 1
        by_satisfaction[sat] = by_satisfaction.get(sat, 0) + 1
        by_delivery[delivery] = by_delivery.get(delivery, 0) + 1

    def _sorted_pairs(d: dict[str, int], limit: int | None = None) -> list[dict]:
        items = [
            {"label": k, "value": v}
            for k, v in sorted(d.items(), key=lambda kv: (-kv[1], kv[0].lower()))
        ]
        return items[:limit] if limit else items

    dissatisfied = sum(
        1
        for r in rows
        if "dissatisf" in (r.get("overall_satisfaction") or "").lower()
    )
    completely = sum(
        1
        for r in rows
        if "completely dissatisfied" in (r.get("overall_satisfaction") or "").lower()
    )
    with_feedback = sum(1 for r in rows if (r.get("additional_feedback") or "").strip())
    with_aruba_fb = sum(1 for r in rows if (r.get("aruba_support_feedback") or "").strip())
    total = len(rows) or 1

    aspect_fields = [
        ("speed_of_access", "Speed of access"),
        ("timeliness_of_updates", "Timeliness of updates"),
        ("time_to_resolve", "Time to resolve"),
        ("professionalism", "Professionalism"),
    ]
    aspect_worst: list[dict] = []
    for field, label in aspect_fields:
        counts = _count_rating(rows, field)
        bad = sum(v for k, v in counts.items() if "dissatisf" in k.lower())
        aspect_worst.append({"label": label, "value": bad, "field": field})

    return {
        "kpis": {
            "total_alerts": len(rows),
            "total_engineers": len(by_engineer),
            "total_accounts": len(by_account),
            "dissatisfied": dissatisfied,
            "completely_dissatisfied": completely,
            "dissatisfied_pct": round(100.0 * dissatisfied / total, 1) if rows else 0,
            "with_customer_feedback": with_feedback,
            "with_aruba_feedback": with_aruba_fb,
            "unique_delivery_types": len(by_delivery),
        },
        "by_engineer": _sorted_pairs(by_engineer),
        "by_account": _sorted_pairs(by_account, limit=12),
        "by_satisfaction": _sorted_pairs(by_satisfaction),
        "by_delivery": _sorted_pairs(by_delivery),
        "aspect_dissatisfied": sorted(
            aspect_worst, key=lambda x: (-x["value"], x["label"])
        ),
        "recent_cases": [
            {
                "id": r["id"],
                "case_id": r["case_id"],
                "engineer": r["engineer"],
                "account_name": r["account_name"],
                "overall_satisfaction": r["overall_satisfaction"],
                "uploaded_at": r["uploaded_at"],
            }
            for r in rows[:8]
        ],
        "rows": rows,
        "db": str(DB_PATH.relative_to(BASE_DIR)),
    }


@dsat_bp.route("/api/dsat/export.csv", methods=["GET"])
def dsat_export_csv():
    import csv
    import io

    from flask import Response

    rows = _flat_case_rows()
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
        mimetype="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="dsat_powerbi_export.csv"'
        },
    )


@dsat_bp.route("/api/dsat/export.xlsx", methods=["GET"])
def dsat_export_xlsx():
    import io

    import pandas as pd
    from flask import send_file

    rows = _flat_case_rows()
    rollup = _engineer_rollup()
    eng_rows = [
        {
            "engineer": e["engineer"],
            "dsat_count": e["dsat_count"],
        }
        for e in rollup["engineers"]
    ]

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="DSAT_Cases", index=False)
        pd.DataFrame(eng_rows).to_excel(writer, sheet_name="By_Engineer", index=False)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name="dsat_powerbi_export.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@dsat_bp.route("/api/dsat/report/<entry_id>", methods=["GET"])
def dsat_report(entry_id: str):
    safe_id = secure_filename(entry_id)
    data = _get_alert(safe_id)
    if not data:
        return jsonify({"error": "Report not found."}), 404
    return jsonify(data)
