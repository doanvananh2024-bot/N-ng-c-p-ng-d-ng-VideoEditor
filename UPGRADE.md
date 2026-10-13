# Video Editor Independent 1.3.2 — 13/10/2026

VideoEditor.exe là bản đối chiếu bố cục. Bản được nâng cấp là **Video Editor Independent**.

## Đường dẫn

`C:\Users\doanv\AppData\Local\Programs\Video Editor Independent\versions\1.2.0\VideoEditorIndependent.exe`

Thư mục vẫn mang tên `1.2.0` để đường dẫn và shortcut hiện có hoạt động; bản mới hiển thị **v1.3.2**. Bộ cài cũ được sao lưu cùng cấp trước khi thay thế. Dữ liệu dự án và thiết lập trong `%LOCALAPPDATA%\VideoEditorIndependent` được giữ nguyên.

## Sửa dịch và lồng tiếng

- Sửa lỗi `dict() got multiple values for keyword argument original_text` khi dịch lại phụ đề đã có metadata. Giữ thời gian, nội dung gốc và giọng gán từng câu.
- Nút Dịch và Lồng tiếng chạy lại bước đã bấm, tạo kết quả mới, giữ các tệp cũ. Nhận dạng chạy cho video thiếu phụ đề nếu đã bật Trích phụ đề.
- Ngọc Huyền V2 đọc đúng câu có dấu `...` và `…`; dấu câu không còn tạo đoạn âm thanh rỗng.
- Phụ đề song ngữ có metadata khớp sẽ dùng phần dịch để đọc. Phụ đề hiển thị vẫn giữ hai ngôn ngữ; nếu người dùng đã sửa nội dung, bản chỉnh được ưu tiên.
- Sửa vòng đời hộp thoại gây lỗi `overlay` và lỗi cập nhật trạng thái. Thông báo lỗi lồng tiếng ghi số câu, thời điểm và giọng cần kiểm tra.
- Kiểm tra cấu hình dịch trước khi chạy; các nút trong trình sửa phụ đề dùng cùng công cụ dịch, giọng và thiết bị đã chọn trong Pipeline.

Đã tạo WAV đầy đủ từ cả hai tệp phụ đề lỗi của người dùng (73 câu mỗi tệp) bằng CUDA, khoảng 159 giây mỗi tệp. Các API có phí được kiểm tra bằng phản hồi giả lập; không gọi tài khoản thật. Google Translate công khai hiện trả HTTP 429 trên kết nối thử; ứng dụng ghi rõ giới hạn dịch vụ. Không tự đổi nhà cung cấp, model hoặc khóa của người dùng.

## Bản sửa Thành phần theo ảnh mới

