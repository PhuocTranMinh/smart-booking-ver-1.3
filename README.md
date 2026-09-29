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
- User tìm phòng, chọn ngày/giờ và thời lượng, đặt trực tiếp hoặc mở bong bóng chat nổi ở góc dưới bên phải. Chatbot chào người dùng mới, có gợi ý hỏi về dịch vụ, loại phòng, bảng giá hoặc bắt đầu đặt; người dùng quen có thể nhập thẳng yêu cầu. Chatbot hỏi đủ số người, ngày tương lai, giờ và thời lượng; ngày quá khứ bị từ chối. Khi chatbot tạo booking sau khi user xác nhận, QR hiện trong popup. QR cũng có nút mở lại từ booking chưa check-in.
- Thẻ phòng dùng minh họa SVG riêng cho phòng học, phòng thảo luận, phòng lab và phòng nhóm; lưới giữ chiều cao thống nhất.
- Chatbot hiểu ngày như `29/09/2026`, `2026-09-29`, `ngày 29 tháng 9`, hôm nay/ngày mai và thứ trong tuần; thời lượng như `90 phút`, `2 tiếng`, `2 tiếng 30 phút`.
- Chatbot trả lời yêu cầu giới thiệu loại phòng bằng danh sách phòng, sức chứa, tiện nghi và giá lấy từ database.
- Booking được chống xung đột bằng transaction SQLite; QR ký HMAC, gắn booking/phòng và thời hạn.
- Thiết bị trong phòng gồm đèn (`lamp`), quạt (`fan`) và loa (`speaker`). Check-in QR gửi lệnh bật cả ba; Admin xem trạng thái telemetry và bật/tắt từng thiết bị ngay trên dashboard.
- MQTT dùng cho telemetry, trạng thái và lệnh thiết bị. HTTP API tiếp tục hỗ trợ mô phỏng và các thao tác request/response như login, booking, cập nhật phòng.
- Admin dashboard hiển thị occupancy, controller online/offline, trạng thái đèn/quạt/loa, booking và audit log; dữ liệu tự làm mới mỗi 10 giây.
- Admin có thể mô phỏng trạng thái occupancy của controller, chỉnh tên phòng, sức chứa, tiện nghi và giá.

## API chính

- `POST /api/auth/login`, `GET /api/auth/me`
- `GET /api/health` báo đang dùng `firestore` hay SQLite local.
- `GET /api/rooms`, `GET /api/availability?start=...&end=...&people=...`
- `POST /api/bookings`, `GET /api/bookings`, `POST /api/bookings/{id}/cancel`
- `POST /api/chat` với `{ "message": "phòng 4 người ngày mai lúc 14h trong 2 tiếng", "session_id": "demo" }`
- `GET /api/qr/image?token=...`, `POST /api/qr/validate`
- `POST /api/iot/telemetry` nhận HTTP telemetry mô phỏng; payload có thể gồm `devices: {"lamp": true, "fan": false, "speaker": true}`.
- `POST /api/admin/rooms/{id}/devices` để Admin cập nhật trạng thái thiết bị; backend gửi lệnh MQTT nếu có broker, nếu không thì cập nhật mô phỏng HTTP.
- `GET /api/admin/bookings`, `GET /api/admin/audit`, `PATCH /api/admin/rooms/{id}`

Ngày giờ API dùng ISO 8601. Khi chưa đặt credential Firebase, app dùng SQLite để chạy local như trước. Khi đặt `FIREBASE_SERVICE_ACCOUNT_JSON`, Firestore của project `smart-booking-82438` trở thành nguồn dữ liệu chính cho phòng, booking, thiết bị, sự kiện và audit. Nếu Firestore chưa có phòng, lần khởi động đầu sẽ chuyển dữ liệu SQLite local hiện có; nếu không có database cũ, app tạo bốn phòng mẫu. SQLite cũ sẽ được thêm cột `speaker_on`. `QR_SECRET` và `ROOM_DB` vẫn là biến môi trường tùy chọn.

### Cấu hình Firebase/Firestore

