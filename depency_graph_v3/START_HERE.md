# START HERE — LifeMate Layer 2 / B043

Cập nhật **15/11/2026**, theo source/reports trong workspace `E:\Hackathon\depency_graph_v3`. Bản hiện tại dùng **59 dependencies**; pilot 19 cạnh là lịch sử. Bước tiếp theo: paired GPU benchmark, rồi tối ưu theo số đo.

## 1. Kiến trúc và một lượt request

Luồng chính:

```text
Request + history/context được phép dùng
  → Goal Planner / Qwen3.5-4B non-thinking
  → GoalTracker + GoalContext có provenance
  → Graph preflight khi đủ chắc chắn; nếu cần thì gọi Decision Policy
  → Resolver kiểm tra proposal: evidence → missing slots → approved READ
  → Validators / Guards B042 → ToolEnv.validate → ToolEnv.call
  → Evidence Ledger + GoalTracker + Progress/STOP Controller → lặp hoặc kết thúc
```

`B043Bridge` phối hợp Graph với legacy preflight. Thiếu `graph_context` có thể phục hồi từ source spans/observations. Resolver dùng compiled indexes, kiểm tra subject, alternatives, nested row/parent, goal và epoch; không đủ căn cứ thì abstain/fallback. Policy READ và Graph READ dùng chung evidence; WRITE thành công làm evidence stale. Depth/read/retry budgets và STOP feedback đều có giới hạn.

**Graph chỉ tự thực thi prerequisite READ đã được phép. WRITE do Policy đề xuất, qua guards B042 và ToolEnv.validate.** GoalTracker chỉ ghi candidate completion.

## 2. Điểm vào và vị trí code

| Thành phần | File / symbol cần đọc |
|---|---|
| GPU entry point | [LifeMate_B043_Full_Graph_Resolver_Kaggle.ipynb](LifeMate_B043_Full_Graph_Resolver_Kaggle.ipynb); launcher `lifemate_graph/kaggle.py::run_benchmark()` |
| Harness / coordinator | `lifemate_graph/b043.py`: `B043Harness.run()`, `B043Bridge`, `install_hooks()` |
| Qwen, Planner, Policy, loop gốc | `inputs/LifeMate_B042_Progress_Retry_Controller_Kaggle.ipynb`: `load_policy_model()`, `generate_once()`, `make_goals()`, `policy_messages()`, `b042_run_one_sample()`. `load_b042()` nạp definitions pin SHA; B043 tạo bản loop có hooks |
| Inference có budget | `lifemate_graph/inference.py::bounded_generate()`; benchmark áp cùng settings cho hai nhánh |
| Context / resolver / evidence | `lifemate_graph/context.py`; `lifemate_graph/runtime.py`: `BackwardDependencyResolver`, `GraphRuntimeController`, `EvidenceLedger` |
| Graph source / runtime | `artifacts_completed/full_dependency_graph_v3.json` + `operator_approvals.json`; runtime nạp `artifacts_final/compiled_graph_v3.json` và `graph_manifest.json` |
| Offline tooling | `lifemate_graph/compiler.py`, `validation.py`, `cli.py`; `scripts/complete_graph.py`. CLI `compile` không chạy discovery |
| ToolEnv | SDK BTC `zaic_tools` do B042 `configure_dataset()` tìm/import. `invoke()` gọi validate rồi call. CPU: `tests/fixtures.py::FakeToolEnv` |
| Metrics / tests / notebook build | `lifemate_graph/evaluation.py`, `tests/test_final_graph.py`, `scripts/run_cpu_checks.py`, `scripts/make_b043_notebook.py` |

Flags: `GRAPH_DISABLED` = B042; `GRAPH_SHADOW` = quan sát; `GRAPH_ASSISTED_SAFE_READ` = hỗ trợ READ. Constructor mặc định **SHADOW**; notebook chọn hai nhánh Disabled/Assisted rõ ràng.

## 3. Chạy kiểm thử và benchmark

Từ workspace, với Python 3.10+ trên PATH; CPU suite dùng standard library, không cần GPU/SDK:

```powershell
python scripts/run_cpu_checks.py
python -m unittest tests.test_final_graph -v
```

Kết quả: `reports/cpu_test_results.json`, `reports/cpu_tests.txt`. Traces production-graph: `reports/final/approved_contract_traces_PRIVATE.jsonl`, `cpu_paired_traces_PRIVATE.jsonl`.

**Kaggle:** import notebook B043, bật GPU và Internet/model cache; attach BTC dataset có train JSONL, catalog, `zaic_tools`, `train_environment`. Source/graph đã nhúng; **Run All** với:

