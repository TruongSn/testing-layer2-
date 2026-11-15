# CPU functional verification

## Đã chạy

`python scripts/run_cpu_checks.py` chạy suite unittest và ghi từng test ID, pass/failure/skip, thời gian, platform và source hashes vào `reports/cpu_test_results.json`; log đầy đủ ở `reports/cpu_tests.txt`. Local có **74 tests, PASS, không skip**. Các assert suites gốc B042/B041/recipient được gọi bên trong integration tests, không cộng chúng như 74 test độc lập mới.

Đã chạy mọi code cell của notebook B043 bằng fresh CPU process (`scripts/verify_b043_notebook.py`). Notebook chạy self-tests và demo, ghi gpu_status=NOT_RUN, tạo ZIP hợp lệ. Trong notebook standalone, 3 tests rebuild/integrity trên raw train skip có lý do vì raw train không nhúng; các tests này đã chạy trong workspace đầy đủ. Chi tiết nằm ở `reports/notebook_verification.json`.

## Các hành vi được chứng minh

| Nhóm | Bằng chứng |
| --- | --- |
| Không READ dư | Đủ grounded arguments; user literal; alternative phone đã đáp ứng |
| Missing prerequisite | Resolver trả NEED_READ với query có exact user-span provenance |
| Evidence reuse | Observation đúng goal/epoch điền ID; policy write không cần lặp READ |
| Multi-hop | READ regions → READ people với region_id đã grounded → Policy send, tất cả qua original invoke |
| Multi-goal | Hai goals cùng tool, hai người/nội dung khác nhau; không trộn ID/value |
| Wrong subject/value | ID thuộc người khác bị block, kể cả ID xuất hiện trong clause goal khác |
| Ambiguity | Hai records cùng tên hoặc duplicate rows đều abstain |
| No-progress | Failed/empty/reserved READ không được lặp vô hạn |
| Cycles/budgets | Cycle detection, depth/read/cost limits dừng an toàn |
| Tool availability | Producer không installed không được schedule |
| Freshness | Successful WRITE làm evidence stale; failed/no-op WRITE không tự tạo epoch mới |
| Conditions | unknown/false goal không bật graph action |
| ToolEnv validation | READ bị ToolEnv.validate từ chối không đi tới env.call |
| Existing safety | Recipient/amount guard suites gốc chạy qua B043 loop và pass |
| Duplicate WRITE | Policy đề xuất lại WRITE thành công bị B042ProgressGuard chặn |
| Rollback/shadow | Scripted baseline và shadow có cùng public trace; unapproved tools dùng baseline |
| Build integrity | All tools, node/edge types, legacy decisions, source hashes, indexes, catalog/scope validation |
| Reproducibility | Hai full builds byte-identical; sources giữ nguyên |

`reports/closed_loop_demo.json` chứa sáu synthetic trajectories: baseline, shadow, assisted, ambiguous, empty, failed. Assisted chạy `demo.people(query=Mai)` rồi `demo.send(contact_id=p_mai, content=hello)` trong execution loop B042 có hooks. Baseline/shadow thiếu ID bị schema path chặn. Ambiguous/empty/failed không có WRITE.

Đây là **scripted Policy + FakeToolEnv**, không phải Qwen inference hoặc official ToolEnv. Các grants synthetic không phải phê duyệt business edges thật. Test thành công chứng minh cơ chế và các counterexamples được kiểm, không chứng minh hiểu đúng mọi request.

## Chưa kiểm chứng

- Kaggle GPU, pinned Qwen3.5-4B load/generation và live zaic_tools contract.
- GPU parity 92 trajectories: không chạy và không phải prerequisite.
- Official fuzzy/state accuracy, true goal completion, wrong-subject/unsafe executed WRITE counts.
- Accuracy/latency improvement hoặc independent evaluation. Benchmark launcher đã có, chưa có kết quả thật.
- General nested-array subject construction, generic membership predicates và derived/history bindings: hiện abstain/review.

Không có tuyên bố production safety/accuracy dựa riêng vào synthetic tests. File machine-readable luôn là nguồn chính xác cho số tests và version code được chạy.
