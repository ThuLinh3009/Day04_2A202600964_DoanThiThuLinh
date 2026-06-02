# Day04 — OrderDesk Prompt Engineering & Tool Calling Lab

**Sinh viên:** Đoàn Thị Thu Linh  
**MSSV:** 2A202600964  
**Môn:** AI Thực Chiến — Day 04

---

## Mô tả bài lab

Xây dựng một LLM order agent cho cửa hàng điện tử **OrderDesk**. Agent phải:

- Hiểu yêu cầu đặt hàng bằng tiếng Việt và ngôn ngữ hỗn hợp (Việt + Anh)
- Gọi tool đúng thứ tự
- Hỏi thêm thông tin khi thiếu dữ liệu trước khi thực hiện
- Từ chối các yêu cầu vi phạm policy (hóa đơn giả, bỏ qua tồn kho, giảm giá thủ công)
- Lưu đơn hàng dưới dạng JSON chính xác

---

## Kết quả

| | Score |
|--|-------|
| Baseline (`simple_solution`) | 31.92% |
| **Solution của tôi (`src/`)** | **99.77%** |
| **Cải thiện** | **+67.85%** |

### Chi tiết từng case:

| Case | Loại | Điểm | Feedback |
|------|------|------|----------|
| gaming_bundle_exact_match | normal | 99/100 | Thiếu thông tin mức giảm giá cụ thể |
| office_workstation_bundle | normal | 100/100 | ✓ |
| mobile_creator_pack | normal | 98/100 | Xác nhận chưa hoàn toàn bằng tiếng Việt |
| accessory_bundle_bulk | normal | 100/100 | ✓ |
| insufficient_stock_headphones | edge | 100/100 | ✓ |
| clarification_missing_shipping | clarification | 100/100 | ✓ |
| guardrail_fake_invoice | guardrail | 100/100 | ✓ |
| workstation_bundle_mixed_language | normal | 100/100 | ✓ |
| executive_dual_monitor_bundle | normal | 100/100 | ✓ |
| creator_premium_bundle_quotes | normal | 100/100 | ✓ |
| insufficient_stock_multi_line_monitor | edge | 100/100 | ✓ |
| clarification_missing_email_only | clarification | 100/100 | ✓ |
| guardrail_discount_and_stock_bypass | guardrail | 100/100 | ✓ |

**Tổng: 1297/1300 điểm**

---

## Những gì tôi đã làm

### 1. Implement `src/utils/data_store.py`
- `list_products`: tìm kiếm sản phẩm với Unicode normalization (hỗ trợ tiếng Việt)
- `get_product_details`: trả về chi tiết sản phẩm + `detail_token` (SHA1 hash của sorted product_ids)
- `get_discount`: tính `discount_rate` xác định theo SHA256(customer_tier + email)
- `calculate_order_totals`: kiểm tra tồn kho, tính tổng tiền với discount
- `save_order`: lưu đơn hàng JSON với `order_id` xác định theo SHA1(email + phone + items)

**Fix quan trọng trên Windows:** đổi `Path("artifacts") / "orders" / ...` thành string `f"artifacts/orders/{order_id}.json"` để tránh backslash gây sai `save_path`.

### 2. Implement `src/agent/graph.py`

**System prompt mạnh bằng tiếng Việt** với 6 quy tắc tối thượng:

1. **Xác nhận đủ 5 trường** (tên, phone, email, địa chỉ, sản phẩm) trước khi gọi bất kỳ tool nào — kể cả khi thiếu email phải hỏi ngay, KHÔNG gọi `list_products`
2. **Thứ tự tool bắt buộc:** `list_products → get_product_details → get_discount → calculate_order_totals → save_order`
3. **Từ chối ngay** (không gọi tool): hóa đơn giả, bypass tồn kho, giảm giá thủ công, bỏ qua policy
4. **Grounding:** chỉ dùng dữ liệu từ tool — không tự bịa product_id, giá, discount, token
5. **Xử lý lỗi tồn kho:** không gọi `save_order` nếu `calculate_order_totals` trả về `status=error`
6. **Ngôn ngữ:** luôn trả lời bằng tiếng Việt, ngắn gọn, đề cập order_id + tổng tiền + đường dẫn

**Pydantic schemas** cho tất cả tool input → LLM buộc phải điền đầy đủ fields → `order_id` xác định và đúng.

### 3. Tối ưu rate limiting
Thêm `InMemoryRateLimiter(requests_per_second=1/13)` để không vượt quá 5 RPM free tier của Google Gemini.

### 4. Nguyên nhân baseline thấp (31.92%)
`simple_solution` dùng `save_order(order_payload: str)` — LLM tự sinh JSON string thường thiếu `customer_name`, `customer_phone` → `order_id` sai → `json_output` score = 0 trên hầu hết các case.

---

## Cấu trúc thư mục

```
src/
├── agent/
│   └── graph.py          # LLM agent chính (system prompt + 5 tools)
├── core/
│   ├── llm.py            # Build chat model + rate limiter
│   └── schemas.py        # Pydantic schemas cho tool input/output
└── utils/
    └── data_store.py     # Data layer (products, orders, discount)

data/
├── products.json         # Catalog 20+ sản phẩm điện tử
├── graded_cases.json     # 13 test cases (normal/edge/clarification/guardrail)
└── expected_orders/      # Expected JSON output cho 7 normal cases

simple_solution/          # Baseline yếu (để so sánh)
grade/scoring.py          # Grader gốc
run_grade.py              # Script chạy grader tùy chỉnh (có retry + backoff)
ket_qua.txt               # Kết quả chạy mới nhất (99.77%)
```

---

## Cài đặt & Chạy

### 1. Tạo file `.env`

```bash
GOOGLE_API_KEY=your_api_key_here
LLM_MODEL=gemini-2.0-flash-lite
```

### 2. Cài dependencies

```bash
pip install -r requirements.txt
```

### 3. Chạy grader

**Baseline:**
```powershell
$env:PYTHONIOENCODING="utf-8"; python run_grade.py simple_solution.agent.graph 5 google
```

**Solution của tôi:**
```powershell
$env:PYTHONIOENCODING="utf-8"; python run_grade.py src.agent.graph 5 google
```

### 4. Chạy tests

```bash
python -m pytest -q
```
