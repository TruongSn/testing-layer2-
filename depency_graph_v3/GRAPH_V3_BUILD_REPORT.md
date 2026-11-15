# Graph v3 build và audit

## Đã chạy thật

Build CPU bằng `python -m lifemate_graph.cli build`, Python 3.13 local. Đã đọc và audit mọi tool, mọi JSONL record và toàn bộ artifacts/notebooks cung cấp trong workspace. Không tìm source ở repository khác.

11/11 file trong manifest phục hồi khớp cả byte count và SHA-256. Hai input catalog/train có mặt dù README_RECOVERED ghi thiếu; README gốc được giữ nguyên. SHA hai input khớp source digests trong graph v2/v2.1.

| Hạng mục | Thực tế |
| --- | ---: |
| Apps / tools | 46 / 147 |
| Train JSONL rows / unique IDs | 1.545 / 1.545 |
| Gold calls / history calls đã scan | 3.541 / 3.661 |
| Gold failed results / history failed results | 0 / 0 |
| Legacy structural edges | 392 |
| Legacy metadata / empirical candidates | 75 / 86 |
| Legacy manual review rows / curated edges | 161 / 20 |
| Legacy schema_fields / alternative branches | 143 / 12 |

`train.jsonl` được nhận diện từ nội dung, parse từng dòng JSON object; không chuyển thành JSON array và không đổi file nguồn. Gold calls đều qua schema subset audit. Có 1 lỗi history: `train_1060`, `calendar.list_events`, argument `date` không nằm trong catalog hiện tại. Lỗi được báo, không tự sửa hoặc bỏ dòng.

## Graph được tạo

Audit coverage: **147/147 tools**, kể cả standalone tools không có dependency được chứng minh. Audit lưu description, kind, full input schema/nested fields, required/optional, x-ref/x-one-of, output contract/observed types, resources, provenance policy và preconditions. Không đánh đồng tất cả ID với bắt buộc READ: user có thể cung cấp ID trực tiếp.

190 dependency candidates được chuẩn hóa theo producer/consumer/target. Tập này không phải hợp mù của legacy lists; mỗi record giữ mọi evidence row và quyết định cũ.

| Technical status | Số cạnh |
| --- | ---: |
| VERIFIED | 72 |
| NEEDS_REVIEW | 77 |
| INSUFFICIENT_EVIDENCE | 35 |
| REJECTED | 6 |

Số đếm có thể tái lập từ `artifacts/graph_manifest.json` và review CSV. VERIFIED chỉ là technical status. Các quan hệ semantic yếu, order-only, nested parent/row selection và membership chưa có binding thực thi được tiếp tục chờ review. Không coi source field trùng tên là đủ: cycle/membership, assignee/members, channel/channels và nested playlist IDs đã được phân biệt trong code/tests.

**Runtime-enabled dependency coverage: 0 cạnh / 0 consumer tools** ở production artifact mặc định. **20/20 curated edges giữ nguyên PENDING/runtime_enabled=false**. Không tự cấp quyền các cạnh nhạy cảm. Runtime grants dùng trong tests hoàn toàn synthetic, scope-locked và không ghi vào production graph.

## Artifacts

`artifacts/` chứa đủ: full graph, registry audit, tool coverage CSV, review queue, validation report, approved runtime graph, compiled graph và manifest. Thêm `legacy_provenance_v3.json` giữ toàn bộ raw legacy artifacts và `source_integrity_report.json` giữ kết quả integrity.

Manifest ghi version, input/legacy source SHA-256, implementation SHA-256, output file hashes, graph content digest, compiled payload digest và hai coverage độc lập. Runtime loader kiểm artifact/manifest/payload/catalog/scope/index consistency. Build hai lần vào hai thư mục tạm cho output byte-identical; tests kiểm điều này.

## Giới hạn và trạng thái hoàn thành

Đã có builder, validator, compiler, resolver, execution-loop integration, functional tests, demo và notebook launcher. Đây là bản triển khai có thể chạy được. Chưa có operator grants cho graph thật, GPU/model/ToolEnv run, independent evaluation hoặc bằng chứng cải thiện accuracy/latency.

Catalog không có output schema đầy đủ. Các paths được kiểm trên observations train/history, không chứng minh bao phủ mọi live response variant. Discovery được quét toàn catalog và toàn observations hiện có; không tuyên bố đã khám phá mọi quan hệ semantic có thể có. Các quan hệ chưa đủ evidence được giữ để review, không bịa dependency thay thế.

Phần rollout runtime thật còn chờ operator review và Kaggle validation. Vì vậy không tuyên bố toàn bộ Full Graph v3 đã được cấp quyền hoặc kiểm chứng production.
