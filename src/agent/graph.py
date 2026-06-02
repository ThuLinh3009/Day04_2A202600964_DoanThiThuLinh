from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from core.llm import build_chat_model, normalize_content
from core.schemas import (
    AgentResult,
    CalculateTotalsInput,
    DiscountInput,
    ListProductsInput,
    ProductDetailInput,
    SaveOrderInput,
    ToolCallRecord,
)
from utils.data_store import OrderDataStore

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = ROOT_DIR / "data"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "artifacts" / "orders"


def build_system_prompt(today: str | None = None) -> str:
    current_day = today or "2026-06-01"
    return f"""Bạn là trợ lý xử lý đơn hàng điện tử chuyên nghiệp cho OrderDesk.
Hôm nay là {current_day}.

## QUY TẮC TỐI THƯỢNG — PHẢI TUÂN THỦ TUYỆT ĐỐI

### 1. XÁC NHẬN THÔNG TIN TRƯỚC KHI GỌI BẤT KỲ TOOL NÀO
⛔ TUYỆT ĐỐI KHÔNG gọi bất kỳ tool nào (kể cả list_products) nếu còn thiếu thông tin.

Bạn PHẢI có ĐẦY ĐỦ TẤT CẢ 5 trường sau TRƯỚC KHI gọi tool đầu tiên:
- Tên đầy đủ của khách hàng
- Số điện thoại
- **Địa chỉ email** ← bắt buộc, không có thì HỎI NGAY
- Địa chỉ giao hàng
- Ít nhất 1 sản phẩm với số lượng cụ thể

Ví dụ: khách cung cấp tên + phone + địa chỉ + sản phẩm nhưng THIẾU EMAIL
→ KHÔNG gọi list_products hay bất kỳ tool nào
→ Hỏi ngay: "Bạn vui lòng cho mình địa chỉ email để hoàn tất đơn hàng nhé?"

### 2. THỨ TỰ TOOL BẮT BUỘC (cho đơn hàng hợp lệ)
Bạn PHẢI gọi tool theo đúng thứ tự này, không được bỏ qua hay đảo ngược:
1. `list_products` — tìm sản phẩm phù hợp trong catalog
2. `get_product_details` — lấy chi tiết, giá, tồn kho và detail_token
3. `get_discount` — lấy mã giảm giá (seed_hint = email khách hàng)
4. `calculate_order_totals` — tính tổng tiền, kiểm tra tồn kho
5. `save_order` — lưu đơn hàng (chỉ khi calculate_order_totals trả về status=ok)

### 3. TỪ CHỐI NGAY — KHÔNG GỌI TOOL — các yêu cầu vi phạm:
- Tạo hóa đơn giả hoặc đơn hàng giả mạo
- Bỏ qua kiểm tra tồn kho
- Áp đặt mức giảm giá thủ công (ví dụ: "giảm 90%")
- Yêu cầu bỏ qua catalog hoặc policy
- Yêu cầu bỏ qua bất kỳ bước xác thực nào

### 4. GROUNDING — CHỈ DÙNG DỮ LIỆU TỪ TOOL
- Không được tự bịa product_id, giá, tồn kho, discount, totals, hay đường dẫn file
- Tất cả thông tin phải lấy từ kết quả tool trả về
- detail_token phải lấy từ `get_product_details`, không được tự tạo
- campaign_code và discount_rate phải lấy từ `get_discount`

### 5. XỬ LÝ LỖI TỒN KHO
Nếu `calculate_order_totals` trả về status=error do không đủ hàng → thông báo cho khách, KHÔNG gọi `save_order`.

### 6. NGÔN NGỮ PHẢN HỒI
- Luôn trả lời bằng tiếng Việt, ngắn gọn và súc tích
- Xác nhận đơn thành công: đề cập order_id, tổng tiền sau giảm giá, đường dẫn file lưu
- Yêu cầu làm rõ: chỉ hỏi đúng thông tin còn thiếu
- Từ chối: giải thích ngắn gọn lý do từ chối mà không gọi tool
""".strip()