Trong Google Cloud Console của project, tạo service account riêng cho app và cấp quyền dữ liệu tối thiểu **Cloud Datastore User** (`roles/datastore.user`). Tạo khóa JSON cho service account; khóa này chỉ đặt ở môi trường server, không commit lên GitHub và không gửi qua chat. Firebase Admin/server credentials truy cập Firestore bằng IAM; Firestore Security Rules không thay thế quyền IAM của server. [Hướng dẫn Firebase Admin SDK](https://firebase.google.com/docs/admin/setup) · [Quyền IAM Firestore](https://docs.cloud.google.com/firestore/docs/security/iam)

Trên Render, cách khuyến nghị là tải JSON lên mục **Environment → Secret Files** với tên `firebase-service-account.json`, sau đó đặt `GOOGLE_APPLICATION_CREDENTIALS=/etc/secrets/firebase-service-account.json`. Đặt thêm `FIREBASE_PROJECT_ID=smart-booking-82438` và redeploy. Có thể thay Secret File bằng secret environment variable `FIREBASE_SERVICE_ACCOUNT_JSON` chứa toàn bộ JSON. Local PowerShell có thể đặt biến cho phiên hiện tại trước khi chạy app:

```powershell
$env:FIREBASE_PROJECT_ID = "smart-booking-82438"
$env:FIREBASE_SERVICE_ACCOUNT_JSON = Get-Content -Raw .\firebase-service-account.json
.venv\Scripts\python.exe -m uvicorn app:app --reload
```

Không đưa file service-account vào ZIP/repository. `/api/health` trả `firestore_connected: true` sau khi xác nhận đọc được Firestore. Nếu credential chưa cấu hình, ứng dụng tự dùng SQLite local; khi dùng Firestore, bản ghi phòng/booking không còn phụ thuộc filesystem tạm của Render.

## MQTT cho thiết bị

Đặt các biến môi trường sau để kết nối broker. Nếu `MQTT_BROKER` chưa được đặt, app chạy demo HTTP mà không cần MQTT.

| Biến | Ý nghĩa |
|---|---|
| `MQTT_BROKER` | Hostname broker, ví dụ hostname cấp bởi MQTT service |
| `MQTT_PORT` | Cổng broker; mặc định 8883 khi TLS bật, nếu không là 1883 |
| `MQTT_TLS` | `true` bật TLS (mặc định), `false` cho kết nối không TLS |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | Tài khoản broker (nếu broker yêu cầu) |
| `MQTT_CLIENT_ID` | Client ID backend, mặc định `smart-room-backend` |

Topic dùng tiền tố `rooms/{room_id}`; tên thiết bị là `lamp`, `fan`, `speaker`:

| Topic | Hướng | Nội dung |
|---|---|---|
| `rooms/{room_id}/telemetry` | Controller → backend | Telemetry tổng hợp: `{"online":true,"occupied":true,"devices":{"lamp":true,"fan":false,"speaker":true}}` |
| `rooms/{room_id}/status` | Controller → backend | Trạng thái phòng/thiết bị hiện tại, cùng định dạng JSON |
| `rooms/{room_id}/command` | Backend → controller | Lệnh thiết bị: `{"command_id":"…","devices":{"lamp":true,"fan":true,"speaker":true},"at":"…"}` |
| `rooms/{room_id}/devices/{device}/status` | Controller → backend | Trạng thái riêng, ví dụ `rooms/1/devices/lamp/status` với `{"on":true}` |
| `rooms/{room_id}/devices/{device}/command` | Dành cho lệnh riêng từng thiết bị | Có thể dùng khi triển khai controller; demo hiện gửi lệnh nhóm trên topic `/command` để tránh thiết bị nhận lặp lệnh |

Backend đăng ký nhận ba loại topic telemetry/status ở trên; lệnh nhóm gửi QoS 1, không retained. Controller cần đăng ký subscribe `rooms/+/command` và phản hồi status sau khi thực thi lệnh. Ví dụ telemetry có thể gửi định kỳ mỗi 5–10 giây hoặc mỗi khi trạng thái thay đổi.

## Public lên Render

File `render.yaml` đã có sẵn. Push project lên GitHub, vào Render chọn **New → Blueprint**, kết nối repo và chọn branch. Nếu project nằm trong thư mục con, đặt Root Directory thành `outputs/smart-room-demo` (hoặc đường dẫn thực tế chứa `render.yaml`). Render sẽ dùng build/start command và tạo `QR_SECRET` tự động. Sau khi deploy xong, mở URL `onrender.com` do Render cấp.

Nếu chưa cấu hình credential Firestore, Render chạy SQLite trên filesystem tạm nên booking và cấu hình có thể mất khi spin down/restart/deploy. Khi `FIREBASE_SERVICE_ACCOUNT_JSON` đã được cấu hình hợp lệ, dữ liệu nghiệp vụ được lưu trên Firestore và tồn tại qua các lần deploy.

## Giới hạn

Chatbot dùng rule/regex, chưa gọi mô hình ngôn ngữ lớn. MQTT backend đã sẵn sàng kết nối broker; để thiết bị vật lý hoạt động cần cấu hình broker và firmware ESP32/thiết bị subscribe/publish đúng topic. Đăng nhập demo và phiên đăng nhập vẫn lưu trong bộ nhớ, nên session mất khi khởi động lại server. Bản này chưa thực hiện bộ kiểm thử nghiệm thu trong đề cương.
