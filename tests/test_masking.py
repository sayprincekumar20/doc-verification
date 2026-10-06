from app.logging import mask_sensitive


def test_masks_tin_with_branch_code():
    assert mask_sensitive("TIN 123-456-789-000 ok") == "TIN ***000 ok"


def test_masks_plain_nine_digit_tin():
    assert mask_sensitive("tin=123456789") == "tin=***789"


def test_keeps_zoho_record_ids():
    text = "account 1000000000000001"
    assert mask_sensitive(text) == text


def test_masks_oauth_tokens():
    assert "abc.def" not in mask_sensitive("Authorization: Zoho-oauthtoken abc.def")
    assert "s3cret" not in mask_sensitive('{"refresh_token": "s3cret"}')