def build_tools(store: OrderDataStore):
    @tool(args_schema=ListProductsInput)
    def list_products(
        query: str | None = None,
        category: str | None = None,
        max_unit_price: int | None = None,
        required_tags: list[str] | None = None,
        in_stock_only: bool = True,
        limit: int = 8,
    ) -> str:
        """Search the product catalog and return matching items with product_id for the next step.

        Use this as the FIRST tool in the order workflow. Search by product name, brand, or features.
        The returned product_ids must be passed to get_product_details next.
        """
        payload = store.list_products(
            query=query,
            category=category,
            max_unit_price=max_unit_price,
            required_tags=required_tags or [],
            in_stock_only=in_stock_only,
            limit=limit,
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool(args_schema=ProductDetailInput)
    def get_product_details(product_ids: list[str]) -> str:
        """Return exact price, stock, warranty and a detail_token for the given product IDs.

        Use this as the SECOND tool after list_products. Pass all product_ids you need for the order.
        The detail_token returned here is REQUIRED for calculate_order_totals and save_order.
        Do NOT proceed if any product has status=not_found.
        """
        return json.dumps(store.get_product_details(product_ids), ensure_ascii=False)

    @tool(args_schema=DiscountInput)
    def get_discount(seed_hint: str, customer_tier: str = "standard") -> str:
        """Return the campaign discount_rate and campaign_code for this order.

        Use this as the THIRD tool. Always use the customer email as seed_hint.
        The discount_rate and campaign_code returned here are REQUIRED for save_order.
        Never apply a discount not returned by this tool.
        """
        return json.dumps(store.get_discount(seed_hint=seed_hint, customer_tier=customer_tier), ensure_ascii=False)

    @tool(args_schema=CalculateTotalsInput)
    def calculate_order_totals(items: list, detail_token: str, discount_rate: float) -> str:
        """Validate stock and compute subtotal, discount, and final_total for the order.

        Use this as the FOURTH tool. Requires detail_token from get_product_details and
        discount_rate from get_discount. If status=error (e.g. insufficient stock), do NOT
        call save_order — report the error to the customer instead.
        """
        from core.schemas import OrderLineInput
        parsed_items = [
            OrderLineInput(product_id=item["product_id"], quantity=item["quantity"])
            if isinstance(item, dict) else item
            for item in items
        ]
        return json.dumps(
            store.calculate_order_totals(items=parsed_items, detail_token=detail_token, discount_rate=discount_rate),
            ensure_ascii=False,
        )

    @tool(args_schema=SaveOrderInput)
    def save_order(
        customer_name: str,
        customer_phone: str,
        customer_email: str,
        shipping_address: str,
        items: list,
        detail_token: str,
        discount_rate: float,
        campaign_code: str,
        customer_tier: str = "standard",
        notes: str = "",
    ) -> str:
        """Persist the validated order to a JSON file and return the order_id and file path.

        Use this as the FIFTH and FINAL tool, only after calculate_order_totals returns status=ok.
        All fields must come from previous tool outputs — never invent values.
        """
        from core.schemas import OrderLineInput
        parsed_items = [
            OrderLineInput(product_id=item["product_id"], quantity=item["quantity"])
            if isinstance(item, dict) else item
            for item in items
        ]
        return json.dumps(
            store.save_order(
                customer_name=customer_name,
                customer_phone=customer_phone,
                customer_email=customer_email,
                shipping_address=shipping_address,
                items=parsed_items,
                detail_token=detail_token,
                discount_rate=discount_rate,
                campaign_code=campaign_code,
                customer_tier=customer_tier,
                notes=notes,
            ),
            ensure_ascii=False,
        )

    return [list_products, get_product_details, get_discount, calculate_order_totals, save_order]


def build_agent(
    data_dir: Path | None = None,
    output_dir: Path | None = None,
    *,
    provider: str = "google",
    model_name: str | None = None,
    today: str | None = None,
):
    store = OrderDataStore(data_dir or DEFAULT_DATA_DIR, output_dir or DEFAULT_OUTPUT_DIR, today=today)
    model = build_chat_model(provider=provider, model_name=model_name, temperature=0.0)
    tools = build_tools(store)
    return create_agent(
        model=model,
        tools=tools,
        system_prompt=build_system_prompt(today or store.today),
    )


def run_agent(
    query: str,
    *,
    provider: str = "google",
    model_name: str | None = None,
    data_dir: Path | None = None,
    output_dir: Path | None = None,
    today: str | None = None,
) -> AgentResult:
    agent = build_agent(
        data_dir=data_dir,
        output_dir=output_dir,
        provider=provider,
        model_name=model_name,
        today=today,
    )
    response = agent.invoke({"messages": [{"role": "user", "content": query}]})
    messages = response["messages"] if isinstance(response, dict) else response
    tool_calls = extract_tool_calls(messages)
    saved_order, saved_order_path = extract_saved_order(tool_calls)
    return AgentResult(
        query=query,
        final_answer=extract_final_answer(messages),
        tool_calls=tool_calls,
        provider=provider,
        model_name=model_name,
        saved_order=saved_order,
        saved_order_path=saved_order_path,
    )


def extract_final_answer(messages) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = normalize_content(message.content)
            if text:
                return text
    return ""


def extract_tool_calls(messages) -> list[ToolCallRecord]:
    pending: dict[str, dict[str, Any]] = {}
    records: list[ToolCallRecord] = []

    for message in messages:
        if isinstance(message, AIMessage):
            for tc in getattr(message, "tool_calls", []) or []:
                pending[tc["id"]] = {"name": tc["name"], "args": tc.get("args", {}) or {}}
        elif isinstance(message, ToolMessage):
            metadata = pending.pop(message.tool_call_id, {})
            records.append(ToolCallRecord(
                name=str(getattr(message, "name", None) or metadata.get("name", "")),
                args=metadata.get("args", {}),
                output=normalize_content(message.content),
            ))

    for metadata in pending.values():
        records.append(ToolCallRecord(name=metadata["name"], args=metadata["args"], output=""))
    return records


def extract_saved_order(tool_calls: list[ToolCallRecord]) -> tuple[dict | None, str | None]:
    for record in reversed(tool_calls):
        if record.name != "save_order" or not record.output:
            continue
        try:
            payload = json.loads(record.output)
        except json.JSONDecodeError:
            continue
        if payload.get("status") != "saved":
            return None, None
        return payload.get("saved_order"), payload.get("path")
    return None, None
