import pytest

from config import Settings
from errors import MediaError
from models import UploadRequest
from service import MediaService
from storage import VerifiedObject


class FakeRepository:
    def __init__(self):
        self.items = {}
        self.deleted = []
        self.fail_reads = False
        self.fail_lists = False

    def create_pending(self, item):
        self.items[item["media_id"]] = item

    def get(self, media_id):
        if self.fail_reads:
            raise RuntimeError("database unavailable")
        return self.items.get(media_id)

    def complete(self, media_id, fields):
        self.items[media_id].update(fields, status="AVAILABLE")
        return True

    def list(self, owner_id, page_size, tag, created_from, created_to, token):
        if self.fail_lists:
            raise RuntimeError("database unavailable")
        items = [item for item in self.items.values() if item["owner_id"] == owner_id]
        items = [item for item in items if not tag or tag in item.get("tags", [])]
        return items[:page_size], None, len(items), 1

    def delete(self, media_id):
        self.deleted.append(media_id)
        return self.items.pop(media_id, None) is not None


class FakeStorage:
    def __init__(self):
        self.deleted = []
        self.verified = []
        self.verified_size = 4
        self.fail_access = False

    def create_upload(self, *args):
        return {"url": "https://upload", "fields": {}}

    def verify_object(self, *args):
        self.verified.append(args)
        return VerifiedObject("image/jpeg", self.verified_size)

    def delete(self, *args):
        self.deleted.append(args)

    def create_access_url(self, *args):
        if self.fail_access:
            raise RuntimeError("storage unavailable")
        return "https://access"


def make_service():
    return MediaService(FakeRepository(), FakeStorage(), Settings())


def request():
    return UploadRequest(original_filename="a.jpg", content_type="image/jpeg", size_bytes=4)


def test_initiate_uses_owner_and_unique_ids():
    media = make_service()
    first = media.initiate("alice", request())
    second = media.initiate("alice", request())
    assert first.image_id != second.image_id
    assert all(item["owner_id"] == "alice" for item in media.repository.items.values())


def test_complete_is_idempotent():
    media = make_service()
    pending = media.initiate("alice", request())
    completed = media.complete("alice", pending.image_id)
    again = media.complete("alice", pending.image_id)
    assert completed.image_id == again.image_id
    assert len(media.storage.verified) == 1


def test_other_owner_gets_hidden_not_found():
    media = make_service()
    pending = media.initiate("alice", request())
    with pytest.raises(MediaError) as error:
        media.complete("bob", pending.image_id)
    assert (error.value.status_code, error.value.code) == (404, "IMAGE_NOT_FOUND")


def test_initiate_rejects_unsupported_type():
    media = make_service()
    with pytest.raises(MediaError) as error:
        media.initiate("alice", UploadRequest(original_filename="a.gif", content_type="image/gif", size_bytes=4))
    assert error.value.status_code == 415


def test_initiate_rejects_oversized_image():
    media = make_service()
    with pytest.raises(MediaError) as error:
        media.initiate(
            "alice",
            UploadRequest(original_filename="a.jpg", content_type="image/jpeg", size_bytes=11 * 1024 * 1024),
        )
    assert error.value.status_code == 413


def test_initiate_rejects_invalid_tags():
    with pytest.raises(ValueError):
        UploadRequest(original_filename="a.jpg", content_type="image/jpeg", size_bytes=4, tags=["not-valid"])


def test_initiate_rejects_blank_title():
    with pytest.raises(ValueError):
        UploadRequest(original_filename="a.jpg", content_type="image/jpeg", size_bytes=4, title="")


def test_list_hides_pending_and_applies_owner_and_tag_filter():
    media = make_service()
    first = media.initiate("alice", request())
    media.complete("alice", first.image_id)
    second = media.initiate("bob", request())
    media.complete("bob", second.image_id)
    media.repository.items[first.image_id]["tags"] = ["sunset"]
    response = media.list_images("alice", 20, "sunset", None, None, None)
    assert [item.image_id for item in response.items] == [first.image_id]
    assert (response.page, response.page_size) == (1, 20)
    assert (response.total_items, response.total_pages) == (1, 1)


def test_list_rejects_a_cursor_past_the_last_page():
    media = make_service()
    media.repository.list = lambda *args: ([], None, 25, 6)

    with pytest.raises(MediaError) as error:
        media.list_images("alice", 5, None, None, None, "cursor")

    assert (error.value.status_code, error.value.code) == (400, "VALIDATION_ERROR")


def test_access_rejects_unknown_mode_and_maps_storage_failure():
    media = make_service()
    pending = media.initiate("alice", request())
    media.complete("alice", pending.image_id)
    with pytest.raises(MediaError) as invalid:
        media.access("alice", pending.image_id, "raw")
    assert invalid.value.status_code == 400
    media.storage.fail_access = True
    with pytest.raises(MediaError) as unavailable:
        media.access("alice", pending.image_id, "preview")
    assert unavailable.value.status_code == 503


def test_repository_failures_are_dependency_errors():
    media = make_service()
    media.repository.fail_reads = True
    with pytest.raises(MediaError) as read_error:
        media.access("alice", "missing", "preview")
    assert read_error.value.status_code == 503

    media.repository.fail_reads = False
    media.repository.fail_lists = True
    with pytest.raises(MediaError) as list_error:
        media.list_images("alice", 20, None, None, None, None)
    assert list_error.value.status_code == 503


def test_complete_rejects_verified_size_mismatch():
    media = make_service()
    media.storage.verified_size = 3
    pending = media.initiate("alice", request())
    with pytest.raises(MediaError) as error:
        media.complete("alice", pending.image_id)
    assert error.value.status_code == 400


def test_delete_is_idempotent_at_the_ownership_boundary():
    media = make_service()
    pending = media.initiate("alice", request())
    media.complete("alice", pending.image_id)
    with pytest.raises(MediaError) as error:
        media.delete("bob", pending.image_id)
    assert error.value.code == "IMAGE_NOT_FOUND"
    media.delete("alice", pending.image_id)
    with pytest.raises(MediaError):
        media.delete("alice", pending.image_id)
