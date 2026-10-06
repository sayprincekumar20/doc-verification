"""Windows defaults to the cp1252 encoding, which can't store characters Zoho returns
(e.g. "$currency_symbol": "₱"). Every text read/write must name its encoding."""

import ast
from pathlib import Path

from app.tools import fetch_account
from tests.fakes import ACCOUNT_ID, FakeZoho

ROOT = Path(__file__).resolve().parents[1]


def test_all_text_io_names_an_encoding():
    offenders = []
    for path in [*ROOT.glob("app/**/*.py"), *ROOT.glob("scripts/*.py")]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in ("read_text", "write_text"):
                name = func.attr
            elif isinstance(func, ast.Name) and func.id == "open":  # builtin open() only
                name = "open"
            else:
                continue
            if not any(
                kw.arg == "encoding" for kw in node.keywords
            ):
                if name == "open" and any(
                    isinstance(a, ast.Constant) and "b" in str(a.value) for a in node.args[1:]
                ):
                    continue  # binary mode
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {name}()")
    assert not offenders, offenders


def test_fetch_saves_peso_sign_as_utf8(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ZOHO_CLIENT_ID=a\nZOHO_CLIENT_SECRET=b\nZOHO_REFRESH_TOKEN=c\n",
                   encoding="utf-8")
    fake = FakeZoho.from_real_responses()
    fake.account["$currency_symbol"] = "₱"
    fetch_account.fetch(ACCOUNT_ID, tmp_path / "samples", env, http=fake.http())
    saved = (tmp_path / "samples" / ACCOUNT_ID / "account.json").read_text(encoding="utf-8")
    assert '"$currency_symbol": "₱"' in saved
