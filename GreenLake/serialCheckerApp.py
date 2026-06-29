"""Check device serial numbers against uploaded PDF or TXT files."""
from __future__ import annotations

import io
import re

from flask import Blueprint, jsonify, request
from pypdf import PdfReader

serial_checker_bp = Blueprint("serial_checker", __name__)


def _parse_serials(raw: str) -> list[str]:
    parts = re.split(r"[\s,;]+", raw.strip())
    seen: set[str] = set()
    serials: list[str] = []
    for part in parts:
        serial = part.strip()
        if serial and serial not in seen:
            seen.add(serial)
            serials.append(serial)
    return serials


def _append_location(found: dict[str, list[dict]], serial: str, location: dict) -> None:
    if any(item["location"] == location["location"] for item in found[serial]):
        return
    found[serial].append(location)


def _check_pdf(data: bytes, filename: str, serials: list[str], found: dict[str, list[dict]]) -> None:
    reader = PdfReader(io.BytesIO(data))
    for page_index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if not text:
            continue
        for serial in serials:
            if serial in text:
                _append_location(
                    found,
                    serial,
                    {
                        "file": filename,
                        "page": page_index + 1,
                        "location": f"{filename} (Page {page_index + 1})",
                    },
                )


def _check_txt(data: bytes, filename: str, serials: list[str], found: dict[str, list[dict]]) -> None:
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        content = data.decode("latin-1", errors="replace")

    for serial in serials:
        if serial in content:
            _append_location(
                found,
                serial,
                {"file": filename, "page": None, "location": filename},
            )


@serial_checker_bp.route("/api/serial-check", methods=["POST"])
def serial_check():
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        return jsonify({"error": "Please upload at least one PDF or TXT file."}), 400

    serials = _parse_serials(request.form.get("serials", ""))
    if not serials:
        return jsonify({"error": "Please enter at least one serial number."}), 400

    found: dict[str, list[dict]] = {serial: [] for serial in serials}
    errors: list[str] = []

    for upload in files:
        filename = upload.filename
        try:
            data = upload.read()
            lower = filename.lower()
            if lower.endswith(".pdf"):
                _check_pdf(data, filename, serials, found)
            elif lower.endswith(".txt"):
                _check_txt(data, filename, serials, found)
            else:
                errors.append(f"{filename}: unsupported file type (use PDF or TXT)")
        except Exception as exc:
            errors.append(f"{filename}: {exc}")

    not_found = [serial for serial in serials if not found[serial]]

    return jsonify(
        {
            "found": {serial: locations for serial, locations in found.items() if locations},
            "not_found": not_found,
            "summary": {
                "total": len(serials),
                "found_count": len(serials) - len(not_found),
                "not_found_count": len(not_found),
                "files_processed": len(files),
            },
            "errors": errors,
        }
    )
