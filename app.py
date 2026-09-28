from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE = Path(__file__).parent
DB_PATH = Path(os.getenv("ROOM_DB", str(BASE / "rooms.db")))
SECRET = os.getenv("QR_SECRET", "demo-only-change-this-secret").encode()
LOCK = threading.RLock()
CHAT: dict[str, dict[str, Any]] = {}
SESSIONS: dict[str, dict[str, str]] = {}
app = FastAPI(title="Smart Study Room API", version="0.2.0")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="minutes")


@contextmanager
def db():
    conn = sqlite3.connect(str(DB_PATH), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS rooms (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, capacity INTEGER NOT NULL,
          amenities TEXT NOT NULL, hourly_rate INTEGER NOT NULL,
          online INTEGER NOT NULL DEFAULT 1, occupied INTEGER NOT NULL DEFAULT 0,
          light_on INTEGER NOT NULL DEFAULT 0, fan_on INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS bookings (
          id TEXT PRIMARY KEY, room_id INTEGER NOT NULL REFERENCES rooms(id),
          user_id TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
          people INTEGER NOT NULL, cost INTEGER NOT NULL, status TEXT NOT NULL,
          qr_exp TEXT NOT NULL, idempotency_key TEXT UNIQUE, created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, booking_id TEXT, event TEXT NOT NULL,
          at TEXT NOT NULL, detail TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,
          action TEXT NOT NULL, detail TEXT NOT NULL, at TEXT NOT NULL
        );
        """)
        if c.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0:
            c.executemany("INSERT INTO rooms(id,name,capacity,amenities,hourly_rate) VALUES(?,?,?,?,?)", [
                (1, "Phòng học A101", 8, '["Máy chiếu","Bảng trắng","Điều hòa"]', 40000),
                (2, "Phòng thảo luận B204", 6, '["Màn hình","Bảng trắng","Điều hòa"]', 35000),
                (3, "Phòng lab C301", 20, '["Máy chiếu","Máy tính","Điều hòa"]', 80000),
                (4, "Phòng nhóm D102", 4, '["Màn hình","Ổ cắm"]', 25000),
            ])


@app.on_event("startup")
def startup() -> None:
    init_db()


def audit(c: sqlite3.Connection, actor: str, action: str, detail: str) -> None:
    c.execute("INSERT INTO audit(actor,action,detail,at) VALUES(?,?,?,?)", (actor, action, detail, stamp(now())))


def require_admin(role: Optional[str]) -> None:
    if role != "admin":
        raise HTTPException(403, "Yêu cầu quyền Administrator")


class BookingIn(BaseModel):
    room_id: int
    start: datetime
    end: datetime
    people: int = Field(ge=1, le=100)
    user_id: str = "demo-user"
    idempotency_key: Optional[str] = None


class ChatIn(BaseModel):
    message: str
    session_id: str = "default"


class QRIn(BaseModel):
    token: str
    room_id: int


class TelemetryIn(BaseModel):
    room_id: int
    occupied: bool
    online: bool = True


class LoginIn(BaseModel):
    username: str
    password: str


DEMO_ACCOUNTS = {
    "user": {"password": "demo123", "user_id": "demo-user", "role": "user", "name": "Người dùng demo"},
    "admin": {"password": "admin123", "user_id": "demo-admin", "role": "admin", "name": "Quản trị viên"},
}


def current_user(authorization: Optional[str] = Header(default=None)) -> dict[str, str]:
    scheme, _, token = (authorization or "").partition(" ")
    identity = SESSIONS.get(token) if scheme.lower() == "bearer" else None
    if not identity:
        raise HTTPException(401, "Vui lòng đăng nhập")
    return identity


def parse_dt(raw: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    except ValueError as exc:
        raise HTTPException(400, "Thời gian phải theo ISO 8601") from exc


def free_rooms(start: datetime, end: datetime, people: int, amenities: Optional[list[str]] = None) -> list[dict[str, Any]]:
    with db() as c:
        rows = c.execute("SELECT * FROM rooms WHERE capacity>=? ORDER BY hourly_rate", (people,)).fetchall()
        result = []
        for row in rows:
            room = dict(row)
            room["amenities"] = json.loads(room["amenities"])
            if amenities and not {a.casefold() for a in amenities}.issubset({a.casefold() for a in room["amenities"]}):
                continue
            conflict = c.execute("""SELECT 1 FROM bookings WHERE room_id=?
              AND status IN ('CONFIRMED','CHECKED_IN') AND start<? AND end>? LIMIT 1""",
              (room["id"], stamp(end), stamp(start))).fetchone()
            if not conflict:
                result.append(room)
        return result


def sign_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()
    return body + "." + sig


def decode_token(token: str) -> dict[str, Any]:
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception as exc:
        raise HTTPException(400, "QR không hợp lệ hoặc đã bị thay đổi") from exc


def booking_view(row: Union[sqlite3.Row, dict]) -> dict[str, Any]:
    booking = dict(row)
    booking["qr_token"] = sign_payload({"booking_id": booking["id"], "room_id": booking["room_id"], "exp": booking["qr_exp"]})
    return booking


@app.get("/")
def home():
    return FileResponse(str(BASE / "static" / "index.html"))


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/auth/login")
def login(data: LoginIn):
    account = DEMO_ACCOUNTS.get(data.username)
    if not account or not hmac.compare_digest(data.password, account["password"]):
        raise HTTPException(401, "Tên đăng nhập hoặc mật khẩu không đúng")
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = {"user_id": account["user_id"], "role": account["role"], "name": account["name"]}
    return {"token": token, "user": SESSIONS[token]}


@app.get("/api/auth/me")
def whoami(identity: dict[str, str] = Depends(current_user)):
    return identity


@app.get("/api/rooms")
def rooms(identity: dict[str, str] = Depends(current_user)):
    with db() as c:
        return [{**dict(r), "amenities": json.loads(r["amenities"])} for r in c.execute("SELECT * FROM rooms ORDER BY id").fetchall()]


@app.get("/api/availability")
def availability(start: str, end: str, people: int = 1, amenities: str = "", identity: dict[str, str] = Depends(current_user)):
    s, e = parse_dt(start), parse_dt(end)
    if s >= e or s < now() - timedelta(minutes=1):
        raise HTTPException(400, "Khoảng thời gian không hợp lệ")
    return free_rooms(s, e, people, [x.strip() for x in amenities.split(",") if x.strip()])


@app.post("/api/bookings")
def create_booking(data: BookingIn, identity: dict[str, str] = Depends(current_user)):
    start = data.start.replace(tzinfo=timezone.utc) if data.start.tzinfo is None else data.start.astimezone(timezone.utc)
    end = data.end.replace(tzinfo=timezone.utc) if data.end.tzinfo is None else data.end.astimezone(timezone.utc)
    if start >= end or start < now() - timedelta(minutes=1):
        raise HTTPException(400, "Khoảng thời gian không hợp lệ")
    with LOCK, db() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            if data.idempotency_key:
                existing = c.execute("SELECT * FROM bookings WHERE idempotency_key=?", (data.idempotency_key,)).fetchone()
                if existing:
                    c.execute("COMMIT")
                    return booking_view(existing)
            room = c.execute("SELECT * FROM rooms WHERE id=?", (data.room_id,)).fetchone()
            if not room:
                raise HTTPException(404, "Không tìm thấy phòng")
            if data.people > room["capacity"]:
                raise HTTPException(400, "Số người vượt sức chứa phòng")
            conflict = c.execute("""SELECT 1 FROM bookings WHERE room_id=?
              AND status IN ('CONFIRMED','CHECKED_IN') AND start<? AND end>? LIMIT 1""",
              (data.room_id, stamp(end), stamp(start))).fetchone()
            if conflict:
                raise HTTPException(409, "Phòng vừa được đặt trong khoảng thời gian này")
            booking_id = str(uuid.uuid4())
            cost = round(room["hourly_rate"] * (end - start).total_seconds() / 3600)
            expiration = end + timedelta(minutes=15)
            c.execute("INSERT INTO bookings VALUES(?,?,?,?,?,?,?,?,?,?,?)", (booking_id, data.room_id, identity["user_id"], stamp(start), stamp(end), data.people, cost, "CONFIRMED", stamp(expiration), data.idempotency_key, stamp(now())))
            c.execute("INSERT INTO events(booking_id,event,at,detail) VALUES(?,?,?,?)", (booking_id, "CONFIRMED", stamp(now()), "Booking đã xác nhận"))
            audit(c, identity["user_id"], "BOOKING_CREATED", booking_id)
            c.execute("COMMIT")
            return booking_view(c.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone())
        except Exception:
            c.execute("ROLLBACK")
            raise


@app.get("/api/bookings")
def list_bookings(identity: dict[str, str] = Depends(current_user)):
    with db() as c:
        return [booking_view(r) for r in c.execute("SELECT * FROM bookings WHERE user_id=? ORDER BY created DESC", (identity["user_id"],)).fetchall()]


@app.post("/api/bookings/{booking_id}/cancel")
def cancel_booking(booking_id: str, identity: dict[str, str] = Depends(current_user)):
    with db() as c:
        row = c.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Không tìm thấy booking")
        if identity["role"] != "admin" and row["user_id"] != identity["user_id"]:
            raise HTTPException(403, "Không có quyền hủy booking này")
        if row["status"] != "CONFIRMED":
            raise HTTPException(409, "Chỉ hủy được booking CONFIRMED")
        c.execute("UPDATE bookings SET status='CANCELLED' WHERE id=?", (booking_id,))
        c.execute("INSERT INTO events(booking_id,event,at,detail) VALUES(?,?,?,?)", (booking_id, "CANCELLED", stamp(now()), "Người dùng hủy booking"))
        audit(c, identity["user_id"], "BOOKING_CANCELLED", booking_id)
        return {"ok": True, "status": "CANCELLED"}


@app.post("/api/qr/validate")
def validate_qr(data: QRIn, identity: dict[str, str] = Depends(current_user)):
    payload = decode_token(data.token)
    if payload.get("room_id") != data.room_id:
        raise HTTPException(403, "QR không thuộc phòng này")
    if now() > parse_dt(payload.get("exp", "")):
        raise HTTPException(403, "QR đã hết hạn")
    with db() as c:
        row = c.execute("SELECT * FROM bookings WHERE id=?", (payload.get("booking_id"),)).fetchone()
        if not row or row["room_id"] != data.room_id:
            raise HTTPException(404, "Không tìm thấy booking phù hợp")
        if identity["role"] != "admin" and row["user_id"] != identity["user_id"]:
            raise HTTPException(403, "QR không thuộc booking của tài khoản này")
        if row["status"] != "CONFIRMED":
            raise HTTPException(409, "Booking không còn ở trạng thái CONFIRMED")
        if now() < parse_dt(row["start"]) - timedelta(minutes=15):
            raise HTTPException(403, "Check-in quá sớm")
        if now() > parse_dt(row["end"]) + timedelta(minutes=15):
            raise HTTPException(403, "Đã quá thời gian check-in")
        c.execute("BEGIN IMMEDIATE")
        c.execute("UPDATE bookings SET status='CHECKED_IN' WHERE id=? AND status='CONFIRMED'", (row["id"],))
        if c.execute("SELECT changes()").fetchone()[0] == 0:
            c.execute("ROLLBACK")
            raise HTTPException(409, "Booking đã được xử lý")
        c.execute("UPDATE rooms SET occupied=1,light_on=1,fan_on=1 WHERE id=?", (data.room_id,))
        c.execute("INSERT INTO events(booking_id,event,at,detail) VALUES(?,?,?,?)", (row["id"], "CHECKED_IN", stamp(now()), "QR hợp lệ; bật thiết bị mô phỏng"))
        audit(c, row["user_id"], "CHECK_IN", row["id"])
        c.execute("COMMIT")
        return {"ok": True, "booking_id": row["id"], "status": "CHECKED_IN", "devices": {"light": "on", "fan": "on"}}


@app.get("/api/qr/image")
def qr_image(token: str):
    import qrcode
    image = qrcode.make(token)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    stream.seek(0)
    return StreamingResponse(stream, media_type="image/png")


@app.post("/api/iot/telemetry")
def telemetry(data: TelemetryIn):
    with db() as c:
        room = c.execute("SELECT id FROM rooms WHERE id=?", (data.room_id,)).fetchone()
        if not room:
            raise HTTPException(404, "Không tìm thấy phòng")
        c.execute("UPDATE rooms SET online=?,occupied=?,light_on=?,fan_on=? WHERE id=?", (int(data.online), int(data.occupied), int(data.occupied), int(data.occupied), data.room_id))
        c.execute("INSERT INTO audit(actor,action,detail,at) VALUES(?,?,?,?)", ("room-controller", "TELEMETRY", f"room={data.room_id}; occupied={data.occupied}; online={data.online}", stamp(now())))
        if not data.occupied:
            c.execute("UPDATE bookings SET status='COMPLETED' WHERE room_id=? AND status='CHECKED_IN' AND end<=?", (data.room_id, stamp(now())))
        return {"ok": True, "room_id": data.room_id, "online": data.online, "occupied": data.occupied}


@app.post("/api/chat")
def chat(data: ChatIn, identity: dict[str, str] = Depends(current_user)):
    message = data.message.strip()
    state = CHAT.setdefault(identity["user_id"] + ":" + data.session_id, {})
    normalized = message.casefold()

    # Informational room questions use the database as the source of truth.
    if re.search(r"(các loại|loại.*phòng|phòng.*loại nào|có.*phòng.*(?:gì|nào)|giới thiệu.*phòng|thông tin.*phòng|danh sách phòng|(?:giá|sức chứa|tiện nghi).*phòng|phòng.*(?:tiện nghi|sức chứa|giá))", normalized):
        state.pop("pending_confirm", None)
        with db() as c:
            rows = c.execute("SELECT name,capacity,amenities,hourly_rate FROM rooms ORDER BY capacity").fetchall()
        descriptions = [f"• {r['name']}: tối đa {r['capacity']} người; tiện nghi {', '.join(json.loads(r['amenities']))}; {r['hourly_rate']:,}₫/giờ" for r in rows]
        return {"reply": "Hiện có các phòng sau (thông tin lấy từ hệ thống):\n" + "\n".join(descriptions) + "\nBạn muốn đặt phòng nào? Hãy cho mình biết số người, ngày, giờ bắt đầu và thời lượng."}

    people_match = re.search(r"\b(\d{1,2})\s*(?:người|nguoi|pax)\b", normalized)
    if people_match:
        state["people"] = int(people_match.group(1))

    local_today = now().astimezone().date()
    requested_date = None
    relative = re.search(r"\b(hôm nay|hom nay|ngày mai|ngay mai|mai|ngày kia|ngay kia|mốt|mot)\b", normalized)
    if relative:
        word = relative.group(1)
        offset = 0 if word in ("hôm nay", "hom nay") else (2 if word in ("ngày kia", "ngay kia", "mốt", "mot") else 1)
        requested_date = local_today + timedelta(days=offset)
    else:
        iso_match = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", normalized)
        dmy_match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b", normalized)
        short_dmy_match = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?![/-]\d{2,4})\b", normalized)
        vn_match = re.search(r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})(?:\s+năm\s+(20\d{2}))?", normalized)
        weekday_match = re.search(r"\b(thứ\s*([2-7])|chủ nhật)\b", normalized)
        try:
            if iso_match:
                requested_date = date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
            elif dmy_match:
                requested_date = date(int(dmy_match.group(3)), int(dmy_match.group(2)), int(dmy_match.group(1)))
            elif short_dmy_match:
                requested_date = date(local_today.year, int(short_dmy_match.group(2)), int(short_dmy_match.group(1)))
            elif vn_match:
                requested_date = date(int(vn_match.group(3) or local_today.year), int(vn_match.group(2)), int(vn_match.group(1)))
            elif weekday_match:
                target_weekday = 6 if weekday_match.group(1) == "chủ nhật" else int(weekday_match.group(2)) - 2
                offset = (target_weekday - local_today.weekday()) % 7 or 7
                requested_date = local_today + timedelta(days=offset)
        except ValueError:
            return {"reply": "Ngày bạn nhập không hợp lệ. Hãy ghi ngày/tháng/năm, ví dụ 05/10/2026.", "needs": ["date"]}
    if requested_date:
        if requested_date < local_today:
            state.pop("date", None); state.pop("start", None); state.pop("end", None); state.pop("pending_confirm", None)
            return {"reply": "Ngày đó đã qua nên mình không thể tạo booking. Hãy chọn hôm nay hoặc một ngày trong tương lai.", "needs": ["date"]}
        state["date"] = requested_date.isoformat()

    time_match = re.search(r"\b(?:lúc\s*)?(\d{1,2})\s*(?:h|:)(\d{2})?\b", normalized)
    if time_match:
        hour, minute = int(time_match.group(1)), int(time_match.group(2) or 0)
        if hour > 23 or minute > 59:
            return {"reply": "Giờ không hợp lệ. Hãy nhập giờ từ 00:00 đến 23:59.", "needs": ["time"]}
        state["time"] = f"{hour:02d}:{minute:02d}"

    duration_match = re.search(r"(?:trong\s*)?(?:(\d+(?:[.,]\d+)?)\s*(tiếng|giờ|hours)(?:\s*(?:và\s*)?(\d+)\s*(?:phút|phut))?|(\d+)\s*(phút|phut|min|minutes))", normalized)
    if duration_match:
        if duration_match.group(1):
            minutes = round(float(duration_match.group(1).replace(",", ".")) * 60) + int(duration_match.group(3) or 0)
        else:
            minutes = int(duration_match.group(4))
        if minutes < 15 or minutes > 12 * 60:
            return {"reply": "Thời lượng đặt phòng phải từ 15 phút đến 12 tiếng. Bạn muốn dùng phòng bao lâu?", "needs": ["duration"]}
        state["duration_minutes"] = minutes

    if not state.get("people"):
        return {"reply": "Bạn cần phòng cho bao nhiêu người?", "needs": ["people"]}
    if not state.get("date"):
        return {"reply": "Bạn muốn đặt phòng ngày nào? Hãy ghi ngày/tháng/năm hoặc nói hôm nay/ngày mai.", "needs": ["date"]}
    if not state.get("time"):
        return {"reply": "Bạn muốn bắt đầu lúc mấy giờ? Ví dụ 14:30.", "needs": ["time"]}
    if not state.get("duration_minutes"):
        return {"reply": "Bạn muốn sử dụng phòng trong bao lâu? Ví dụ 90 phút, 2 tiếng hoặc 2 tiếng 30 phút.", "needs": ["duration"]}

    start_local = datetime.combine(date.fromisoformat(state["date"]), datetime.strptime(state["time"], "%H:%M").time()).astimezone()
    if start_local <= now().astimezone():
        state.pop("time", None); state.pop("start", None); state.pop("end", None); state.pop("pending_confirm", None)
        return {"reply": "Giờ bắt đầu đó đã qua. Hãy chọn một giờ trong tương lai (hoặc đổi sang ngày khác).", "needs": ["time"]}
    state["start"] = start_local.astimezone(timezone.utc)
    state["end"] = state["start"] + timedelta(minutes=state["duration_minutes"])
    choices = free_rooms(state["start"], state["end"], state["people"])
    if not choices:
        return {"reply": "Mình chưa tìm thấy phòng trống phù hợp. Bạn thử đổi giờ hoặc số người nhé.", "rooms": []}
    room = choices[0]
    duration_hours = (state["end"] - state["start"]).total_seconds() / 3600
    state.update({"room_id": room["id"], "cost": round(room["hourly_rate"] * duration_hours)})
    start_local, end_local = state["start"].astimezone(), state["end"].astimezone()
    summary = (f"{room['name']} cho {state['people']} người, ngày {start_local:%d/%m/%Y}, {start_local:%H:%M}–{end_local:%H:%M} "
               f"dự kiến {state['cost']:,}₫. Phòng đủ sức chứa, còn trống và có giá thấp nhất trong các lựa chọn.")
    if re.search(r"\b(xác nhận|đồng ý|đặt phòng|confirm)\b", message, re.I):
        if state.get("pending_confirm"):
            try:
                booking = create_booking(BookingIn(room_id=room["id"], start=state["start"], end=state["end"], people=state["people"], idempotency_key="chat-" + uuid.uuid4().hex), identity)
            except HTTPException as exc:
                return {"reply": "Không thể tạo booking: " + str(exc.detail), "rooms": choices}
            state.clear()
            return {"reply": "Đã tạo booking " + booking["id"][:8] + ". Trạng thái CONFIRMED.", "booking": booking}
        state["pending_confirm"] = True
        return {"reply": "Trước khi đặt, hãy xác nhận đề xuất: " + summary + " Nhắn ‘xác nhận’ để tiếp tục.", "rooms": choices}
    state["pending_confirm"] = True
    return {"reply": "Đề xuất: " + summary + " Nếu phù hợp, nhắn ‘xác nhận’ để đặt.", "rooms": choices}


@app.get("/api/admin/audit")
def get_audit(identity: dict[str, str] = Depends(current_user)):
    require_admin(identity["role"])
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200").fetchall()]


@app.patch("/api/admin/rooms/{room_id}")
def update_room(room_id: int, values: dict[str, Any], identity: dict[str, str] = Depends(current_user)):
    require_admin(identity["role"])
    allowed = {"name", "capacity", "amenities", "hourly_rate"}
    if not values or set(values) - allowed:
        raise HTTPException(400, "Chỉ cập nhật name, capacity, amenities, hourly_rate")
    if "amenities" in values and not isinstance(values["amenities"], list):
        raise HTTPException(400, "amenities phải là danh sách")
    if ("capacity" in values and int(values["capacity"]) < 1) or ("hourly_rate" in values and int(values["hourly_rate"]) < 0):
        raise HTTPException(400, "Sức chứa hoặc giá không hợp lệ")
    update = {k: json.dumps(v, ensure_ascii=False) if k == "amenities" else v for k, v in values.items()}
    clause = ",".join(k + "=?" for k in update)
    with db() as c:
        result = c.execute("UPDATE rooms SET " + clause + " WHERE id=?", (*update.values(), room_id))
        if result.rowcount == 0:
            raise HTTPException(404, "Không tìm thấy phòng")
        audit(c, "admin", "ROOM_UPDATED", "room=" + str(room_id))
        return {"ok": True, "room_id": room_id}


@app.get("/api/admin/bookings")
def admin_bookings(identity: dict[str, str] = Depends(current_user)):
    require_admin(identity["role"])
    with db() as c:
        return [dict(r) for r in c.execute("SELECT b.*,r.name room_name FROM bookings b JOIN rooms r ON r.id=b.room_id ORDER BY b.created DESC").fetchall()]