- **Thành phần → Lớp**: năm nút cyan thêm Văn bản, Phương tiện, Vùng mờ, Bộ lọc, Âm thanh. Danh sách gom thành các nhóm thu gọn và hiển thị số lớp.
- Mã lớp: **V** video gốc, **I0…** phương tiện, **S0/S1** phụ đề gốc/dịch, **T0…** tiêu đề, **B0…** vùng mờ, **F0…** bộ lọc, **A0/A1** âm gốc/lồng tiếng và **A2…** âm thanh thêm. Bấm hàng hoặc mũi tên để mở Chi tiết; công tắc và nút xóa có tác dụng trên đúng lớp. Các lớp nền không thể xóa.
- S0/S1 có kiểu chữ, vị trí và bật/tắt riêng. Xem trước và xuất đều dùng các thiết lập đó. Trước khi chỉnh S0/S1, dự án cũ giữ cách hiển thị một bản phụ đề; sau khi chỉnh có thể hiển thị cả hai. Áp dụng cho tất cả giữ tệp phụ đề của từng video.
- Nhóm thu gọn, lớp đã chỉnh, trạng thái bật/tắt và kiểu từng bản phụ đề được giữ khi chuyển video và lưu/mở dự án.
- Video gốc có xoay tự do **−180…180°**, tỉ lệ **5…500%**; vẫn giữ bộ điều khiển xoay 90° cũ.
- Phông giao diện VE UI của ảnh tham chiếu được dùng với bản quyền và [SIL Open Font License](https://github.com/bettergui/BeVietnamPro/blob/main/OFL.txt) đi kèm tại `_internal/ui_fonts/OFL.txt`. Các tệp font không bị thay đổi.
- Danh sách giọng offline nhận các model hoàn chỉnh đã cài. Khi tệp `ngoc_huyen.onnx` bản thường không còn nhưng Ngọc Huyền Premium đã cài, ID cũ dùng model Premium tương ứng; bảng giọng và trạng thái ghi tên thực tế. Ngọc Huyền V2 giữ model riêng.

Kiểm thử S0/S1 dùng nội dung khác nhau ở hai vị trí và kiểm màu thực tế trong bốn trường hợp bật/tắt, cả trên QWidget xem trước và EXE xuất video. Kiểm thử GUI bao gồm năm nút thêm, chuyển chi tiết, công tắc, xóa, thu gọn, chuyển video và lưu/mở dự án. Bố cục đối chiếu ảnh ở sidebar 300 px, tỉ lệ 125%; chưa chứng nhận mọi màn hình/tính năng của hai phần mềm đồng nhất 100%.

## Chức năng bổ sung theo bản đối chiếu

- **Thành phần → Video gốc**: vị trí, tỉ lệ chung/ngang/dọc, lật dọc, độ rõ, nền đen/màu/mờ. Xem trước và xuất dùng cùng thông số. Nền mờ xử lý ở đủ độ phân giải màu để khớp ảnh xem trước.
- **Thành phần → Thêm lớp**: nhiều lớp âm thanh và bộ lọc riêng; chỉnh thời gian, bật/tắt, nhân đôi/xóa. Các lớp âm chỉnh tệp, lặp, âm lượng và fade. Bộ lọc có cường độ và khoảng tác dụng riêng.
- **Chữ / ảnh**: tám chế độ hòa trộn, mười hiệu ứng chữ, nền ôm chữ/theo khung, chuyển động vòng quanh/ngang/bật chéo và tự đổi mỗi 15 giây. Chữ và phụ đề dùng cùng bộ dựng kiểu chữ.
- **Sửa phụ đề → AI sửa**: chọn phạm vi, nhập kịch bản, xem câu gốc/đề xuất, chọn câu áp dụng. Nội dung chưa đổi cho tới khi nhấn Áp dụng; giữ thời gian, giọng riêng và hoàn tác.
- **Pipeline → Phụ đề**: chọn GPU/CPU/Tự động và batch 1–12 cho Whisper. GPU và batch 2 đã nhận đúng câu tiếng Anh bằng model small đã cài; model được dùng lại từ thư mục stt. Tự động có thể lùi về CPU khi khởi tạo GPU không thành công.
- **Giọng đọc**: thêm Kokoro offline từ bộ 54 giọng hiện có, Google TTS, MiniMax, SiliconFlow, Vbee, Zalo và cấu hình cầu nối CapCut. Hai giọng Ngọc Huyền và xử lý A0/A1 của v1.2.2 được giữ.

## Kết nối bằng tài khoản/API riêng

Vào **Thiết lập → Khóa API → Lồng tiếng** hoặc nút cấu hình ở bảng Giọng đọc. Khóa lưu bằng Windows DPAPI; app không lấy khóa/tài khoản từ VideoEditor.

| Dịch vụ | Cấu hình cần nhập |
|---|---|
| MiniMax | API key; model tùy chọn, mặc định speech-2.8-hd |
| SiliconFlow | API key; model tùy chọn, mặc định FunAudioLLM/CosyVoice2-0.5B |
| Vbee | Access token và App ID; API sync hỗ trợ các giọng được nhà cung cấp công bố |
| Zalo | API key riêng |
| CapCut | Endpoint của cầu nối do bạn vận hành, token tùy chọn; GET /v2/speakers và POST /v2/synthesize trả âm thanh |
| Google TTS | Không cần khóa; cần Internet |

Đã đối chiếu API tại [MiniMax](https://platform.minimax.io/docs/api-reference/speech-t2a-http), [SiliconFlow](https://docs.siliconflow.cn/docs/api/audio-speech-post), [Vbee](https://api-docs.vbee.vn/vbee-api/text-to-speech/realtime-api), [Zalo](https://ai.zalo.solutions/docs/api/text-to-audio-converter) và [gTTS](https://gtts.readthedocs.io/en/latest/module.html). Các bộ nối trả phí được kiểm tra bằng HTTP mô phỏng và giải mã MP3 thật; chưa gọi tài khoản trả phí của bạn. CapCut dùng hợp đồng cầu nối độc lập, không phải API công khai do CapCut xác nhận.

## Phạm vi còn khác bản đối chiếu

Đây là bản triển khai các chức năng tương ứng, chưa chứng nhận sao chép toàn bộ hay trùng 100% từng pixel. `feature-matrix.json` và `parity-audit.md` ghi các nhóm đã đối chiếu cùng phần còn thiếu.

Máy chủ tách giọng, đăng nhập/tài khoản trình duyệt, bản quyền và máy chủ cập nhật của VideoEditor là hệ thống riêng; chúng chưa được chuyển sang Independent. Chưa có toàn bộ kho tải/gỡ từng giọng và các engine STT riêng của bản gốc. Kokoro được kiểm tra bằng giọng Anh; chưa xác nhận cách phát âm Nhật/Trung của frontend eSpeak giống model tham chiếu. Các hiệu ứng màu cần xem bản xuất để đánh giá đầy đủ.

## Ba bảng âm thanh theo ảnh

- Chọn video → **Thành phần → Lớp → A0 Âm gốc**: chỉnh Giọng gốc, Trống / nhịp, Âm trầm (bass), Nhạc nền; Fade vào / ra áp dụng riêng cho âm gốc. Bốn thanh hiện khi chọn **Pipeline → Lồng tiếng → Video gốc → Máy này → Chi tiết**.
- Chọn **A1 Lồng tiếng**: chỉnh âm lượng giọng mới và Fade vào / ra riêng. Checkbox của A0/A1 bật/tắt từng lớp.
- **Pipeline → Lồng tiếng** gồm Giọng đọc, Video gốc, Giọng lồng. Luồng song song tạo nhiều câu trong một video, giữ đúng giọng và thời điểm của từng câu; GPU được xếp hàng để hạn chế bộ nhớ.
- **Giọng đều** giữ tốc độ giọng cố định; **Khớp theo câu** tăng tốc khi cần để câu không vượt mốc kế tiếp. Tốc độ video thay đổi thời điểm phụ đề và giọng mới; giọng mới không bị thay đổi tốc độ hai lần.
- Dự án mới có các giá trị trong ảnh: A0 24/100/100/0%, A1 126%, video 0.90x, giọng 1.20x, song song 3. Dự án đã lưu giữ thiết lập của nó. Bảng sửa cách đọc chọn từ các bảng người dùng tạo trong Kịch bản; nội dung bảng không được tự tạo.

Các mức âm và fade được kiểm bằng tín hiệu tổng hợp có tần số riêng cho từng lớp, qua FFmpeg thực tế. Thiết lập được lưu theo video và dự án. Các lớp âm tách riêng cần xuất từng video trước khi nối hàng loạt.

## Bố cục

- Khung ngoài/khoảng cách 8 px, đệm lưới 10 px, khoảng cách thẻ 12 px, bảng bên phải 300 px.
- Thanh trên gồm Thiết Lập, Kịch bản, Cắt ghép, Tải xuống, Điều khoản; vùng phiên bản/ngôn ngữ/trạng thái nằm bên phải.
- Thẻ video: header 44 px, thanh tua 12 px, footer 40 px. Header có tên/checkbox/trạng thái/tiến độ; footer có tỉ lệ, tua ±5 giây, phát, đồng hồ, toàn màn hình và áp dụng.
- Pipeline, Thành phần, Tinh chỉnh video và các nhóm điều khiển theo vị trí của bản tham chiếu. Tìm kiếm/chọn hàng loạt vẫn có trong menu dự án/thẻ video.
- Hộp thoại dùng kích thước đối chiếu, cửa sổ nổi và nền tối 70%.

Đã kiểm tra cửa sổ QWidget thật ở 1600×920 và 1920×1080. Chưa xác nhận trùng 100% từng pixel/màu/kiểu chữ vì công cụ chụp cửa sổ tham chiếu không trả được khung hình. Tên ứng dụng, phiên bản và trạng thái chủ sở hữu thuộc bản Independent.

## Hai giọng offline

Vào **Pipeline → Lồng tiếng**, bấm ô **Giọng đọc**, chọn engine ở cột trái, bấm **▶** để nghe thử và **Xác nhận** để dùng.

| Engine | ID | Mô hình có sẵn |
|---|---|---|
| Ngọc Huyền (offline) | `ngochuyen:ngoc_huyen` | `tts\ngochuyen\ngoc_huyen.onnx` |
| Ngọc Huyền V2 (offline) | `ngochuyen_v2:ngoc_huyen_v_2` | `tts\ngochuyen_v2\ngoc_huyen_v_2.onnx` |

Mô hình được đọc tại `%LOCALAPPDATA%\VideoEditor\tts`; không cần chạy VideoEditor.exe hoặc nhập API. V2 dùng model riêng, vì tệp `ngochuyen_v2\ngoc_huyen.onnx` trùng model v1.

Giọng dùng cho toàn bộ phụ đề hoặc từng câu, với căn thời gian/tốc độ/độ trễ và nhiều người đọc. Nội dung cần là tiếng Việt. V2 báo rõ từ/ký hiệu chưa hỗ trợ để sửa trong bảng cách đọc. Edge/FPT/ElevenLabs vẫn có, dùng Internet và thông tin riêng của người dùng.

## GPU

Mở **Hiệu năng** trong menu Tài khoản/công cụ hoặc **Pipeline → Hệ thống**. Chọn Tự động, GPU (NVIDIA) hoặc CPU cho hai giọng offline và tách giọng.

- Tách giọng dùng pack `%LOCALAPPDATA%\VideoEditor\voicesep\smartvoice_cuda` và checkpoint đã có.
- Giọng/OCR dùng ONNX Runtime GPU được đóng gói và thư viện `%LOCALAPPDATA%\VideoEditor\cuda`.
- GPU được chọn rõ ràng thì báo lỗi khi CUDA không hoạt động; Tự động có thể dùng CPU và báo trong tiến độ.
- Thiết bị encode chọn riêng trong Hệ thống; Whisper chọn thiết bị riêng tại Pipeline → Phụ đề.

Không đổi PATH hệ thống, không xóa mô hình dùng chung. Model TTS được giữ trong cache; tác vụ GPU được xếp hàng để hạn chế nạp nhiều model đồng thời.

## Kiểm chứng và giới hạn

QA dùng dữ liệu tổng hợp, không gọi dịch vụ trả phí. Có kiểm thử CUDA thật với hai câu tiếng Việt, tách giọng thật, hủy, cache, nhiều giọng, bảo vệ đầu ra và fallback; kiểm thử FFmpeg và bytecode/bộ xử lý của `.exe` cuối cùng. Các báo cáo đi kèm ghi kết quả từng phép thử.

Frontend V2 dùng các bảng âm vị/thanh của pack. Thứ tự âm đầu–vần–thanh được đối chiếu bằng giọng tổng hợp và Whisper offline; chưa có tài liệu huấn luyện gốc cho toàn bộ từ vựng.
