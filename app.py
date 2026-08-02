"""POSCO 방문일정 공유 웹앱 — Flask 서버."""

import base64
import json
import os
import re
import uuid
from datetime import datetime, time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect

BASE_DIR = Path(__file__).resolve().parent
SHARE_DIR = BASE_DIR / "static" / "share"
SHARE_DIR.mkdir(parents=True, exist_ok=True)

def database_uri() -> str:
    """Render 등에서는 DATABASE_URL(Postgres), 로컬은 SQLite."""
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        return f"sqlite:///{BASE_DIR / 'visits.db'}"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    return url


app = Flask(__name__, static_folder=None)
app.config["SQLALCHEMY_DATABASE_URI"] = database_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class Visit(db.Model):
    __tablename__ = "visits"

    id = db.Column(db.Integer, primary_key=True)
    visit_date = db.Column(db.Date, nullable=False, index=True)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    visitor_name = db.Column(db.String(120), nullable=False)
    visitor_company = db.Column(db.String(120), nullable=False, default="")
    visitor_email = db.Column(db.String(200), nullable=False, default="")
    visitor_phone = db.Column(db.String(80), nullable=False, default="")
    visitor_clearance = db.Column(db.String(80), nullable=False, default="")
    visitor_notes = db.Column(db.Text, nullable=False, default="")
    visitor_photo = db.Column(db.Text, nullable=False, default="")
    registrant_name = db.Column(db.String(120), nullable=False, default="")
    registrant_team = db.Column(db.String(120), nullable=False, default="")
    registrant_email = db.Column(db.String(200), nullable=False, default="")
    purpose = db.Column(db.String(200), nullable=False, default="")
    location = db.Column(db.String(80), nullable=False, default="서울")
    status = db.Column(db.String(40), nullable=False, default="Confirmed")
    notes = db.Column(db.Text, nullable=False, default="")
    schedule_items = db.Column(db.Text, nullable=False, default="[]")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        try:
            items = json.loads(self.schedule_items or "[]")
        except (TypeError, ValueError):
            items = []
        return {
            "id": self.id,
            "visit_date": self.visit_date.strftime("%Y.%m.%d"),
            "visit_date_iso": self.visit_date.isoformat(),
            "start_time": self.start_time.strftime("%H:%M"),
            "end_time": self.end_time.strftime("%H:%M"),
            "time_range": f"{self.start_time.strftime('%H:%M')} - {self.end_time.strftime('%H:%M')}",
            "visitor_name": self.visitor_name,
            "visitor_company": self.visitor_company,
            "visitor_email": self.visitor_email,
            "visitor_phone": self.visitor_phone,
            "visitor_clearance": self.visitor_clearance,
            "visitor_notes": self.visitor_notes,
            "visitor_photo": self.visitor_photo,
            "registrant_name": self.registrant_name,
            "registrant_team": self.registrant_team,
            "registrant_email": self.registrant_email,
            "purpose": self.purpose,
            "location": self.location,
            "status": self.status,
            "notes": self.notes,
            "schedule_items": items,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


STATUS_STYLES = {
    "Confirmed": "bg-primary/10 text-primary",
    "Urgent": "bg-secondary/10 text-secondary",
    "Completed": "bg-outline-variant/30 text-on-surface-variant",
    "Pending": "bg-tertiary-fixed text-on-tertiary-fixed-variant",
    "Cancelled": "bg-error-container text-on-error-container",
}


def parse_time(value: str) -> time:
    return datetime.strptime(value.strip(), "%H:%M").time()


def parse_date(value: str):
    value = value.strip().replace(".", "-")
    return datetime.strptime(value, "%Y-%m-%d").date()


def _normalize_photo(value) -> str:
    """프로필 사진은 data URL(base64)만 허용. 과도한 크기는 거절."""
    if value is None:
        return ""
    photo = str(value).strip()
    if not photo:
        return ""
    if not photo.startswith("data:image/"):
        raise ValueError("지원하지 않는 사진 형식입니다.")
    # ~600KB raw base64 ≈ 약 450KB 이미지
    if len(photo) > 800_000:
        raise ValueError("사진 용량이 너무 큽니다. 더 작은 이미지를 선택하세요.")
    return photo


app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024


@app.after_request
def no_cache_html(response):
    if response.content_type and "text/html" in response.content_type:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


# ── Pages ──────────────────────────────────────────────


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/managing_schedule.html")
def managing_schedule():
    return send_from_directory(BASE_DIR, "managing_schedule.html")


@app.route("/visit_management.html")
def visit_management():
    return send_from_directory(BASE_DIR, "visit_management.html")


@app.route("/visitor.html")
def visitor():
    return send_from_directory(BASE_DIR, "visitor.html")


@app.route("/visitor_list.html")
def visitor_list():
    return send_from_directory(BASE_DIR, "visitor_list.html")


@app.route("/navigator.html")
def navigator():
    return send_from_directory(BASE_DIR, "navigator.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(BASE_DIR / "static", filename)


# ── API ────────────────────────────────────────────────


@app.get("/api/config")
def public_config():
    """프론트엔드 공개 설정 (카카오 JS 키 등)."""
    return jsonify({
        "kakao_js_key": (os.environ.get("KAKAO_JS_KEY") or "").strip(),
        "public_origin": (os.environ.get("PUBLIC_ORIGIN") or "").strip(),
    })


@app.get("/api/visitors")
def list_visitors():
    """방문 기록 기준 방문객 목록 (이름+소속 기준 최신 프로필)."""
    q = (request.args.get("q") or "").strip().lower()
    visits = Visit.query.order_by(Visit.visit_date.desc(), Visit.created_at.desc()).all()
    latest = {}
    for visit in visits:
        key = (
            (visit.visitor_name or "").strip().lower(),
            (visit.visitor_company or "").strip().lower(),
        )
        if not key[0]:
            continue
        if key not in latest:
            latest[key] = visit

    items = []
    for visit in latest.values():
        row = {
            "visit_id": visit.id,
            "visitor_name": visit.visitor_name or "",
            "visitor_company": visit.visitor_company or "",
            "visitor_email": visit.visitor_email or "",
            "visitor_phone": visit.visitor_phone or "",
            "visitor_clearance": visit.visitor_clearance or "",
            "visitor_notes": visit.visitor_notes or "",
            "visitor_photo": visit.visitor_photo or "",
            "location": visit.location or "",
            "visit_date": visit.visit_date.strftime("%Y.%m.%d") if visit.visit_date else "",
            "time_range": f"{visit.start_time.strftime('%H:%M')} - {visit.end_time.strftime('%H:%M')}" if visit.start_time and visit.end_time else "",
        }
        if q:
            hay = " ".join([
                row["visitor_name"],
                row["visitor_company"],
                row["visitor_email"],
                row["visitor_phone"],
                row["visitor_clearance"],
                row["location"],
                row["visitor_notes"],
            ]).lower()
            if q not in hay:
                continue
        items.append(row)

    items.sort(key=lambda x: (x["visitor_name"], x["visitor_company"]))
    return jsonify({"items": items, "total": len(items)})


@app.post("/api/share-images")
def create_share_image():
    """카카오 공유용으로 일정표 PNG를 임시 저장하고 공개 URL을 반환."""
    data = request.get_json(silent=True) or {}
    image_data = (data.get("image") or "").strip()
    if not image_data.startswith("data:image/"):
        return jsonify({"error": "이미지 데이터가 필요합니다."}), 400

    match = re.match(r"^data:image/(png|jpeg|jpg);base64,(.+)$", image_data, re.I | re.S)
    if not match:
        return jsonify({"error": "지원하지 않는 이미지 형식입니다."}), 400

    ext = match.group(1).lower()
    if ext == "jpg":
        ext = "jpeg"
    raw = match.group(2)
    try:
        binary = base64.b64decode(raw, validate=True)
    except Exception:
        return jsonify({"error": "이미지 디코딩에 실패했습니다."}), 400

    if len(binary) > 2_500_000:
        return jsonify({"error": "이미지 용량이 너무 큽니다."}), 400

    # 오래된 공유 이미지 정리 (24시간)
    now = datetime.utcnow().timestamp()
    for path in SHARE_DIR.glob("*.png"):
        try:
            if now - path.stat().st_mtime > 86400:
                path.unlink(missing_ok=True)
        except OSError:
            pass
    for path in SHARE_DIR.glob("*.jpeg"):
        try:
            if now - path.stat().st_mtime > 86400:
                path.unlink(missing_ok=True)
        except OSError:
            pass

    filename = f"{uuid.uuid4().hex}.{ext if ext != 'jpeg' else 'jpg'}"
    (SHARE_DIR / filename).write_bytes(binary)

    origin = (os.environ.get("PUBLIC_ORIGIN") or request.url_root).rstrip("/")
    url = f"{origin}/static/share/{filename}"
    return jsonify({"url": url, "filename": filename}), 201


@app.get("/api/visits")
def list_visits():
    status = request.args.get("status")
    location = (request.args.get("location") or "").strip()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(50, max(1, request.args.get("per_page", 10, type=int)))

    query = Visit.query.order_by(Visit.visit_date.desc(), Visit.start_time.desc())
    if status:
        query = query.filter(Visit.status == status)
    if location:
        if location == "서울":
            query = query.filter(Visit.location.in_(["서울", "서울 센터"]))
        else:
            query = query.filter(Visit.location == location)

    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()

    return jsonify(
        {
            "items": [v.to_dict() for v in items],
            "total": total,
            "page": page,
            "per_page": per_page,
            "status_styles": STATUS_STYLES,
        }
    )


@app.get("/api/visits/<int:visit_id>")
def get_visit(visit_id: int):
    visit = Visit.query.get_or_404(visit_id)
    return jsonify(visit.to_dict())


@app.post("/api/visits")
def create_visit():
    data = request.get_json(silent=True) or {}

    required = ["visit_date", "start_time", "end_time", "visitor_name"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"필수 항목 누락: {', '.join(missing)}"}), 400

    try:
        schedule_items = data.get("schedule_items") or []
        if not isinstance(schedule_items, list):
            schedule_items = []
        visit = Visit(
            visit_date=parse_date(data["visit_date"]),
            start_time=parse_time(data["start_time"]),
            end_time=parse_time(data["end_time"]),
            visitor_name=data["visitor_name"].strip(),
            visitor_company=(data.get("visitor_company") or "").strip(),
            visitor_email=(data.get("visitor_email") or "").strip(),
            visitor_phone=(data.get("visitor_phone") or "").strip(),
            visitor_clearance=(data.get("visitor_clearance") or "").strip(),
            visitor_notes=(data.get("visitor_notes") or "").replace("\r\n", "\n").strip(),
            visitor_photo=_normalize_photo(data.get("visitor_photo")),
            registrant_name=(data.get("registrant_name") or "이름").strip(),
            registrant_team=(data.get("registrant_team") or "실").strip(),
            registrant_email=(data.get("registrant_email") or "@posco.com").strip(),
            purpose=(data.get("purpose") or "").strip(),
            location=(data.get("location") or "서울").strip(),
            status=(data.get("status") or "Confirmed").strip(),
            notes=(data.get("notes") or "").strip(),
            schedule_items=json.dumps(schedule_items, ensure_ascii=False),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    db.session.add(visit)
    db.session.commit()
    return jsonify(visit.to_dict()), 201


@app.put("/api/visits/<int:visit_id>")
def update_visit(visit_id: int):
    visit = Visit.query.get_or_404(visit_id)
    data = request.get_json(silent=True) or {}

    try:
        if "visit_date" in data:
            visit.visit_date = parse_date(data["visit_date"])
        if "start_time" in data:
            visit.start_time = parse_time(data["start_time"])
        if "end_time" in data:
            visit.end_time = parse_time(data["end_time"])

        for field in (
            "visitor_name",
            "visitor_company",
            "visitor_email",
            "visitor_phone",
            "visitor_clearance",
            "visitor_notes",
            "visitor_photo",
            "registrant_name",
            "registrant_team",
            "registrant_email",
            "purpose",
            "location",
            "status",
            "notes",
        ):
            if field in data and data[field] is not None:
                if field == "visitor_photo":
                    setattr(visit, field, _normalize_photo(data[field]))
                    continue
                value = str(data[field]).replace("\r\n", "\n")
                if field in ("visitor_notes", "notes"):
                    setattr(visit, field, value.strip(" \t\r\n"))
                else:
                    setattr(visit, field, value.strip())

        if "schedule_items" in data:
            items = data["schedule_items"] if isinstance(data["schedule_items"], list) else []
            visit.schedule_items = json.dumps(items, ensure_ascii=False)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    db.session.commit()
    return jsonify(visit.to_dict())


@app.delete("/api/visits/<int:visit_id>")
def delete_visit(visit_id: int):
    visit = Visit.query.get_or_404(visit_id)
    db.session.delete(visit)
    db.session.commit()
    return jsonify({"ok": True})


@app.get("/api/stats")
def stats():
    total = Visit.query.count()
    confirmed = Visit.query.filter_by(status="Confirmed").count()
    urgent = Visit.query.filter_by(status="Urgent").count()
    completed = Visit.query.filter_by(status="Completed").count()
    return jsonify(
        {
            "total": total,
            "confirmed": confirmed,
            "urgent": urgent,
            "completed": completed,
        }
    )


def seed_if_empty():
    if Visit.query.count() > 0:
        return

    samples = [
        Visit(
            visit_date=parse_date("2024-11-21"),
            start_time=parse_time("14:00"),
            end_time=parse_time("15:30"),
            visitor_name="김철수 사장",
            visitor_company="글로벌 파트너스",
            registrant_name="이민석 과장",
            registrant_team="홍보팀",
            purpose="신규 제휴 미팅",
            location="서울",
            status="Confirmed",
        ),
        Visit(
            visit_date=parse_date("2024-11-21"),
            start_time=parse_time("10:00"),
            end_time=parse_time("11:30"),
            visitor_name="Robert Wilson",
            visitor_company="Steel Corp Ltd.",
            registrant_name="박지수 대리",
            registrant_team="기술지원팀",
            purpose="기술 세미나",
            location="포항",
            status="Urgent",
        ),
        Visit(
            visit_date=parse_date("2024-11-20"),
            start_time=parse_time("15:00"),
            end_time=parse_time("16:00"),
            visitor_name="최현우 본부장",
            visitor_company="POSCO DX",
            registrant_name="이민석 과장",
            registrant_team="홍보팀",
            purpose="시스템 정기 점검",
            location="광양",
            status="Completed",
        ),
        Visit(
            visit_date=parse_date("2024-11-20"),
            start_time=parse_time("09:00"),
            end_time=parse_time("10:30"),
            visitor_name="정미경 이사",
            visitor_company="현대건설",
            registrant_name="김지훈 대리",
            registrant_team="전략기획팀",
            purpose="사업 협력 논의",
            location="서울",
            status="Confirmed",
        ),
        Visit(
            visit_date=parse_date("2024-11-19"),
            start_time=parse_time("13:30"),
            end_time=parse_time("15:00"),
            visitor_name="Lee Sang-hoon",
            visitor_company="Samsung Electronics",
            registrant_name="박지수 대리",
            registrant_team="기술지원팀",
            purpose="장비 유지보수",
            location="포항",
            status="Completed",
        ),
    ]
    db.session.add_all(samples)
    db.session.commit()


def ensure_schema():
    """기존 DB에 새 컬럼이 없으면 추가 (SQLite/Postgres 공통)."""
    inspector = inspect(db.engine)
    if not inspector.has_table("visits"):
        return
    cols = {c["name"] for c in inspector.get_columns("visits")}
    alterations = {
        "registrant_email": "ALTER TABLE visits ADD COLUMN registrant_email VARCHAR(200) NOT NULL DEFAULT ''",
        "schedule_items": "ALTER TABLE visits ADD COLUMN schedule_items TEXT NOT NULL DEFAULT '[]'",
        "visitor_email": "ALTER TABLE visits ADD COLUMN visitor_email VARCHAR(200) NOT NULL DEFAULT ''",
        "visitor_phone": "ALTER TABLE visits ADD COLUMN visitor_phone VARCHAR(80) NOT NULL DEFAULT ''",
        "visitor_clearance": "ALTER TABLE visits ADD COLUMN visitor_clearance VARCHAR(80) NOT NULL DEFAULT ''",
        "visitor_notes": "ALTER TABLE visits ADD COLUMN visitor_notes TEXT NOT NULL DEFAULT ''",
        "visitor_photo": "ALTER TABLE visits ADD COLUMN visitor_photo TEXT NOT NULL DEFAULT ''",
    }
    with db.engine.begin() as conn:
        for name, sql in alterations.items():
            if name not in cols:
                conn.exec_driver_sql(sql)


with app.app_context():
    db.create_all()
    ensure_schema()
    seed_if_empty()


def _lan_ip() -> str:
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    import os

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    lan = _lan_ip()
    print(f"로컬:   http://127.0.0.1:{port}")
    print(f"내부망: http://{lan}:{port}")
    print("외부(인터넷) 접속은 터널 또는 클라우드 배포가 필요합니다.")
    app.run(debug=debug, host=host, port=port)
