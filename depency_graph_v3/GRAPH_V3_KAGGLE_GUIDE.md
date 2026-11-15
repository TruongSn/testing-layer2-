# Chạy B043 trên Kaggle

## Chuẩn bị

Mở `LifeMate_B043_Full_Graph_Resolver_Kaggle.ipynb`. Notebook nhúng source, tests, compiled graph, catalog và notebook B042 đã kiểm SHA; không cần tìm repository LifeMate hoặc upload Python package riêng.

Attach một bộ BTC train hợp lệ chứa `train.jsonl`, `train_tools_catalog.json`, `zaic_tools` và `train_environment`. Không attach nhiều bản train trùng nhau: loader kế thừa B042 yêu cầu đúng một cặp. Nếu chỉ có dữ liệu nghiên cứu local thì chỉ chạy được CPU self-tests/demo; không tạo giả ToolEnv thay SDK thật cho benchmark.

Bật GPU và Internet nếu chưa có model/dependencies cache. Model ID `Qwen/Qwen3.5-4B`, revision `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` và 4-bit loading path được kế thừa nguyên từ B042. Library requirements có trong `requirements-kaggle.txt`; launcher lưu exact versions thực tế. GPU/library compatibility chưa được xác minh local.

## Cấu hình cell đầu

- `B043_GPU='auto'`: có CUDA thì chạy GPU stage; `'0'`: CPU-only; `'1'`: yêu cầu GPU, thiếu thì báo FAILED.
- `B043_CASE_IDS`: mặc định 5 train-development cases, mỗi case chạy ba modes. Không tự chạy parity-92.
- `B043_MODES`: GRAPH_DISABLED, GRAPH_SHADOW, GRAPH_ASSISTED_SAFE_READ.
- `B043_GRAPH_DIR`: để trống dùng embedded graph 0 grants. Sau operator review, attach thư mục `artifacts_reviewed` và đặt đường dẫn vào biến này.
- `B043_WORKDIR`: mặc định `/kaggle/working/lifemate_b043`. Dùng output directory mới khi thay model/code/graph/data/config.

Để đánh giá graph thật có tác động, phải review grants theo `GRAPH_V3_RUNTIME_INTEGRATION.md`, build và nạp đúng manifest/compiled pair cùng catalog digest. Không bật PENDING edges bằng cách sửa runtime flag thủ công. Khi 0 edges được phê duyệt, report ghi `graph_assistance_possible=false`; không diễn giải assisted arm như một graph rollout.

## Run All

1. Verify/extract embedded payload và graph checksums.
2. Chạy CPU functional tests, baseline/shadow/assisted FakeToolEnv demos.
3. Nếu CUDA được bật, discover SDK/data qua loader B042, kiểm catalog SHA rồi nạp Qwen một lần.
4. Mỗi trajectory tạo đúng một ToolEnv; các modes của cùng case phải có initial state hash bằng nhau. Không reset/clone trong trajectory, không đọc hidden state.
5. Ghi checkpoint sau mỗi case/mode. Rerun cùng fingerprint tái dùng checkpoint; stale fingerprint bị từ chối.
6. Lưu reports, private traces, predictions và output ZIP. Nếu GPU stage lỗi, ZIP vẫn chứa lỗi và partial checkpoints trước khi notebook báo FAILED.

Local CPU Run All đã được thực thi. Điều này chưa chứng minh Kaggle GPU Run All thành công: SDK/env/model không có trên máy local.

## Đọc output

`LifeMate_B043_outputs.zip` chứa `cpu/`, `gpu_status.json`, và `benchmark/` khi benchmark thật chạy. `run_manifest.json` lưu code/input/graph/model digests, seed, GPU và package versions. `traces_PRIVATE.jsonl` có debug/gold phục vụ hậu kiểm, không publish; policy/resolver không nhận các labels này.

Benchmark đo exact-call F1, tool-name F1, counts, tokens, planner/policy/total latency, graph overhead, repeated READ và repairs. `state_accuracy`, official score, true goal completion, subject/unsafe-write correctness và các semantic rates để null nếu chưa có official evaluator/adjudicator. GoalTracker completion chỉ là candidate evidence.

Train đã dùng trong discovery nên các runs này là **train-development**, không phải independent evaluation. Dùng cùng inputs/cases/model/settings giữa hai arms, review regression theo case và đo held-out độc lập trước rollout. Nếu correctness giảm, report khuyến nghị rollback; GRAPH_DISABLED giữ baseline B042.

## Tái tạo notebook và chạy CPU local

```powershell
python -m lifemate_graph.cli build
python scripts/run_cpu_checks.py
python scripts/make_b043_notebook.py
python scripts/verify_b043_notebook.py
```

Notebook tự chứa package tại thời điểm generation. Sau thay đổi source/artifacts, phải generate lại. ZIP riêng để bàn giao workspace được tạo bởi `scripts/package_delivery.py`; không bao gồm raw train hoặc hidden environments.
