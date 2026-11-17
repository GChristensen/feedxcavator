import importlib.util
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("install_script", ROOT / "install.py")
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


def parts(saved, argv):
    return install.choose_parts(saved, install.parse_args(argv))


def test_choice_adds_to_saved_parts():
    assert parts([], []) == []
    assert parts([], ["--with-browser"]) == ["camoufox"]
    assert parts(["captcha"], ["--with-browser=patchright,camoufox"]) == ["camoufox", "patchright", "captcha"]
    assert parts(["camoufox", "dev"], ["--with-postgres"]) == ["camoufox", "postgres", "dev"]


def test_without_removes():
    assert parts(["camoufox", "patchright", "captcha"], ["--without", "browser"]) == ["captcha"]
    assert parts(["camoufox", "captcha"], ["--without", "captcha", "--without", "camoufox"]) == []
    assert parts(["unknown", "dev"], []) == ["dev"]


def test_bad_names_are_refused():
    with pytest.raises(SystemExit):
        install.parse_args(["--with-browser=chrome"])
    with pytest.raises(SystemExit):
        install.parse_args(["--without", "gpu"])


def test_engine_in_config():
    example = (ROOT / "config.example.yml").read_text(encoding="utf-8")
    assert install.configured_engine(example) == "camoufox"
    changed = install.set_engine(example, "patchright")
    assert install.configured_engine(changed) == "patchright"
    assert changed.count("\n") == example.count("\n")
    assert install.configured_engine("user: me\n") == "camoufox"
    assert install.configured_engine(install.set_engine("user: me\n", "playwright")) == "playwright"
    assert install.configured_engine("browser-engine: 'Patchright'  # x\n") == "patchright"


def test_extras_match_the_script():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert set(project["optional-dependencies"]) == set(install.PARTS)


def test_constraints_follow_the_lock():
    """constraints.txt is exported from uv.lock (just lock); pip installs must get the same versions."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = {p["name"]: p["version"] for p in lock["package"] if p["name"] != "feedxcavator"}
    text = (ROOT / "constraints.txt").read_text(encoding="utf-8")
    pinned = dict(re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;]+)", text, re.M))
    normalize = lambda name: re.sub(r"[-_.]+", "-", name).lower()  # noqa: E731
    assert {normalize(k): v for k, v in pinned.items()} == {normalize(k): v for k, v in locked.items()}
