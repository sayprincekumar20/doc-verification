"""Pull mode: the button marks the Account; the engine polls Zoho and starts the job."""

import json

import httpx

from app.db.models import VerificationJob
from app.services.request_poller import poll_once
from app.zoho.auth import ZohoTokenProvider
from app.zoho.client import ZohoClient

ACC = "4776053000067384571"


class FakeZoho:
    def __init__(self, marked, fail_update=False):
        self.marked = marked
        self.fail_update = fail_update
        self.updates = []
        self.searches = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth/v2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if path.endswith("/Accounts/search"):
            self.searches.append(request.url.params["criteria"])
            if not self.marked:
                return httpx.Response(204)
            return httpx.Response(200, json={"data": self.marked,
                                             "info": {"more_records": False}})
        if request.method == "PUT" and path.endswith("/Accounts"):
            if self.fail_update:
                return httpx.Response(500, json={"code": "INTERNAL_ERROR"})
            self.updates.append(json.loads(request.content))
            return httpx.Response(200, json={"data": [{"code": "SUCCESS", "details": {}}]})
        return httpx.Response(404)

    def client(self, settings):
        http = httpx.Client(transport=httpx.MockTransport(self.handler))
        return ZohoClient(settings, ZohoTokenProvider(settings, http=http), http=http,
                          sleep=lambda _: None)


MARKED = {"id": ACC, "Inventory_Customer_Number": "EXAMPLE-001", "DV_Requested": True,
          "DV_Request_Reason": "for activation", "DV_Requested_By": "rep@example.com",
          "DV_Requested_At": "2026-10-21T16:23:01+08:00"}


def test_marked_account_starts_one_job_and_mark_is_cleared(db, settings):
    fake, queued = FakeZoho([MARKED]), []
    started = poll_once(db, fake.client(settings), settings, queued.append)
    assert len(started) == 1 and queued == started
    assert fake.searches == ["(DV_Requested:equals:true)"]
    assert fake.updates[0]["data"][0] == {"id": ACC, "DV_Requested": False}
    assert fake.updates[0]["trigger"] == []
    job = db.query(VerificationJob).one()
    assert job.account_id == ACC and job.reason == "for activation"
    assert job.requested_by_email == "rep@example.com" and job.source == "crm_button"


def test_second_click_while_running_does_not_start_another_job(db, settings):
    queued = []
    poll_once(db, FakeZoho([MARKED]).client(settings), settings, queued.append)
    poll_once(db, FakeZoho([MARKED]).client(settings), settings, queued.append)
    assert len(queued) == 1 and db.query(VerificationJob).count() == 1


def test_nothing_marked(db, settings):
    queued = []
    assert poll_once(db, FakeZoho([]).client(settings), settings, queued.append) == []


def test_mark_not_cleared_means_no_job_this_round(db, settings):
    queued = []
    fake = FakeZoho([MARKED], fail_update=True)
    assert poll_once(db, fake.client(settings), settings, queued.append) == []
    assert db.query(VerificationJob).count() == 0
