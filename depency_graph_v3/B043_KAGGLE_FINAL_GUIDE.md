# Chạy B043 Final trên Kaggle

1. Import `LifeMate_B043_Full_Graph_Resolver_Kaggle.ipynb`. Chọn GPU, bật Internet hoặc dùng model cache đã chuẩn bị. Model là Qwen3.5-4B non-thinking, cùng revision/config B042; phiên bản packages thực tế được ghi vào run manifest.
2. Attach BTC train dataset hợp lệ có `train.jsonl`, tool catalog, `zaic_tools` và `train_environment`. Notebook tự tìm dataset bằng cơ chế B042. Không chỉnh sửa hoặc đọc thủ công environment files.
3. **Run All.** Source và graph 59 cạnh đã nhúng sẵn; không cần upload ZIP runtime riêng. Nếu muốn dataset riêng, giải nén `B043_Final_Graph_Kaggle.zip` trên máy, tạo **private Kaggle dataset** từ các thư mục bên trong. Đặt `B043_GRAPH_DIR` ở cell cấu hình bằng đường dẫn `/kaggle/input/<dataset-slug>/artifacts_final`. Graph ngoài phải khớp checksum với graph đã test trong notebook.
4. Giữ `B043_MODES = ['GRAPH_DISABLED','GRAPH_ASSISTED_SAFE_READ']`, `B043_CASE_IDS = None`, `B043_SAMPLE_LIMIT = 40`. Năm cases đã báo lỗi chạy trước; các ca còn lại được chọn có seed theo single/multi-tool/app/hop, ambiguous, missing-info, no-op và READ→WRITE. Không chạy Shadow hoặc parity-92.
5. Tải `LifeMate_B043_final_outputs.zip` ở cell cuối. Có CPU report, GPU status, benchmark report, per-case predictions/private traces, manifest và checkpoints. Nếu GPU fail, ZIP vẫn được lưu cùng traceback/checkpoints.

Kiểm tra `gpu_status.json` có `status=COMPLETE`, `gpu_executed=true`; `benchmark_report.json` có `graph_smoke_passed=true` và `cases_with_graph_reads > 0` ở nhánh assisted. Trong traces, prerequisite có `origin=b043_graph_read`; các WRITE phải có `origin=model_policy`. Xem thêm resolver proofs, goal ID, observation sequence, write epoch, call order và ToolEnv validation.

State hash thực thi luôn được lưu. Nếu BTC cung cấp full-state reference hashes độc lập, upload file JSON `{ "train_xxxx": "hash..." }` và đặt `B043_REFERENCE_HASHES` bằng đường dẫn file trước khi Run All. Không dùng hash của partial `label.final_state`. Khi chưa có reference/evaluator, State Match và official score là null; fuzzy-call F1 được đánh dấu proxy, không phải điểm thi chính thức.

Hai nhánh dùng cùng seed/model/settings, budget 30s/sample và tối đa 16k input+output. Thời gian model load/install nằm ngoài per-sample inference; CUDA/tool call có thể vượt budget khi đang chạy nên phải kiểm tra `percentage_exceeding_30s`. Chưa có chứng minh hard-deadline trên GPU.

Rerun cùng cấu hình sẽ resume checkpoints. Đổi code/graph/model/packages/settings thì đổi `B043_WORKDIR`, tránh checkpoint cũ. Rollback bằng `B043_MODES = ['GRAPH_DISABLED']`; B042 gốc vẫn nằm trong `inputs/`. Có thể chạy B042 gốc độc lập nếu cần rollback hoàn toàn notebook mới.
