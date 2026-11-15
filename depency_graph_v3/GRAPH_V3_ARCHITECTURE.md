# Graph v3: kiến trúc và hợp đồng

## Ranh giới

`builder.py` là pipeline offline duy nhất đọc train labels/tool_results để audit và tìm candidates. `runtime.py`, `compiler.py` phần loader và `b043.py` không import train miner. Runtime chỉ nhận request đã lọc, goals, proposed arguments, available tools và observations đã xảy ra. Không đọc environment state hoặc kết quả tương lai.

Không có Python package refactor B042 trong workspace được cung cấp. `b043.py` nạp definitions của notebook B042 gốc, kiểm SHA-256, bỏ cell benchmark entrypoint và thêm các hook vào một bản sao AST của `b042_run_one_sample`. Đây là tích hợp vào loop thật, không phải một harness minh họa khác. Notebook B042 không bị sửa.

## Modules

| File | Trách nhiệm |
| --- | --- |
| `common.py` | SHA-256, canonical JSON, nested paths, schema subset, typed comparisons |
| `builder.py` | Integrity, catalog audit, toàn bộ observations, legacy provenance, discovery |
| `validation.py` | Endpoint/path/type checks, semantic tiers, membership review, SCC cycles |
| `compiler.py` | Approval overlay, runtime indexes, checksums, catalog/scope validation |
| `runtime.py` | EvidenceLedger, GoalContext, bounded backward resolver, controller |
| `b043.py` | Checked B042 hooks, input sanitizer, graph-aware goal planning |
| `evaluation.py`, `kaggle.py` | Post-execution metrics, paired launcher, checkpoints |

## Graph schema

`full_dependency_graph_v3.json` có `schema_version=3`, version string, source digests, tool schemas, `nodes`, `relations` và `dependencies`. Structural relations và dependency candidates là hai tập riêng. Không coi 392 legacy structural edges là 392 prerequisites.

Node types: TOOL, ARGUMENT, OUTPUT_FIELD, RESOURCE, ENTITY_TYPE, PRECONDITION. Argument `type` là node type; `value_type`/`schema.type` giữ kiểu dữ liệu. Output fields lưu kiểu đã quan sát; catalog hiện có returns dạng text, không có formal output schema đầy đủ.

Relations: PRODUCES, CONSUMES, REFERENCES_RESOURCE, REQUIRES_DATA_FROM, CONDITIONAL_DEPENDENCY, ALTERNATIVE_INPUT, INVALIDATES_EVIDENCE. Quan hệ data có đường dẫn source/target thực. Candidate chưa có source path vẫn được giữ trong `dependencies`, không tạo đường data giả.

Mỗi dependency lưu `edge_id`, producer/consumer, field paths, dependency type, structured `required_when`, alternative branches, subject type/binding, selection strategy, evidence sources với SHA và original rows, evidence tier, validation status/reasons, operator decision, runtime flag, risk, cost, argument binding và notes. Legacy curation record được giữ nguyên riêng.

`RESOURCE_STATE_PRECONDITION` biểu diễn membership như `appliances[].cycles[]`, `members[]`, `channels[]`. Không copy `state.cycle` hoặc người đang được giao việc để suy ý định mới. Những predicates này đang NEEDS_REVIEW; generic runtime abstain với collection/row construction. Nested `items[].item_id` giữ parent/row binding requirement, không tự broadcast một ID vào mọi dòng.

## Discovery và validation

Catalog/Spec là contract. Description nêu producer là evidence mạnh; x-ref + resource token chỉ tạo candidate. Typed exact-flow trong aligned train/history turns chỉ là bằng chứng luồng giá trị. Order-only relationships luôn chờ review. Không suy mandatory dependency từ tần suất.

VERIFIED đòi producer READ tồn tại, consumer/target tồn tại, source path thực sự được quan sát, type tương thích và explicit contract. Đây vẫn chưa phải chứng minh hoàn chỉnh semantic identity của từng request tương lai. NEEDS_REVIEW, INSUFFICIENT_EVIDENCE và REJECTED được giữ trong graph/review queue. Self edges không bootstrap được và bị reject. SCC cycles được báo; runtime có cycle/depth guards độc lập.

Write epoch là safety policy bảo thủ: sau WRITE thành công và không phải `done=false`, tất cả READ evidence trước đó stale. Không tuyên bố mọi WRITE thực sự sửa mọi resource. Invalidation relation được gắn `RUNTIME_SAFETY_POLICY` để phân biệt với contract app.

## Runtime algorithm

1. Xác định goal duy nhất hoặc goal được binding rõ; điều kiện phải true.
2. Kiểm schema, required slots, optional slots được goal yêu cầu và x-one-of branches.
3. Proposed values có scoped user provenance có thể đáp ứng slot. Identifier có subject selector khác không được bỏ qua identity check chỉ vì cùng xuất hiện trong request.
4. Tra `argument_dependencies[consumer][slot]`, không quét toàn graph.
5. Tìm evidence đúng goal, write epoch, producer arguments và exact subject selectors. So NFC/casefold, giữ dấu và role words; không fuzzy/cắt honorific để chọn người.
6. Một record khớp tất cả selectors mới được dùng. Trùng record/entity, conflicting values hoặc routes không phân biệt được thì abstain.
7. Nếu thiếu evidence, dựng producer arguments từ binding đã được compiler/operator review và user spans. Producer còn thiếu dữ liệu thì truy ngược tiếp.
8. Trả NEED_READ; controller chỉ đề xuất READ. Harness thực thi qua validators/guards và `ToolEnv.validate()`, cập nhật hai ledgers rồi giải lại target.
9. Policy vẫn đề xuất mọi WRITE. Graph không đánh dấu goal complete và không tự xác nhận an toàn/completion.

Statuses: SATISFIED, NEED_READ, NEED_MODEL_DECISION, AMBIGUOUS, UNRESOLVED, BLOCKED_UNSAFE, NOT_APPLICABLE. Defaults: depth 5, graph reads 4, max read cost NORMAL. Failed/empty/reserved reads không được lặp cùng goal/tool/args/epoch. Một WRITE cho phép READ mới nhưng không reset tổng budget.

## Subject binding và giới hạn

GoalContext có goal ID, subjects theo `tool:slot`, bindings theo tool/path, optional intent, branch choice, condition và request span. Các literal có start/end trong current request; multi-goal assistance bắt buộc có clause span riêng. Evidence của goal khác không được tự dùng. Nhiều goals cùng tool có thể được phân biệt bằng explicit argument bindings; nếu không phân biệt được, Policy tiếp tục.

Span và exact matching là rào chắn provenance, không phải chứng minh hiểu tiếng Việt hoàn hảo. Qwen vẫn có thể xác định sai goal/clause. Existing guards tiếp tục chạy; production correctness cần đánh giá độc lập.

Compiled graph có consumer/producer/argument indexes, alternative branches, preconditions, validated paths, subject constraints và risk. Runtime bundle không chứa train sample IDs, gold calls/results hoặc values. Một harness kiểm bundle một lần và dùng private compiled copy cho các requests; state/evidence tách theo request. B043 module globals dùng tuần tự, không hỗ trợ chạy concurrent requests trên cùng harness instance.

Chưa có kết quả accuracy/latency GPU. Graph có thể giảm model calls hoặc repair loops, nhưng cũng có thể tăng reads/prompt size/overhead. Phải đo tổng latency và correctness trên cùng điều kiện.
