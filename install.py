#!/usr/bin/env python3
"""Installs Feedxcavator into .venv next to this script, with the dependency versions pinned in uv.lock.

    python3 install.py [--with-browser[=ENGINE]] [--with-captcha] [--with-postgres] [--with-dev]
                       [--without NAME] [--pip] [--no-fetch] [--no-check]

Run it again after updating the code: without options it reinstalls the parts chosen before, which are kept in
.venv/feedxcavator-install.json. Options add to that choice; --without removes a part.

With uv on PATH the environment is made with `uv sync --locked`, which also fetches a suitable Python and removes
packages that are no longer chosen. Otherwise it uses venv and pip with constraints.txt (exported from uv.lock),
which needs Python 3.12 or later and does not uninstall anything.

Only the standard library is used here, and the syntax is kept old, so any python3 can start the script.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
STATE = VENV / "feedxcavator-install.json"
WINDOWS = os.name == "nt"

ENGINES = ("camoufox", "patchright", "playwright")
PARTS = ENGINES + ("captcha", "postgres", "dev")
DEFAULT_ENGINE = "camoufox"
MIN_PYTHON = (3, 12)


class InstallError(Exception):
    pass


def say(message=""):
    print(message, flush=True)


def note(message):
    say("  note: " + message)


def venv_python():
    return VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")


def run(cmd, cwd=ROOT, check=True, **kwargs):
    say("> " + " ".join(str(c) for c in cmd))
    result = subprocess.run([str(c) for c in cmd], cwd=str(cwd), **kwargs)
    if check and result.returncode != 0:
        raise InstallError("command failed with exit code {}: {}".format(result.returncode, cmd[0]))
    return result


def quiet(cmd, timeout=None):
    """Runs a probe; returns (ok, combined output)."""
    try:
        result = subprocess.run([str(c) for c in cmd], cwd=str(ROOT), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, universal_newlines=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return result.returncode == 0, result.stdout


# --- choice of parts -------------------------------------------------------------------------------------------

def parse_args(argv):
    parser = argparse.ArgumentParser(prog="install.py", description="Install Feedxcavator into .venv.")
    parser.add_argument("--with-browser", nargs="?", const=DEFAULT_ENGINE, metavar="ENGINE",
                        help="browser engines, comma-separated: camoufox (default), patchright, playwright; "
                             "the first one is written to a new config.yml")
    parser.add_argument("--with-captcha", action="store_true", help="pyautogui, for solve-captcha")
    parser.add_argument("--with-postgres", action="store_true", help="psycopg, for a PostgreSQL database")
    parser.add_argument("--with-dev", action="store_true", help="pytest, for development")
    parser.add_argument("--without", action="append", default=[], metavar="NAME",
                        help="remove a part chosen before: an engine, browser (all engines), captcha, postgres "
                             "or dev; may be repeated")
    parser.add_argument("--pip", action="store_true", help="use venv and pip even when uv is available")
    parser.add_argument("--no-fetch", action="store_true", help="do not download browser builds")
    parser.add_argument("--no-check", action="store_true", help="do not test-launch camoufox")
    args = parser.parse_args(argv)
    args.engines = []
    if args.with_browser is not None:
        for name in args.with_browser.split(","):
            name = name.strip().lower()
            if name not in ENGINES:
                parser.error("unknown browser engine: {} (one of {})".format(name, ", ".join(ENGINES)))
            if name not in args.engines:
                args.engines.append(name)
    for name in args.without:
        if name.lower() not in PARTS + ("browser",):
            parser.error("unknown part: {} (one of browser, {})".format(name, ", ".join(PARTS)))
    return args


def choose_parts(saved, args):
    """The saved parts plus those the options add, minus --without; in PARTS order."""
    chosen = set(p for p in saved if p in PARTS)
    chosen.update(args.engines)
    for flag in ("captcha", "postgres", "dev"):
        if getattr(args, "with_" + flag):
            chosen.add(flag)
    for name in args.without:
        name = name.lower()
        chosen -= set(ENGINES) if name == "browser" else {name}
    return [p for p in PARTS if p in chosen]


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8")).get("parts", [])
    except (OSError, ValueError, AttributeError):
        return []


def save_state(parts):
    STATE.write_text(json.dumps({"parts": parts}, indent=2) + "\n", encoding="utf-8")


# --- environment -----------------------------------------------------------------------------------------------

def install_with_uv(uv, parts):
    cmd = [uv, "sync", "--locked", "--project", ROOT]
    for part in parts:
        cmd += ["--extra", part]
    run(cmd)


def python_version(python):
    ok, out = quiet([python, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"])
    if not ok:
        return None
    major, minor = out.strip().split(".")
    return int(major), int(minor)


def install_with_pip(parts, removed):
    python = venv_python()
    if not python.exists():
        if sys.version_info[:2] < MIN_PYTHON:
            raise InstallError("Python {}.{} or later is needed, this is {}.{}. Run the script with a newer "
                               "python, or install uv, which fetches one.".format(*(MIN_PYTHON + sys.version_info[:2])))
        try:
            run([sys.executable, "-m", "venv", VENV])
        except InstallError:
            raise InstallError("could not create .venv; on Debian/Ubuntu install the python3-venv package")
    version = python_version(python)
    if version is None or version < MIN_PYTHON:
        raise InstallError(".venv has Python {}, but {}.{} or later is needed: delete .venv and run the script "
                           "with a newer python".format("%d.%d" % version if version else "?", *MIN_PYTHON))
    if not quiet([python, "-m", "pip", "--version"])[0]:  # a venv made by uv has no pip
        run([python, "-m", "ensurepip", "--upgrade"])
    target = ".[{}]".format(",".join(parts)) if parts else "."
    run([python, "-m", "pip", "install", "--disable-pip-version-check", "-c", ROOT / "constraints.txt", "-e",
         target])
    if removed:
        note("pip does not uninstall: the packages of {} stay in .venv. Delete .venv and run the script again to "
             "drop them.".format(", ".join(removed)))


# --- browsers --------------------------------------------------------------------------------------------------

CAMOUFOX_INSTALLED = "from camoufox.pkgman import camoufox_path; camoufox_path(download_if_missing=False)"
CAMOUFOX_LAUNCH = """
from camoufox.sync_api import Camoufox
with Camoufox(headless=True) as browser:
    browser.new_page().goto("about:blank")
