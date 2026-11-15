# B043 — GPU pilot với 19 dependency đã duyệt

1. Upload `B043_Runtime_Graph_Reviewed.zip` thành Kaggle dataset **private**. ZIP chứa thư mục `artifacts_reviewed/`; không chứa raw train hoặc hidden environment.
2. Import notebook `LifeMate_B043_Full_Graph_Resolver_Kaggle.ipynb`. Attach bộ BTC train/catalog/zaic_tools/environment đã dùng cho smoke trước, cùng reviewed dataset. Bật GPU và Internet nếu model/dependencies chưa có cache.
3. Trong cell cấu hình, đặt đường dẫn thực tế tới folder có compiled graph và manifest:

```python
B043_GRAPH_DIR = '/kaggle/input/<reviewed-dataset>/artifacts_reviewed'
B043_MODES = ['GRAPH_ASSISTED_SAFE_READ']
B043_GPU = '1'
```

Nếu Kaggle vẫn mount nguyên ZIP, giải nén trước rồi trỏ biến đến thư mục đã giải nén:

```python
from zipfile import ZipFile
with ZipFile('/kaggle/input/<reviewed-dataset>/B043_Runtime_Graph_Reviewed.zip') as archive:
    archive.extractall('/kaggle/working/b043_reviewed')
B043_GRAPH_DIR = '/kaggle/working/b043_reviewed/artifacts_reviewed'
```

Notebook cũng nhúng **cùng graph PRODUCTION 19 cạnh**. Để `B043_GRAPH_DIR=None` sẽ dùng bản nhúng; external graph phải có cùng payload checksum để tránh chạy khác graph đã kiểm thử.

4. **Run All.** Startup phải in `Production runtime-approved edges: 19`. Notebook kiểm payload, toàn bộ graph manifest, catalog digest và đúng tập 19 IDs; sau đó chạy CPU gates trên production graph, rồi nạp Qwen3.5-4B theo loading path B042.
5. Kiểm `graph_reads`, `origin=b043_graph_read`, SATISFIED proof đúng edge_id/goal_id/epoch trong traces. Graph chỉ tự READ; mọi WRITE phải có `origin=model_policy` và validation/guard path. Duplicate/ambiguity phải abstain. Valid ID/evidence không tạo prerequisite READ mới.

Mặc định chỉ chạy active smoke với 5 train-development cases, không chạy lại Shadow/parity benchmark. Dùng output directory mới nếu thay code/graph/model để không trộn checkpoint cũ. Raw labels không vào Policy/Resolver.

Reports, traces, checkpoint và `LifeMate_B043_outputs.zip` được lưu dưới `/kaggle/working/lifemate_b043`. Nếu GPU stage lỗi, ZIP vẫn giữ log và partial checkpoints. Ba raw-input tests skip trong notebook standalone vì không nhúng raw train; bản local đã kiểm integrity/schema, còn test discovery rebuild được skip có chủ đích.

Active GPU với bản source mới chưa được kiểm thử local; chưa kết luận lợi ích accuracy/latency. Rollback bằng `B043_MODES=['GRAPH_DISABLED']` nếu pilot có regression.
