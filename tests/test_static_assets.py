"""Front-end files: path guard, service-worker shell list, labels, CSP-compatibility, no CDNs."""
import re
from html.parser import HTMLParser

import pytest

from atsc.dashboard.assets import STATIC_DIR, build_id, index_html, resolve_static, service_worker_js

JS_DIR = STATIC_DIR / "js"


@pytest.mark.parametrize("rel", ["../assets.py", "js/../../server.py", "/etc/passwd", "",
                                 "js/", "js\\main.js", "./styles.css", "js//main.js",
                                 "styles.css\x00.png", "js", "missing.js"])
def test_resolve_static_rejects_anything_unsafe(rel):
    assert resolve_static(rel) is None


def test_resolve_static_finds_real_files():
    assert resolve_static("styles.css") == STATIC_DIR / "styles.css"
    assert resolve_static("js/main.js") == JS_DIR / "main.js"


def test_build_id_is_stable_and_templated():
    assert build_id() == build_id() and re.fullmatch(r"[0-9a-f]{12}", build_id())
    page = index_html().decode()
    assert "__ATSC_" not in page and build_id() in page
    assert 'data-transport="poll"' in index_html("poll").decode()
    with pytest.raises(ValueError):
        index_html("carrier-pigeon")
    assert "__ATSC_BUILD__" not in service_worker_js().decode()


def test_service_worker_precaches_exactly_the_page_shell():
    sw = (STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    block = sw[sw.index("const SHELL = ["):sw.index("];", sw.index("const SHELL = ["))]
    shell = set(re.findall(r'"([^"]+)"', block))
    expected = {"/", "/static/styles.css", "/static/icon.svg"}
    expected |= {f"/static/js/{p.name}" for p in JS_DIR.glob("*.js")}
    assert shell == expected, shell ^ expected
    for url in shell - {"/"}:
        assert resolve_static(url[len("/static/"):]) is not None, url


def test_every_module_import_resolves():
    for path in JS_DIR.glob("*.js"):
        for target in re.findall(r'from\s+"(\./[^"]+)"', path.read_text(encoding="utf-8")):
            assert (path.parent / target).resolve().is_file(), f"{path.name} imports {target}"


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields, self.labels, self.resources, self.inline = [], set(), [], []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("input", "select", "textarea") and a.get("type") != "hidden":
            self.fields.append(a)
        if tag == "label" and a.get("for"):
            self.labels.add(a["for"])
        if tag == "script":
            if a.get("src"):
                self.resources.append(a["src"])
            else:
                self.inline.append("<script> without src")
        if tag == "link" and a.get("href"):
            self.resources.append(a["href"])
        if tag == "style":
            self.inline.append("<style>")
        if "style" in a:
            self.inline.append(f"style= on <{tag}>")
        for key in a:
            if key.startswith("on"):
                self.inline.append(f"{key}= on <{tag}>")


def _page():
    parser = _Page()
    parser.feed((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
    return parser


def test_every_form_field_has_a_label():
    page = _page()
    assert page.fields, "no form fields found"
    for field in page.fields:
        assert field.get("id") and field.get("name"), field
        assert field["id"] in page.labels or field.get("aria-label"), f"unlabelled field {field}"


def test_page_is_compatible_with_the_content_security_policy():
    page = _page()
    assert page.inline == [], page.inline
    for url in page.resources:
        assert url.startswith("/static/"), url
        assert resolve_static(url[len("/static/"):]) is not None, url


def test_no_third_party_code_or_fonts():
    for path in list(JS_DIR.glob("*.js")) + [STATIC_DIR / "styles.css", STATIC_DIR / "sw.js"]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r'(import|from)\s*\(?\s*["\']https?:', text), path.name
        assert not re.search(r"url\(\s*['\"]?https?:", text), path.name
        assert "fonts.googleapis" not in text and "cdn." not in text, path.name
