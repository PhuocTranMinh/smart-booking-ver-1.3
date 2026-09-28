# Smart Study Room — Capstone demo

Bản demo theo đề tài đặt phòng học thông minh tích hợp chatbot, QR và mô phỏng IoT.

## Chạy trên Windows / PowerShell

Cần Python 3.9 trở lên. Trong PowerShell, chuyển vào thư mục này và chạy:

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app:app --reload
```

Không cần kích hoạt `.venv` bằng `Activate.ps1`. Mở http://127.0.0.1:8000 và đăng nhập bằng một tài khoản demo:

| Vai trò | Tên đăng nhập | Mật khẩu |
|---|---|---|
| Người dùng | `user` | `demo123` |
| Administrator | `admin` | `admin123` |

## Tính năng

- Đăng nhập demo phân vai User/Administrator.
- User tìm phòng, chọn ngày/giờ và thời lượng, đặt trực tiếp hoặc dùng chatbot. Chatbot hỏi đủ số người, ngày tương lai, giờ và thời lượng; ngày quá khứ bị từ chối. Khi chatbot tạo booking sau khi user xác nhận, QR hiện trong popup. QR cũng có nút mở lại từ booking chưa check-in.
- Chatbot hiểu ngày như `29/09/2026`, `2026-09-29`, `ngày 29 tháng 9`, hôm nay/ngày mai và thứ trong tuần; thời lượng như `90 phút`, `2 tiếng`, `2 tiếng 30 phút`.
- Chatbot trả lời yêu cầu giới thiệu loại phòng bằng danh sách phòng, sức chứa, tiện nghi và giá lấy từ database.
- Booking được chống xung đột bằng transaction SQLite; QR ký HMAC, gắn booking/phòng và thời hạn.
- Admin dashboard hiển thị trạng thái phòng, occupancy, controller online/offline, thiết bị, booking và audit log; dữ liệu tự làm mới mỗi 10 giây.
- Admin có thể mô phỏng trạng thái occupancy của controller, chỉnh tên phòng, sức chứa, tiện nghi và giá.

## API chính

- `POST /api/auth/login`, `GET /api/auth/me`
- `GET /api/rooms`, `GET /api/availability?start=...&end=...&people=...`
- `POST /api/bookings`, `GET /api/bookings`, `POST /api/bookings/{id}/cancel`
- `POST /api/chat` với `{ "message": "phòng 4 người ngày mai lúc 14h trong 2 tiếng", "session_id": "demo" }`
- `GET /api/qr/image?token=...`, `POST /api/qr/validate`
- `POST /api/iot/telemetry`
- `GET /api/admin/bookings`, `GET /api/admin/audit`, `PATCH /api/admin/rooms/{id}`

Ngày giờ API dùng ISO 8601. Database SQLite được tạo tự động ở lần chạy đầu. Có thể đặt `QR_SECRET` và `ROOM_DB` làm biến môi trường.

## Giới hạn

Chatbot dùng rule/regex, chưa gọi mô hình ngôn ngữ lớn. IoT hiện mô phỏng qua HTTP, chưa nối ESP32, MQTT, PIR hoặc relay thật. Tài khoản demo và phiên đăng nhập chỉ dành cho demo; session lưu trong bộ nhớ và mất khi khởi động lại server. Bản này chưa thực hiện bộ kiểm thử nghiệm thu trong đề cương.
