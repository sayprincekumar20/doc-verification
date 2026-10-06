import pytest

from app.zoho.auth import ZohoTokenProvider
from app.zoho.client import ZohoClient
from app.zoho.errors import FileTooLargeError, ZohoAPIError, ZohoTransientError
from tests.fakes import ACCOUNT_ID, JPEG, FakeZoho


def make_client(settings, fake):
    http = fake.http()
    return ZohoClient(settings, ZohoTokenProvider(settings, http=http), http=http,
                      sleep=lambda _: None)


def test_token_is_cached(settings):
    fake = FakeZoho()
    client = make_client(settings, fake)
    client.get_record("Accounts", ACCOUNT_ID)
    client.get_record("Accounts", ACCOUNT_ID)
    assert fake.token_requests == 1


def test_refreshes_token_once_on_401(settings):
    fake = FakeZoho()
    fake.fail_next = [401]
    client = make_client(settings, fake)
    assert client.get_record("Accounts", ACCOUNT_ID)["Account_Name"] == "Example Market"
    assert fake.token_requests == 2


def test_retries_rate_limit_then_succeeds(settings):
    fake = FakeZoho()
    fake.fail_next = [429, 503]
    client = make_client(settings, fake)
    assert client.get_record("Accounts", ACCOUNT_ID)["id"] == ACCOUNT_ID


def test_gives_up_after_max_retries(settings):
    fake = FakeZoho()
    fake.fail_next = [503] * 20
    client = make_client(settings, fake)
    with pytest.raises(ZohoTransientError):
        client.get_record("Accounts", ACCOUNT_ID)


def test_client_error_is_not_retried(settings):
    fake = FakeZoho()
    fake.fail_next = [400]
    client = make_client(settings, fake)
    with pytest.raises(ZohoAPIError):
        client.get_record("Accounts", ACCOUNT_ID)


def test_empty_attachment_list_returns_empty(settings):
    assert make_client(settings, FakeZoho()).list_attachments("Accounts", ACCOUNT_ID) == []


def test_download_size_limit(settings):
    fake = FakeZoho()
    fake.attachments = [{"id": "1", "name": "big.jpg", "data": JPEG + b"0" * 2_000_000}]
    client = make_client(settings, fake)
    with pytest.raises(FileTooLargeError):
        client.download_attachment("Accounts", ACCOUNT_ID, "1", settings.max_file_bytes)
