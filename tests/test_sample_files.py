from app.tools.sample_files import canonical, find, index_files


def test_matches_fetched_prefixed_and_underscored_names(tmp_path):
    files = tmp_path / "samples" / "1000000000000001" / "files"
    files.mkdir(parents=True)
    for name in ("attachment_5906238000094553109_BIR_2303_-_YAO_GILMARIE.png",
                 "attachment_5906238000061698187_IMG_20260113_092731.jpg",
                 "file_field_777_permit.pdf"):
        (files / name).write_bytes(b"x")
    (files / "extraction_results.json").write_text("{}", encoding="utf-8")
    (tmp_path / "samples" / "DENNY_S_-_P__OCAMPO__BAC_.pdf").write_bytes(b"x")

    index = index_files([tmp_path / "samples"])
    assert find(index, "BIR 2303 - YAO GILMARIE.png").name.endswith("YAO_GILMARIE.png")
    assert find(index, "BIR_2303_-_YAO_GILMARIE.png") is not None
    assert find(index, "IMG_20260113_092731.jpg") is not None
    assert find(index, "permit.pdf") is not None
    assert find(index, "DENNY_S_-_P__OCAMPO__BAC_.pdf") is not None
    assert find(index, "extraction_results.json") is None
    assert find(index, "IMG_20260113_092805.jpg") is None


def test_canonical():
    assert canonical("attachment_123_DTI CERT - X.jpg") == canonical("DTI_CERT_-_X.jpg")
    assert canonical("IMG_20260113_092731.jpg") != canonical("IMG_20260113_092749.jpg")