```python
B043_MODES = ['GRAPH_DISABLED', 'GRAPH_ASSISTED_SAFE_READ']
B043_CASE_IDS = None
B043_SAMPLE_LIMIT = 40
```

Smoke 5 cases phải có graph READ mới tiếp tục tập đại diện. Hai nhánh cùng model/revision, seed, ToolEnv, settings; budget 30s/sample, tối đa 16k input+output. CUDA/tool call đang chạy có thể vượt budget; kiểm tra overruns.

Tải `LifeMate_B043_final_outputs.zip`: GPU status, benchmark report, predictions/traces, manifest/checkpoints. Kiểm tra `status=COMPLETE`, `graph_smoke_passed=true`, assisted `cases_with_graph_reads > 0`; READ origin `b043_graph_read`, WRITE origin `model_policy`. Đổi code/graph/settings thì đổi `B043_WORKDIR`. Xem [Kaggle guide](B043_KAGGLE_FINAL_GUIDE.md).

## 4. Baseline mới nhất và lỗi đã biết

Các số dưới đây lấy từ reports đã lưu, không phải lần chạy mới khi viết tài liệu này.

| Phạm vi | Kết quả |
|---|---|
| Catalog / candidates | 147 tools, 46 apps; 190 candidates |
| Technical status | 80 VERIFIED / 70 NEEDS_REVIEW / 34 INSUFFICIENT / 6 REJECTED |
| Runtime coverage | 59 enabled dependencies, 54 consumer tools; 21 VERIFIED còn tắt |
| CPU local | 132 tests: **131 PASS, 0 FAIL, 1 SKIP** discovery rebuild |
| Notebook Run All CPU | 132 tests: **129 PASS, 3 SKIP** vì không nhúng raw train |
| Production-graph CPU closed loops | 57 data contracts + 2 context contracts; 5/5 scenarios có tổng **9 graph READ**, **0 graph-generated WRITE** |
| GPU final B042/B043 | **NOT_RUN**; accuracy, State Match, official score và latency đều chưa đo |

Nguồn: `reports/cpu_test_results.json`, `reports/notebook_verification.json`, `reports/final/{cpu_mechanism_report,benchmark_report}.json`. CPU dùng scripted Policy/recorded observations. Exact-call F1 là debug; fuzzy F1 là proxy. State Match cần authoritative full-state hashes, không hash partial `label.final_state`.

Lỗi có regression: thiếu context → graph_reads=0; STOP sớm (`train_0136`); argument/subject mismatch (`train_0463`); sai track, thiếu đọc chat (`train_0951`); Policy READ sai goal gây đọc lặp; phone tồn tại nhưng sai contact; nested quantity/parent, duplicate/stale evidence. Giữ ca đúng `train_0488`, `train_0557`. Cơ chế đã PASS CPU; chưa xác nhận Qwen GPU hoặc xếp hạng bottleneck GPU.

Chưa hỗ trợ đủ: temporal derivation, appliance-cycle/assignee membership, order/restaurant và reward/program joins, tham chiếu thiếu identity, một số đường nhạy cảm. Chúng abstain/fallback; xem `artifacts_completed/candidate_completion.csv`.

## 5. Phạm vi sửa và các bất biến

**Có thể sửa khi triển khai tiếp:** runtime/context/coordinator, resolver/evidence, compiler/validator, Policy/inference adapter, benchmark/metrics, tests, notebook generator; ưu tiên lỗi tái hiện được. Giữ scope approval; đổi graph cần evidence/approval phù hợp, compile qua CLI vào output mới/rỗng, không bật cờ bằng tay. Sửa runtime phải cập nhật manifest, regenerate notebook, verify payload/checksum và đóng gói lại: `scripts/make_b043_notebook.py`, `verify_b043_notebook.py`, `package_final_delivery.py`.

**Giữ nguyên:** `inputs/` (train vẫn JSONL), B042 đã pin SHA, `previous_graph/`, `reference_notebooks/`, `artifacts/`, snapshot `artifacts_reviewed/`. Chỉnh Qwen/Policy qua adapter/bản dẫn xuất. Bảo toàn Validators/Guards, conditional logic, GoalTracker, progress/replay controls, semantics ToolEnv. Không dùng gold/future/hidden state ở runtime, không graph-generated WRITE, không mở cạnh REJECTED hoặc dùng synthetic grants cho production. Chưa làm Graph v4, refactor lớn, fine-tuning, parity-92. Mở quyền dữ liệu nhạy cảm/tác dụng phụ cần quyết định riêng. Rollback: `GRAPH_DISABLED`. Chỉ sửa trong workspace này.
