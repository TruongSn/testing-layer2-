# B043 Active Graph — Bàn giao pilot

**Đã cấp quyền và compile đúng 19/19 dependencies được người dùng duyệt.** Compile-only, không chạy discovery. Candidate graph, B042 gốc và dữ liệu BTC giữ nguyên byte. Không bật thêm cạnh.

| Hạng mục | Kết quả |
| --- | --- |
| Approval | 19 APPROVED; attribution: người dùng duyệt qua prompt FINAL ACTIVATION |
| Runtime enabled | 19 dependencies, 17 consumer tools |
| Production closed loops | 19/19 PASS bằng chính compiled PRODUCTION graph |
| Production safety gates | 16/16 tests PASS; không dùng synthetic grants thay thế |
| CPU suite local | 109 tests: 108 PASS, 0 FAIL, 0 ERROR, 1 SKIP |
| Test được skip | Offline discovery rebuild, đúng phạm vi nhiệm vụ |
| Automatic WRITE | Không; chỉ Policy CALL qua guards và ToolEnv.validate |

Gates đã kiểm: đúng goal/entity/provenance; duplicate/ambiguous subject abstain, production kể cả dòng lặp giống hệt; không đoán query/ID/amount; nguồn của minutes/content/label; music.play alternative branch; không lặp READ failed/empty; valid ID/evidence không đọc thừa; availability và ToolEnv.validate; grounding/additional/progress guards; GRAPH_DISABLED khớp B042 gốc trong closed loop.

CPU dùng FakeToolEnv và scripted Policy; **authorization là production approval thật**. Traces ở `artifacts_reviewed/closed_loop_verification.json`, gates ở `runtime_gate_results.json`, full log/report ở `artifacts_reviewed/cpu/`. Các tests synthetic cũ chạy riêng trong regression suite, không thay thế production gates.

## SHA-256

| Đối tượng | Hash |
| --- | --- |
| Original full graph file | `37514613a9dfc9d74c5d7aa17a48ae982514a29208c96957cd16c4201bd826bd` |
| Canonical graph content | `449fa731f239a4284e5e157a428d37d3872fbed776ca1aa99f28ef7aaab75ce8` |
| Catalog | `a994984bb580d7e0d60166641858901d428549682ba3614dbf9269037a33cce0` |
| Compiled graph file | `728c99798c2600a88fad6e8fa4afb1141b47e206248307f2abb3106a4ad0d0a7` |
| Compiled payload | `ad7a10901094748b561dc07b9822d0db67743c0edae2648f54606eee754d7712` |
| Operator approval | `d52aa19f511d499b7547772346dcdf15978865f944a2dee8b745df17ef4dd861` |
| Runtime source bundle | `67a09dfda5a501d90e6cae1014c9dfb1914fe46ac691c73557658f3689ed7125` |

Per-file source hashes nằm trong `artifacts_reviewed/graph_manifest.json`; notebook/ZIP checksums nằm trong `deliverables/B043_delivery_checksums.json`.

Source `artifacts/` vẫn là snapshot cũ có 0 grants. Runtime dùng `artifacts_reviewed/` có 19 grants. Draft/CSV giữ nguyên để đối chiếu; `operator_approvals.json` là approval chính thức.

## Giới hạn còn lại

Active GPU/Qwen và ToolEnv thật chưa chạy local. Prior GRAPH_DISABLED/GRAPH_SHADOW GPU smoke PASS là thông tin người dùng cung cấp. Chưa kết luận cải thiện accuracy hoặc latency. Pilot chỉ có 19 edges; alias, relative reference, nested collection hoặc goal context không chắc chắn sẽ abstain/fallback. Không chạy benchmark lớn hoặc parity-92.
