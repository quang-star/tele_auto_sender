# Tele Auto Sender

Công cụ gửi tin nhắn Telegram lặp lại tự động bằng tài khoản cá nhân (qua **Telethon**), hỗ trợ **tự động dừng ngay lập tức** khi phát hiện từ khóa dừng trong cuộc trò chuyện. Có cả giao diện đồ họa (**GUI** bằng `tkinter`) và chế độ dòng lệnh (**CLI**).

---

## Tính năng chính

- **Đăng nhập 1 lần (Hỗ trợ cả Quét QR & OTP)**: Đăng nhập siêu nhanh bằng nút **📷 Quét QR** (không cần đợi mã OTP/SMS) hoặc mã OTP + xác thực 2 lớp (2FA), tự lưu phiên đăng nhập (`my_telegram_account.session`) và thông số cấu hình (`user_config.json`).
- **Chọn cuộc trò chuyện linh hoạt**: Tải danh sách chat gần đây (User, Group, Channel, Bot, Saved Messages) để chọn nhanh, hoặc nhập trực tiếp `@username`, `me`, số điện thoại, hay ID chat.
- **Tự động dừng theo từ khóa**: Lắng nghe tin nhắn mới theo thời gian thực và dừng ngay khi gặp bất kỳ từ khóa nào bạn cấu hình.
- **Giảm rủi ro giới hạn tài khoản**: Hỗ trợ khoảng nghỉ giữa mỗi tin (`delay_seconds`, tối thiểu 1.5s), cộng thêm thời gian ngẫu nhiên (`jitter_seconds`), giới hạn số tin tối đa (`max_messages`), và tự động tạm nghỉ khi gặp `FloodWaitError` từ Telegram.
- **Hỗ trợ cả GUI và CLI**: Dễ dùng cho mọi người với cửa sổ trực quan hoặc chạy nhanh trong terminal.

---

## Yêu cầu hệ thống

- Windows (khuyến nghị) với **Python 3.13+** (hoặc Python 3.10+ có sẵn `tkinter`).
- Thư viện: `telethon>=1.36.0` (trong `requirements.txt`).

---

## Bước 1: Lấy `API_ID` và `API_HASH` (chỉ mất ~1 phút)

Để công cụ kết nối với Telegram bằng tài khoản của bạn, hãy tạo một ứng dụng cá nhân trên trang chính thức của Telegram:

1. Truy cập **https://my.telegram.org** và đăng nhập bằng số điện thoại Telegram của bạn (nhập mã xác nhận gửi về app Telegram).
2. Chọn mục **API development tools**.
3. Điền form **Create new application**:
   - **App title**: Tên tiếng Anh không dấu 6–15 ký tự (VD: `MyDesktopHelper`, không chứa chữ `Telegram`)
   - **Short name**: Chữ thường + số viết liền 6–15 ký tự (VD: `mydeskhelper26`)
   - **URL**: Để trống
   - **Platform**: Chọn `Desktop`
   - **Description**: Để trống
   *(Mẹo: Nếu gặp báo lỗi `ERROR`, hãy tắt VPN / 1.1.1.1 WARP rồi thử lại).*
4. Bấm **Create application** và sao chép 2 giá trị:
   - **`App api_id`** (dãy số nguyên) -> `API_ID`
   - **`App api_hash`** (chuỗi ký tự) -> `API_HASH`

> **Lưu ý bảo mật:** Không chia sẻ `API_ID`, `API_HASH`, file `my_telegram_account.session` hoặc `user_config.json` cho người khác.

---

## Bước 2: Cài đặt & Chạy chương trình

### Cách 1: Chạy giao diện đồ họa (GUI) — Khuyên dùng

- **Nhấp đúp vào `run.bat`** (hoặc chạy `run.bat` trong Command Prompt).
  - Script sẽ tự kiểm tra Python, tự cài `telethon` từ `requirements.txt` nếu chưa có, rồi mở giao diện GUI.
- Hoặc chạy trực tiếp bằng Python (sau khi đã `pip install -r requirements.txt`):
  ```bat
  python main.py
  ```

### Cách 2: Chạy trên dòng lệnh (CLI)

```bat
python main.py --cli
```
Hoặc chạy trực tiếp:
```bat
python cli_runner.py
```

Trong chế độ CLI:
- Nhập `API_ID`, `API_HASH`, số điện thoại + OTP (+ mật khẩu 2FA nếu có) ở lần đầu.
- Chọn cuộc hội thoại bằng số thứ tự `[1..N]` hoặc gõ `@username` / ID / nhấn `Enter` để dùng lại chat gần nhất.
- Nhấn `Ctrl+C` bất cứ lúc nào để dừng gửi và ngắt kết nối an toàn.

---

## Cơ chế tự động dừng theo từ khóa

- **Từ khóa dừng (`stop_keywords_raw`)**: Nhập một hoặc nhiều từ khóa cách nhau bằng dấu phẩy `,` (hoặc xuống dòng trong GUI), ví dụ: `ok, dậy rồi, dừng lại`.
- **Chỉ dừng khi người khác nhắn (`only_incoming`)**:
  - Khi bật (khuyên dùng): Công cụ chỉ kiểm tra tin nhắn từ người/bên kia gửi tới, bỏ qua tin nhắn do chính bạn gửi ra (tránh tự kích hoạt dừng nếu nội dung bạn gửi vô tình chứa từ khóa).
  - Khi tắt: Kiểm tra cả tin nhắn của chính bạn lẫn người khác.
- **Phân biệt chữ HOA / thường (`case_sensitive`)**: Mặc định tắt (`ok` khớp với `OK`, `Ok`).
- **Khớp toàn bộ tin nhắn (`whole_message_match`)**:
  - Khi tắt (mặc định): Chỉ cần từ khóa xuất hiện bên trong tin nhắn (ví dụ từ khóa `dậy rồi` khớp với `"ừ mình dậy rồi nè"`).
  - Khi bật: Toàn bộ nội dung tin nhắn (sau khi bỏ khoảng trắng đầu/cuối) phải trùng khít với từ khóa.

---

## Lưu ý an toàn để tránh `FloodWait` và khóa tài khoản

- **Không đặt delay quá thấp**: Nên để khoảng nghỉ tối thiểu **5–15 giây** mỗi tin (ứng dụng ép cứng tối thiểu `1.5s`), kết hợp thêm **random jitter** (VD `2–5 giây`) để nhịp gửi tự nhiên hơn.
- **Đặt giới hạn số tin (`max_messages`)**: Khi thử nghiệm hoặc gửi lúc vắng mặt, nên đặt giới hạn hợp lý (VD `20` hoặc `50`) thay vì để vô hạn.
- **Không spam người lạ hoặc nhiều nhóm cùng lúc**: Việc gửi lặp lại tới người lạ có thể khiến người nhận bấm *Report Spam* và dẫn tới tài khoản bị Telegram hạn chế gửi tin. Chỉ dùng cho liên hệ quen biết, nhóm nội bộ hoặc `Saved Messages` (`me`).
- **Khi gặp `FloodWait`**: Chương trình sẽ tự tạm nghỉ đúng số giây Telegram yêu cầu trước khi tiếp tục; không nên tắt nóng rồi chạy lại liên tục ngay lập tức.
