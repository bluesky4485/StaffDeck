from app.api.mock import MockOrderQueryRequest, mock_order_archive_query, mock_order_query


def test_primary_order_query_returns_configured_order() -> None:
    result = mock_order_query(MockOrderQueryRequest(order_id="ORDER-1001"))

    assert result["found"] is True
    assert result["source"] == "primary_order_center"
    assert result["refundable"] is True


def test_primary_order_query_returns_miss_for_unknown_primary_order() -> None:
    result = mock_order_query(MockOrderQueryRequest(order_id="ARCHIVE-1001"))

    assert result["found"] is False
    assert result["miss_reason"] == "source_miss"


def test_archive_order_query_returns_refundable_history_order() -> None:
    result = mock_order_archive_query(MockOrderQueryRequest(order_id="ARCHIVE-1001"))

    assert result["found"] is True
    assert result["source"] == "archive_order_center"
    assert result["refundable"] is True
