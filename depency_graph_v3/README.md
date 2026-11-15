# LifeMate Layer 2 — Dependency Graph v3 / B043

B043 Final: 190 candidates được xử lý, 80 VERIFIED và 59 safe-READ dependencies đã compile trong `artifacts_final/`. CPU suite: 131 PASS, 0 FAIL, 1 offline-rebuild SKIP. Xem [báo cáo cuối](B043_FINAL_REPORT.md) và [hướng dẫn Kaggle](B043_KAGGLE_FINAL_GUIDE.md). Graph chỉ tự READ; WRITE vẫn do Policy và guards B042.

Workspace độc lập. Source mới nằm trong `lifemate_graph/`; B042, graph gốc và dữ liệu BTC được giữ nguyên byte.

```powershell
python scripts/run_cpu_checks.py
python -m lifemate_graph.cli demo
```

CPU package dùng Python 3.10+ và standard library, không cần GPU hoặc ToolEnv. Không cần cài package để chạy từ workspace.

- [Notebook B043](LifeMate_B043_Full_Graph_Resolver_Kaggle.ipynb): package và artifacts được nhúng sẵn, có CPU self-tests, demo, launcher Qwen/ToolEnv, checkpoint và output ZIP.
- [Kiến trúc](GRAPH_V3_ARCHITECTURE.md), [build/audit](GRAPH_V3_BUILD_REPORT.md), [tích hợp](GRAPH_V3_RUNTIME_INTEGRATION.md), [kiểm thử](GRAPH_V3_TEST_REPORT.md), [Kaggle](GRAPH_V3_KAGGLE_GUIDE.md).
- [Graph manifest](artifacts/graph_manifest.json), [review queue](artifacts/graph_review_queue_v3.csv), [CPU results](reports/cpu_test_results.json), [closed-loop demo](reports/closed_loop_demo.json).

Audit đủ 147/147 tools. Graph hoàn thiện và unified overlay nằm trong `artifacts_completed/`; graph gốc và pilot 19 cạnh trong `artifacts_reviewed/` giữ nguyên để đối chiếu. Notebook mặc định nạp final graph, so sánh GRAPH_DISABLED với GRAPH_ASSISTED_SAFE_READ. Các báo cáo GRAPH_V3_* và activation 19 cạnh phía trên là lịch sử triển khai.

Chưa chạy Qwen/GPU/ToolEnv thật, chưa đo cải thiện accuracy hoặc latency. Không có benchmark parity 92 trajectories được khởi chạy.
