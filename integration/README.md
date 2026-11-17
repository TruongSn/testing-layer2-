# LifeMate từ WAV đến tool calls

`run_lifemate.py` nối Whisper Turbo INT8 với Layer 2 B043. Runner nhận WAV của yêu cầu mới và lịch sử người dùng, chuẩn hóa tiếng Việt theo catalog của split rồi chuyển transcript, context khả dụng và lịch sử sang B043. B043 dùng Qwen3.5-4B và ToolEnv của split. Lịch sử đã thực hiện được dùng làm ngữ cảnh.

## Chuẩn bị

- Dùng Python 3.11 trở lên. Cài PyTorch CUDA và các thư viện trong `depency_graph_v3/requirements-kaggle.txt` cho Layer 2.
- Giải nén gói Whisper Turbo INT8 của nhóm. Thư mục gốc của gói có `inference.py`, `normalizer.py`, `audio.py` và `model/model.bin`. Cài `requirements.txt` của gói trong môi trường Python dành cho ASR.
- Chuẩn bị dữ liệu được cấp quyền. `--data-root` trỏ đến thư mục chứa `train` và/hoặc `public_test`. Mỗi split cần JSONL, catalog, `zaic_tools`, thư mục môi trường và các WAV tham chiếu.
- Runner dùng notebook B043 Final có sẵn trong `depency_graph_v3/`, kiểm tra SHA-256 và nạp runtime từ payload nhúng. Qwen dùng revision pin trong B043 và chạy trên GPU CUDA đầu tiên.

## Chạy một mẫu

Chạy từ thư mục repo và thay các đường dẫn trong lệnh bằng đường dẫn trên máy chạy.

```powershell
python -B integration/run_lifemate.py --data-root "D:\path\competition-data" `
  --split train --sample-id train_0001 `
  --asr-release "D:\path\lifemate_asr_turbo_release" `
  --asr-python "D:\path\asr-env\Scripts\python.exe" `
  --output "runs/lifemate-train-0001"
```

ASR mặc định chạy CPU. Có thể dùng `--asr-device cuda` khi môi trường ASR có CUDA/cuDNN phù hợp. Chọn `--split public_test` và bỏ `--sample-id` để xử lý toàn bộ split. Mỗi lần chạy dùng thư mục output mới.

## Chạy riêng Layer 2 với transcript đã lưu

Dùng `--asr-result <asr.json>` để lấy lại transcript từ lượt chạy trước. Ví dụ trên Linux/Kaggle, chọn GPU và gắn dữ liệu cùng transcript qua nguồn riêng tư có quyền truy cập.

```bash
python -B integration/run_lifemate.py \
  --data-root "/path/to/competition-data" \
  --split train --sample-id train_0001 \
  --asr-result "/path/to/saved/asr.json" \
  --output "/kaggle/working/lifemate-train-0001"
```

Chế độ này cần JSONL, catalog, `zaic_tools` và thư mục môi trường của split để chạy simulator. Transcript đã lưu cung cấp nội dung của các lượt audio. Ngân sách Layer 2 là 30 giây cho mỗi mẫu.

## Đầu ra

- `submission.jsonl` chứa ID, các tool call thực tế và state hash do ToolEnv cung cấp.
- `asr.json` chứa transcript và confidence để đối chiếu.
- `traces.jsonl` ghi các bước và lý do dừng của từng mẫu. `run_manifest.json` ghi cấu hình và danh tính dữ liệu/model cho lượt chạy.

Giữ gói ASR, dữ liệu cuộc thi, transcript và output của các lượt chạy trong phạm vi nhóm.
