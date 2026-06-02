from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db import get_session
from app.db.models import MockOrder, utc_now

router = APIRouter(prefix="/api/mock", tags=["mock"])

PRODUCT_CATALOG = {
    "SKU-001": {
        "product_id": "SKU-001",
        "display_name": "SKU-001",
        "brand": "Mock",
        "price": Decimal("99.00"),
        "currency": "CNY",
        "spec": "standard",
    },
    "SKU-002": {
        "product_id": "SKU-002",
        "display_name": "SKU-002",
        "brand": "Mock",
        "price": Decimal("199.00"),
        "currency": "CNY",
        "spec": "standard",
    },
    "SKU-003": {
        "product_id": "SKU-003",
        "display_name": "SKU-003",
        "brand": "Mock",
        "price": Decimal("299.00"),
        "currency": "CNY",
        "spec": "standard",
    },
}

PRODUCT_NAME_CATALOG = {
    "iphone 15": {
        "product_id": "PHONE-IP15",
        "display_name": "iPhone 15",
        "brand": "Apple",
        "price": Decimal("4599.00"),
        "currency": "CNY",
        "spec": "128GB",
    },
    "三星s24": {
        "product_id": "PHONE-S24",
        "display_name": "三星 Galaxy S24",
        "brand": "Samsung",
        "price": Decimal("3999.00"),
        "currency": "CNY",
        "spec": "256GB",
    },
    "小米14": {
        "product_id": "PHONE-MI14",
        "display_name": "小米 14",
        "brand": "Xiaomi",
        "price": Decimal("3299.00"),
        "currency": "CNY",
        "spec": "256GB",
    },
    "a1": {
        "product_id": "A1",
        "display_name": "A1 标准商品",
        "brand": "Mock",
        "price": Decimal("129.00"),
        "currency": "CNY",
        "spec": "standard",
    },
    "a3": {
        "product_id": "A3",
        "display_name": "A3 高阶商品",
        "brand": "Mock",
        "price": Decimal("239.00"),
        "currency": "CNY",
        "spec": "pro",
    },
}

PRIMARY_ORDER_CENTER = {
    "ORDER-1001": {"status": "signed", "signed_days": 3, "refundable": True},
    "ORDER-1002": {"status": "signed", "signed_days": 16, "refundable": False},
}

ARCHIVE_ORDER_CENTER = {
    "ARCHIVE-1001": {
        "status": "signed",
        "signed_days": 4,
        "refundable": True,
        "archive_reason": "订单已归档到历史订单中心",
        "recommendation": "该历史订单签收 4 天，当前可继续发起售后退款审核。",
    }
}


class MockOrderQueryRequest(BaseModel):
    order_id: str


class MockProductPurchaseRequest(BaseModel):
    user_id: str = "user_demo"
    product_id: str
    sku_id: str | None = None
    quantity: int = Field(default=1, ge=1, le=99)
    payment_method: str = "mock_balance"


class MockProductPriceQueryRequest(BaseModel):
    product_name: str


class MockOrderAddRequest(BaseModel):
    user_id: str = "user_demo"
    order_id: str | None = None
    product_id: str
    sku_id: str | None = None
    quantity: int = Field(default=1, ge=1, le=99)
    status: str = "created"


@router.post("/order/query")
def mock_order_query(
    request: MockOrderQueryRequest, db: Session = Depends(get_session)
) -> dict[str, Any]:
    order_id = _normalize_id(request.order_id)
    dynamic_record = _find_dynamic_order(db, order_id)
    if dynamic_record:
        return _order_hit(order_id, "primary_order_center", dynamic_record)
    record = PRIMARY_ORDER_CENTER.get(order_id)
    if not record:
        return _order_miss(order_id, "primary_order_center")
    return _order_hit(order_id, "primary_order_center", record)


@router.post("/order/archive-query")
def mock_order_archive_query(request: MockOrderQueryRequest) -> dict[str, Any]:
    order_id = _normalize_id(request.order_id)
    record = ARCHIVE_ORDER_CENTER.get(order_id)
    if not record:
        return _order_miss(order_id, "archive_order_center")
    return _order_hit(order_id, "archive_order_center", record)


@router.post("/product/purchase")
def mock_product_purchase(
    request: MockProductPurchaseRequest, db: Session = Depends(get_session)
) -> dict[str, Any]:
    record = _find_product_record(request.product_id)
    if not record:
        return _product_miss(request.product_id)
    unit_price = record["price"]
    total_amount = unit_price * Decimal(request.quantity)
    order_id = f"MOCK{uuid4().hex[:10].upper()}"
    result = {
        "found": True,
        "order_id": order_id,
        "purchase_id": f"PUR{uuid4().hex[:10].upper()}",
        "user_id": request.user_id,
        "product_id": record["product_id"],
        "display_name": record["display_name"],
        "sku_id": request.sku_id,
        "quantity": request.quantity,
        "unit_price": float(unit_price),
        "total_amount": float(total_amount),
        "currency": "CNY",
        "payment_method": request.payment_method,
        "payment_status": "paid",
        "order_status": "paid",
        "created_at": _now_iso(),
    }
    _upsert_dynamic_order(
        db,
        order_id=order_id,
        user_id=request.user_id,
        product_id=record["product_id"],
        sku_id=request.sku_id,
        quantity=request.quantity,
        status="paid",
        payment_status="paid",
        order_status="paid",
        total_amount=float(total_amount),
        currency="CNY",
        metadata={"purchase_id": result["purchase_id"], "payment_method": request.payment_method},
    )
    return result


