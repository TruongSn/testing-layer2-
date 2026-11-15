# B043 Final — kết quả triển khai

Đã hoàn tất phần triển khai local và chuẩn bị paired GPU evaluation. **GPU final chưa chạy**: máy local thiếu PyTorch, Transformers, zaic_tools và quyền thực thi Kaggle. Chưa có số accuracy/latency của Qwen cho bản này.

| Phạm vi | Kết quả thực tế |
|---|---:|
| Tool audit | 147/147 tools, 46 apps |
| Candidates được xử lý | 190/190 |
| VERIFIED / NEEDS_REVIEW / INSUFFICIENT / REJECTED | 80 / 70 / 34 / 6 |
| Runtime-enabled prerequisite READ dependencies | 59, phục vụ 54 consumer tools |
| VERIFIED nhưng chưa bật | 21 |
| CPU suite local | 132 tests: **131 PASS, 0 FAIL, 1 SKIP** |
| Production-graph closed loops | 57 data-binding contracts + 2 context contracts |
| Năm development scenarios, Planner không có graph_context/goals | 5/5 có graph READ; tổng **9 READ** |
| Graph-generated WRITE trong các trace CPU | **0** |
| Qwen accuracy B042/B043; latency GPU B042/B043 | **Chưa đo** |

Skip duy nhất ở local là test rebuild discovery có chủ đích. Original inputs, B042, graph gốc và approval pilot 19 cạnh được giữ nguyên. Graph hoàn thiện nằm trong `artifacts_completed/`; CLI compile-only tạo `artifacts_final/` từ graph đó và approval overlay thống nhất. Không có cạnh REJECTED được bật. JSON Schema, 190 technical decisions, source/catalog digests, compiled indexes và checksum đã được kiểm tra.

Thay đổi chính: context deterministic có source spans; phục hồi intent khi Planner bỏ sót; evidence theo goal/epoch; context READ cho bài hát tham chiếu từ chat và số phần dựa trên nhóm; bind nested rows có parent proof; record-type predicates; READ do Policy và Graph dùng chung evidence; STOP feedback có giới hạn. Planner dùng prompt gọn B042, không còn yêu cầu sinh offsets bằng prompt chứa toàn registry. WRITE vẫn do Policy đề xuất và qua guards, progress controller và ToolEnv.validate. Số điện thoại quan sát được phải thuộc đúng contact; direct phone từ user vẫn là alternative hợp lệ.

Trace CPU cho các lỗi đã báo:

| Scenario | Graph READs | Chuỗi thực thi |
|---|---:|---|
| train_0136 | 3 | list_chats → list_restaurants → get_menu → Policy place_order |
| train_0463 | 1 | list_chats → Policy read_chat |
| train_0488 | 1 | library.search → Policy reserve |
| train_0557 | 1 | list_tracks → Policy play |
| train_0951 | 3 | list_chats → read_chat → list_tracks → Policy add_to_playlist |

Đây là **scripted Policy/FakeToolEnv với recorded observations**, không phải kết quả inference Qwen hoặc official ToolEnv. Sai track, sai recipient/quantity, duplicate/ambiguous records, cross-goal/stale evidence, điều kiện chưa được chứng minh và WRITE replay đều có regression gates. GRAPH_DISABLED tiếp tục dùng B042 nguyên trạng; cùng budget inference được áp cho hai nhánh benchmark.

Năm failure modes đã tái hiện hoặc xác định từ source: thiếu context làm graph không chạy; STOP sớm; wrong-subject/occurrence; phối hợp Policy READ sai goal gây đọc lại; arguments/alternative branches thiếu provenance. Các đường này đã có CPU regression. **Chưa có dữ liệu để xếp hạng tần suất lỗi hoặc bottleneck latency trên GPU.**

Coverage còn thiếu: temporal derivation phức tạp; appliance-cycle/assignee membership; order/restaurant và reward/program joins; tham chiếu hội thoại không có literal identity rõ; các đường tài chính, khóa cửa, y tế/an ninh chưa có phạm vi tự đọc đủ chắc. Chúng abstain/fallback; không gán VERIFIED chỉ bằng co-occurrence. Chi tiết từng cạnh: `artifacts_completed/candidate_completion.csv`.

Notebook mặc định chạy hai nhánh trên 40 samples có phân tầng, bắt đầu bằng smoke 5 cases. Nếu smoke không có graph READ, nó lưu traces và dừng trước tập lớn. Thu thập call F1 proxy, exact/name F1, model/token/tool/graph timing, retries, overruns và checkpoints. State Match cần authoritative reference hashes độc lập; không hash partial label.final_state, đọc hidden state hoặc chạy gold trong env thứ hai. Official score, unnecessary READ và semantic wrong-WRITE rates giữ null khi thiếu evaluator/adjudication.

Budget 30s chặn generation/tool call mới khi hết thời gian; không thể cưỡng bức ngắt CUDA pass hoặc tool call đang chạy. Notebook đo và báo các ca vượt 30s. Chưa tuyên bố tuân thủ hard deadline trên GPU.

Việc tiếp theo là chạy notebook, xem paired deltas và top failures thực tế. Chỉ tối ưu prompt/model calls/inference sau đo; nếu model vẫn chọn sai tool/args khi đủ evidence thì mới cân nhắc SFT/LoRA.

Hash đầy đủ: `reports/final/source_graph_hashes.json`. Compiled payload SHA-256: `c903f0166b60656780a1111aa4db572692e64385c6b37e4da977af83ebdc7622`. Source aggregate: `a61114046b198324bf1310e5bf0cf57f0f089cb6161b2ecd88ccdb5c62833784`.
