# Giao diện theo ảnh tham chiếu

Bản cập nhật điều chỉnh khung làm việc và bốn bảng Pipeline: Phụ đề, Dịch thuật,
Lồng tiếng, Hệ thống. Dùng nền tối, màu cyan, các nhóm thu gọn, công tắc, thanh
trượt có ô nhập giá trị, nút Thêm video màu xanh lá và thanh tác vụ phía dưới.

Phông Be Vietnam Pro được phân phối nguyên bản trong `ui_fonts/` cùng giấy phép
SIL OFL. Giữ cả `ui_fonts/` và `ui_icons/` bên cạnh `main.py`; cấu hình PyInstaller
đã được bổ sung thư mục biểu tượng.

## Chạy và kiểm tra

Dùng Python 3.12 trong môi trường đã cài PySide6 6.8.3 và các thư viện của dự án:

```text
python main.py
python verify_reference_layout.py --screenshots qa/reference-layout
python verify_independent.py --ui-only
python verify_translate_dub_ui.py
python verify_audio_reference.py --ui-only
```

Trên Linux không có màn hình, đặt `QT_QPA_PLATFORM=offscreen`. Để chụp đúng tỉ lệ
ảnh mẫu, đặt `QT_SCALE_FACTOR=1.25`: cửa sổ 1536 × 800 tạo ảnh 1920 × 1000, không
gồm thanh tiêu đề và taskbar Windows.

Kiểm chứng: 6 kiểm tra giao diện/đồng bộ cấu hình, 2 kiểm tra nhập và xuất video,
13 kiểm tra dịch/lồng tiếng với nhà cung cấp mô phỏng, 1 kiểm tra lưu/mở thiết lập
âm thanh đều đạt. Các kiểm tra media dùng FFmpeg thật. Dịch vụ AI trực tuyến và
GPU thật cần kiểm tra trong môi trường có cấu hình tương ứng.

## Phạm vi bản này

Tên phiên bản, trạng thái chủ sở hữu, giọng, model, engine và thiết bị hiển thị
theo khả năng/cấu hình thực tế của Independent. Các tùy chọn Whisper/OCR, API,
thư mục xuất và tiếp tục tác vụ nằm trong các nhóm có thể mở rộng.

Đã xem ảnh kết quả trên Linux ở tỉ lệ 125% và kiểm tra bố cục ở cả 1536 × 800 lẫn
1120 × 690. Chưa chứng nhận trùng từng pixel trên Windows hoặc các cửa sổ chưa
có ảnh mẫu.

Đây là bản mã nguồn. EXE trên máy Windows chưa được cập nhật. Muốn thử tại máy,
giải nén vào thư mục mới, dùng môi trường Python của dự án và chạy `main.py`.
Đóng gói EXE cần FFmpeg, các dependency AI và các tài nguyên/đường dẫn Windows
được nêu trong `VideoEditorIndependent.spec`; bước đóng gói chưa được chạy trên
môi trường Linux này.
