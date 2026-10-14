from datetime import UTC, datetime

from repository import MediaRepository


class QueryTable:
    def __init__(self):
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("Select") == "COUNT":
            return {"Count": 2}
        if kwargs.get("ExclusiveStartKey"):
            return {"Items": [{"media_id": "second"}]}
        return {
            "Items": [{"media_id": "first"}],
            "LastEvaluatedKey": {"media_id": "first"},
        }


def test_list_fills_page_when_filter_excludes_evaluated_items():
    repository = MediaRepository.__new__(MediaRepository)
    repository.table = QueryTable()

    items, next_token, total_items, page = repository.list(
        owner_id="alice",
        page_size=2,
        tag="travel",
        created_from=None,
        created_to=datetime(2026, 10, 14, tzinfo=UTC),
        token=None,
    )

    assert [item["media_id"] for item in items] == ["first", "second"]
    assert next_token is None
    assert (total_items, page) == (2, 1)
    assert len(repository.table.calls) == 3
    assert repository.table.calls[0]["Limit"] == 2
    assert repository.table.calls[1]["Limit"] == 1
    assert repository.table.calls[2]["Select"] == "COUNT"
