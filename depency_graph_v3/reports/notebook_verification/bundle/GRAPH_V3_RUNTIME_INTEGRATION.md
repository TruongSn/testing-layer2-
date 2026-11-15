# B043 runtime integration

## Execution path

`User request → Goal Planner → Policy/graph-assisted preflight → BackwardDependencyResolver → existing validators/guards → original invoke/ToolEnv.validate → ToolEnv.call → existing ledger + graph ledger → GoalTracker → B042ProgressGuard → next decision`.

`load_b042()` kiểm digest notebook và thực thi definitions cells 1–6, không chạy cell 7. Hook transformer phải tìm đúng một vị trí cho mỗi attach/observe/proposal/preflight/legacy-preflight/recovery hook; sai cấu trúc thì fail closed. Bản sao loop được đặt tên `b043_run_one_sample`; baseline gốc và `_B042_BASELINE_RUN_ONE_SAMPLE` giữ nguyên.

`runtime_item()` allowlist inputs trước khi tạo env/planner/policy/ledger. Labels, expected calls, final/future/hidden state không đi vào runtime. Gold chỉ dùng để tính call metrics sau khi loop hoàn tất. History tool_results đã xảy ra là hợp lệ; graph ledger hiện không tự nhập history thiếu goal/write-epoch provenance. Baseline ledger vẫn xử lý history theo B042.

## Feature flags và quyền

| Mode | Hành vi |
| --- | --- |
| GRAPH_DISABLED | Gọi `run_one_sample` gốc B042; không dùng graph hooks để quyết định |
| GRAPH_SHADOW | Bản sao loop có diagnostics; không đổi proposed args, prompt hoặc tool execution |
| GRAPH_ASSISTED_SAFE_READ | Chỉ approved VERIFIED edges đúng scope; có thể tạo READ hoặc bổ sung grounded args; WRITE vẫn từ Policy |

Compiled artifact mặc định không có grants nên assisted mode dùng scheduling/planner baseline. Với grants và một active goal có validated graph context, graph coordinator sở hữu prerequisite scheduling cho goal đó; legacy `preflight_read_candidate()` và guarded auto-recovery không chạy song song. Khi active goal không có approved route/context, legacy scheduling tiếp tục. Các **guard checks** vẫn giữ: grounding/amount, additional grounding, condition monitor, recipient check, empty-read guard, progress/duplicate WRITE và schema validation.

Khi graph không đủ điều kiện lập READ an toàn, không đoán subject/query/ID. Đưa feedback cho Policy hoặc tiếp tục path baseline của tool không có approved incoming edge. Legacy hardcoded safety rules không bị xóa. Việc tắt legacy automatic preflight trong assisted trajectories chỉ thay scheduling; validators tương ứng vẫn chặn action thiếu evidence. Cần Kaggle regression validation trước rollout.

Graph preflight chỉ dùng goal có validated explicit spans/bindings, condition=true và approved READ route. Chỉ chuẩn bị goal pending đầu tiên để tránh prefetch evidence của goal tiếp theo rồi làm stale ngay sau WRITE. Proposed-call resolution có thể gọi một chuỗi bounded READs rồi trả lại chính target proposal cho original validators. Mọi READ đi qua original `invoke()` và ToolEnv.validate; READ budget không vượt call/deadline limits của B042.

Qwen B043 planner được bổ sung `graph_context` tùy chọn. Khi không thể xác định semantic intent, planner có thể bỏ context và fallback. Hints chỉ chứa các dependency liên quan; full available tool set/schema vẫn còn. Runtime không dùng Qwen để duyệt graph.

## Operator approval workflow

1. Review `graph_review_queue_v3.csv` cùng catalog, Spec và paths/evidence trong full graph.
2. Tạo bản sao `approvals.example.json`; điền graph content SHA, reviewer và scope PRODUCTION.
3. Mỗi decision APPROVED phải chỉ rõ edge_id VERIFIED, **đúng source_output_path**, selector_keys đã review, read_cost, reason và producer_argument_bindings nếu cần. Không cấp APPROVED hàng loạt theo support score.
4. Build sang thư mục mới, ví dụ:

```powershell
python -m lifemate_graph.cli build --approvals operator_approvals.json --out artifacts_reviewed
```

Ví dụ cấu trúc decision để operator tự điền sau review:

```json
{
  "edge_id": "COPY_VERIFIED_EDGE_ID",
  "decision": "APPROVED",
  "source_output_path": "COPY_EXACT_SOURCE_PATH",
  "selector_keys": ["name"],
  "read_cost": "LOW",
  "producer_argument_bindings": {"query": {"subject_key": "name"}},
  "reason": "Operator review rationale"
}
```

Không áp dụng mapping `query` cho producer không có argument này. List tool không cần arguments có thể dùng `{}`. Producer required arguments không grounded hoặc không giải được qua upstream dependency thì resolver abstain.

Compiler từ chối stale graph digest, unknown/duplicate edges, unverified edge, WRITE producer, source mismatch, selector key không hợp lệ hoặc condition chưa được runtime hỗ trợ. Old graph decisions không bị sửa: approvals là overlay riêng có provenance. Synthetic grants không thể nạp với production scope.

## Rollback và giới hạn

Đổi mode về GRAPH_DISABLED để quay lại B042; không cần sửa graph gốc. Không tự đổi flag dựa trên test synthetic. Benchmark báo ROLLBACK_RECOMMENDED nếu exact-call F1 giảm; safety/completion thật vẫn cần adjudication.

Multi-goal evidence tách theo goal/epoch. Scalar field dependencies được hỗ trợ; nested array construction, multi-entity collection selection và generic state predicates chưa tự chạy. Current request spans được hỗ trợ; graph chưa tự suy derived time, số tiền nói bằng chữ hoặc history subject binding. B041 amount repair và guards cũ vẫn đảm nhiệm phạm vi của chúng.

Việc tái dùng một observation chỉ được phép khi đúng goal, subject và epoch. Đây là lựa chọn bảo thủ; có thể phát sinh READ lại giữa hai goals sau WRITE. Chưa có số liệu GPU latency hoặc accuracy để kết luận lợi ích tổng thể.