@router.post("/product/price-query")
@router.post("/product/price_query")
def mock_product_price_query(request: MockProductPriceQueryRequest) -> dict[str, Any]:
    product_name = request.product_name.strip()
    record = _find_product_record(product_name)
    if not record:
        return _product_miss(product_name)
    return {
        "product_name": product_name,
        "found": True,
        "source": "mock_product_price_catalog",
        "product_id": record["product_id"],
        "display_name": record["display_name"],
        "brand": record["brand"],
        "price": float(record["price"]),
        "currency": record["currency"],
        "spec": record["spec"],
        "updated_at": _now_iso(),
    }


@router.post("/order/add")
def mock_order_add(
    request: MockOrderAddRequest, db: Session = Depends(get_session)
) -> dict[str, Any]:
    record = _find_product_record(request.product_id)
    if not record:
        return _product_miss(request.product_id)
    unit_price = record["price"]
    total_amount = unit_price * Decimal(request.quantity)
    order_id = _normalize_id(request.order_id) if request.order_id else f"ADD{uuid4().hex[:10].upper()}"
    result = {
        "found": True,
        "order_id": order_id,
        "user_id": request.user_id,
        "product_id": record["product_id"],
        "display_name": record["display_name"],
        "sku_id": request.sku_id,
        "quantity": request.quantity,
        "unit_price": float(unit_price),
        "total_amount": float(total_amount),
        "currency": "CNY",
        "status": request.status,
        "created_at": _now_iso(),
    }
    _upsert_dynamic_order(
        db,
        order_id=order_id,
        user_id=request.user_id,
        product_id=record["product_id"],
        sku_id=request.sku_id,
        quantity=request.quantity,
        status=request.status,
        payment_status=None,
        order_status=request.status,
        total_amount=float(total_amount),
        currency="CNY",
        metadata={},
    )
    return result


def _find_product_record(value: str) -> dict[str, Any] | None:
    normalized_id = _normalize_id(value)
    if normalized_id in PRODUCT_CATALOG:
        return PRODUCT_CATALOG[normalized_id]

    normalized_name = _normalize_product_name(value)
    if normalized_name in PRODUCT_NAME_CATALOG:
        return PRODUCT_NAME_CATALOG[normalized_name]

    for record in (*PRODUCT_CATALOG.values(), *PRODUCT_NAME_CATALOG.values()):
        if _normalize_id(record["product_id"]) == normalized_id:
            return record
        if _normalize_product_name(record["display_name"]) == normalized_name:
            return record
    return None


def _product_miss(product_name: str) -> dict[str, Any]:
    return {
        "product_name": product_name,
        "found": False,
        "results": [],
        "miss_reason": "product_not_found",
        "hint": "可尝试使用 iPhone 15、三星S24、小米14、A1、A3 或 SKU-001/SKU-002/SKU-003 作为 mock 商品名。",
    }


def _normalize_id(value: str) -> str:
    return value.strip().upper()


def _normalize_product_name(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _order_hit(order_id: str, source: str, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": order_id,
        "found": True,
        "source": source,
        **record,
    }


def _order_miss(order_id: str, source: str) -> dict[str, Any]:
    return {
        "order_id": order_id,
        "found": False,
        "source": source,
        "results": [],
        "miss_reason": "source_miss",
        "hint": "当前订单中心未命中，可尝试其他已配置的订单查询工具。",
    }


def _find_dynamic_order(db: object, order_id: str) -> dict[str, Any] | None:
    if not isinstance(db, Session):
        return None
    row = db.get(MockOrder, order_id)
    if not row:
        return None
    return {
        "status": row.status,
        "signed_days": row.signed_days,
        "refundable": row.refundable,
        "user_id": row.user_id,
        "product_id": row.product_id,
        "sku_id": row.sku_id,
        "quantity": row.quantity,
        "payment_status": row.payment_status,
        "order_status": row.order_status,
        "total_amount": row.total_amount,
        "currency": row.currency,
        "created_at": row.created_at.isoformat(),
        "recommendation": "该订单已在 mock 订单中心创建，可继续进行订单查询、取消或售后流程。",
        **(row.metadata_json or {}),
    }


def _upsert_dynamic_order(
    db: object,
    *,
    order_id: str,
    user_id: str,
    product_id: str,
    sku_id: str | None,
    quantity: int,
    status: str,
    payment_status: str | None,
    order_status: str | None,
    total_amount: float,
    currency: str,
    metadata: dict[str, Any],
) -> None:
    if not isinstance(db, Session):
        return
    normalized_order_id = _normalize_id(order_id)
    row = db.get(MockOrder, normalized_order_id)
    now = utc_now()
    if not row:
        row = MockOrder(order_id=normalized_order_id, created_at=now)
    row.user_id = user_id
    row.product_id = product_id
    row.sku_id = sku_id
    row.quantity = quantity
    row.status = status
    row.payment_status = payment_status
    row.order_status = order_status
    row.signed_days = 0
    row.refundable = True
    row.total_amount = total_amount
    row.currency = currency
    row.metadata_json = metadata
    row.updated_at = now
    db.add(row)
    db.commit()


def _now_iso() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat()