"""


def find_chrome():
    """The installed Chrome that patchright and playwright use through browser-channel: chrome."""
    if WINDOWS:
        for base in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if os.environ.get(base):
                path = Path(os.environ[base]) / "Google" / "Chrome" / "Application" / "chrome.exe"
                if path.exists():
                    return str(path)
        return None
    if sys.platform == "darwin":
        path = Path("/Applications/Google Chrome.app")
        return str(path) if path.exists() else None
    for name in ("google-chrome", "google-chrome-stable"):
        if shutil.which(name):
            return shutil.which(name)
    return None


def fetch_browsers(parts, check):
    python = venv_python()
    problems = []
    if "camoufox" in parts:
        if quiet([python, "-c", CAMOUFOX_INSTALLED])[0]:
            say("camoufox: the browser build is already downloaded")
        else:
            run([python, "-m", "camoufox", "fetch"])
        if check:
            say("camoufox: test launch")
            ok, out = quiet([python, "-c", CAMOUFOX_LAUNCH], timeout=180)
            if ok:
                say("camoufox: launches")
            else:
                say("\n".join(out.strip().splitlines()[-8:]))
                problem = "camoufox does not launch"
                if not WINDOWS:
                    problem += ("; it needs the libraries Firefox needs (GTK 3, libX11-xcb, ALSA). Installing the "
                                "distribution's firefox package brings them in.")
                problems.append(problem)
    for engine in ("patchright", "playwright"):
        if engine in parts:
            chrome = find_chrome()
            if chrome:
                say("{}: uses the installed Chrome ({})".format(engine, chrome))
            else:
                say("{}: no Chrome found, downloading its Chromium".format(engine))
                run([python, "-m", engine, "install", "chromium"])
    return problems


# --- config.yml ------------------------------------------------------------------------------------------------

ENGINE_RE = re.compile(r"^browser-engine:[ \t]*['\"]?([A-Za-z]+)", re.M)


def configured_engine(text):
    match = ENGINE_RE.search(text)
    return match.group(1).lower() if match else DEFAULT_ENGINE


def set_engine(text, engine):
    new, count = re.subn(r"^(browser-engine:[ \t]*)\S+", lambda m: m.group(1) + engine, text, count=1, flags=re.M)
    return new if count else text.rstrip("\n") + "\nbrowser-engine: {}\n".format(engine)


def home_dir():
    return Path(os.environ.get("FEEDXCAVATOR_HOME") or ROOT)


def prepare_config(parts, first_engine):
    """Creates config.yml from the example when missing. Returns (path, created, problems)."""
    config = home_dir() / "config.yml"
    engines = [p for p in parts if p in ENGINES]
    problems = []
    if not config.exists():
        text = (ROOT / "config.example.yml").read_text(encoding="utf-8")
        engine = first_engine or (DEFAULT_ENGINE if DEFAULT_ENGINE in engines else (engines or [DEFAULT_ENGINE])[0])
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(set_engine(text, engine), encoding="utf-8")
        say("Created {} (browser-engine: {}). Edit hostname and the other settings there.".format(config, engine))
        return config, True, problems
    engine = configured_engine(config.read_text(encoding="utf-8"))
    if engines and engine not in engines:
        problems.append("{} has browser-engine: {}, which is not installed (installed: {})".format(
            config, engine, ", ".join(engines)))
    return config, False, problems


def offer_password(config):
    later = "set the console password with: .venv/bin/python -m feedxcavator set-password"
    if WINDOWS:
        later = later.replace(".venv/bin/python", r".venv\Scripts\python")
    try:
        answer = input("Set the console password now? [Y/n] ").strip().lower() if sys.stdin.isatty() else "n"
    except EOFError:
        answer = "n"
    if answer not in ("", "y", "yes"):
        note(later)
        return
    env = dict(os.environ, FEEDXCAVATOR_HOME=str(config.parent))
    python = venv_python()
    run([python, "-m", "feedxcavator", "migrate"], cwd=config.parent, env=env)
    run([python, "-m", "feedxcavator", "set-password"], cwd=config.parent, env=env, check=False)


# --- main ------------------------------------------------------------------------------------------------------

def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    saved = load_state()
    parts = choose_parts(saved, args)
    removed = [p for p in saved if p not in parts]
    say("Installing Feedxcavator into {} with: {}".format(VENV, ", ".join(parts) or "the core only"))

    uv = None if args.pip else shutil.which("uv")
    if uv:
        install_with_uv(uv, parts)
    else:
        install_with_pip(parts, removed)
    save_state(parts)

    problems = []
    if not args.no_fetch:
        problems += fetch_browsers(parts, check=not args.no_check)
    config, created, config_problems = prepare_config(parts, args.engines[0] if args.engines else None)
    problems += config_problems

    if not any(p in ENGINES for p in parts):
        note("no browser engine is installed; feeds with fetch.backend: browser fail until you run "
             "install.py --with-browser")
    elif not WINDOWS and sys.platform != "darwin" and not shutil.which("Xvfb") and not os.environ.get("DISPLAY"):
        note("Xvfb is not installed: without a display the browser runs headless, which is easier to detect. "
             "Package: xvfb (Debian/Ubuntu), xorg-x11-server-Xvfb (Fedora/RHEL).")
    if "captcha" in parts and not WINDOWS and not shutil.which("Xvfb") and not os.environ.get("DISPLAY"):
        note("solve-captcha moves the real pointer, so it needs an X display or Xvfb")
    if created:
        offer_password(config)

    for problem in problems:
        say("warning: " + problem)
    say("Done. Start the server with {}.".format("feedxcavator.cmd" if WINDOWS else "./feedxcavator.sh"))
    return 1 if problems else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except InstallError as e:
        say("error: " + str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
