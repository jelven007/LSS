"""Frontend acceptance checks validate links, rather than just a file manifest."""
import pytest

from check_static import check


def test_frontend_bundle_requires_all_entry_resources(tmp_path):
    (tmp_path / "index.html").write_text(
        '<script src="/assets/main.js"></script><link rel="stylesheet" href="/assets/main.css">'
    )
    with pytest.raises(ValueError, match=r"main\.js"):
        check(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "main.js").write_text("export {}")
    with pytest.raises(ValueError, match=r"main\.css"):
        check(tmp_path)
    (assets / "main.css").write_text("")
    assert check(tmp_path) == 2


def test_frontend_bundle_rejects_path_escape(tmp_path):
    (tmp_path / "index.html").write_text('<script src="/../outside.js"></script>')
    with pytest.raises(ValueError, match="outside bundle"):
        check(tmp_path)
