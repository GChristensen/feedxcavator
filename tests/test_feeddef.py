import pytest

from feedxcavator import feeddef, tasks


def test_defaults_and_extra_fields():
    definition, _ = feeddef.parse_definition("title: T\nsource: https://e.test\ncustom: [1, 2]\n")
    feed = feeddef.make_feed(definition, "news/local/city-paper")
    assert feed.suffix == "city-paper"  # defaults to the directory name
    assert feed.group == "news/local"
    assert feed.custom == [1, 2] and feed["custom"] == [1, 2]  # extra fields
    assert feed.enabled is True and feed.parallel is True and feed.tasks == []
    assert feed.fetch["backend"] == "http" and feed.fetch["charset"] == "auto" and feed.fetch["retry"] == 2
    assert feed.output == {"format": "rss", "limit": None}
    assert feed.missing is None and feed.get("missing", 1) == 1
    with pytest.raises(TypeError):
        feed.title = "x"


def test_legacy_spellings_are_converted():
    definition, _ = feeddef.parse_definition(
        "title: T\nsource: https://e.test\ntask: news\nrealtime: true\npartition: 50\ncharset: windows-1251\n"
        "proxy: default\ntimeout: 0\ngroup: a/b\nselectors: {item: div}\n"
        "filter:\n  history: true\n  content: title+summary\n  wordfilter: spam\n"
    )
    assert definition["tasks"] == ["news"]
    assert definition["websub"] == {"enabled": True, "partition": 50}
    assert definition["fetch"]["charset"] == "windows-1251" and definition["fetch"]["proxy"] == "default"
    assert definition["fetch"]["timeout"] == 30
    assert "group" not in definition
    assert definition["filter"] == [{"history": {"key": "link", "keep": None, "keep_days": None}},
                                    {"wordfilter": {"name": "spam", "fields": ["title", "summary"]}}]
    assert definition["mode"] == "selectors"


def test_validation_errors_carry_line_numbers():
    text = "title: T\nsource: https://e.test\noutput: edn\nfetch:\n  backend: carrier-pigeon\nselectors:\n  item: 'div['\n"
    with pytest.raises(feeddef.DefinitionError) as error:
        feeddef.parse_definition(text)
    by_line = {d.line: d.message for d in error.value.diagnostics}
    assert "EDN" in by_line[3]  # edn output is no longer supported
    assert "backend" in by_line[5]
    assert any("div[" in m or "item" in m for line, m in by_line.items() if line in (6, 7))
    with pytest.raises(feeddef.DefinitionError) as error:
        feeddef.parse_definition("title: [unclosed\nsource: x\n")
    assert "YAML syntax error" in error.value.diagnostics[0].message


def test_extends_merges_deeply_and_detects_cycles():
    templates = {
        "forums/phpbb": {"extends": "forums/base", "selectors": {"item": "li.row", "title": "a.topictitle"},
                         "fetch": {"charset": "utf-8"}},
        "forums/base": {"output": "atom", "selectors": {"author": ".author"}, "id": "f" * 32},
        "loop": {"extends": "loop"},
    }
    definition, _ = feeddef.parse_definition(
        "source: https://f.test\nextends: forums/phpbb\nselectors: {title: a.t}\nfetch: {timeout: 5}\n",
        templates.__getitem__,
    )
    assert definition["selectors"] == {"author": ".author", "item": "li.row", "title": "a.t"}
    assert definition["fetch"]["charset"] == "utf-8" and definition["fetch"]["timeout"] == 5
    assert definition["output"]["format"] == "atom"
    assert definition["id"] is None  # a template has no id
    with pytest.raises(feeddef.DefinitionError) as error:
        feeddef.parse_definition("extends: loop\n", templates.__getitem__)
    assert "cyclic" in str(error.value)


def test_set_suffix_keeps_the_text():
    text = "# my feed\n---\ntitle: T # keep\nsuffix: old   # the url\nsource: x\n"
    assert feeddef.set_suffix(text, "new-one") == "# my feed\n---\ntitle: T # keep\nsuffix: new-one   # the url\nsource: x\n"
    assert feeddef.set_suffix("# c\ntitle: T\nsource: x", "s") == "# c\ntitle: T\nsuffix: s\nsource: x\n"
    assert feeddef.set_suffix("# c\nsource: x\n", "s") == "# c\nsuffix: s\nsource: x\n"
    nested = "title: T\nparams:\n  suffix: inner\n"
    assert feeddef.set_suffix(nested, "s") == "title: T\nsuffix: s\nparams:\n  suffix: inner\n"


def test_identity_is_the_suffix_unless_an_id_is_set():
    plain, _ = feeddef.parse_definition("source: https://e.test\nsuffix: My.Feed_1~x\n")
    feed = feeddef.make_feed(plain, "a/dir-name")
    assert feed.id is None and feed.key == "My.Feed_1~x" and feed.suffix == "My.Feed_1~x"
    assert feeddef.make_feed(feeddef.parse_definition("source: x\n")[0], "a/dir-name").key == "dir-name"
    with_id, _ = feeddef.parse_definition(f"id: {'1' * 32}\nsuffix: s\nsource: x\n")  # all digits: a YAML number
    feed = feeddef.make_feed(with_id, "a/b")
    assert feed.id == "1" * 32 and feed.key == "1" * 32 and feed.uuid == "1" * 32
    for bad in ("новости", "a b", "a/b", "-lead", "q?x", ""):
        with pytest.raises(feeddef.DefinitionError) as error:
            feeddef.parse_definition(f"suffix: '{bad}'\nsource: x\n")
        assert "suffix" in str(error.value)
    with pytest.raises(feeddef.DefinitionError):
        feeddef.parse_definition("id: not-an-id\nsource: x\n")


def test_new_feed_template_is_valid():
    text = feeddef.NEW_FEED_TEMPLATE.format(suffix="untitled-feed")
    definition, _ = feeddef.parse_definition(text)
    assert definition["id"] is None and definition["suffix"] == "untitled-feed"
    # every commented example line is valid once uncommented (apart from the duplicate `pages` block)
    lines, seen_pages = [], False
    for line in text.splitlines():
        if line.startswith("#pages:"):
            if seen_pages:
                break
            seen_pages = True
        lines.append(line[1:] if line.startswith("#") else line)
    uncommented = "\n".join(l for l in lines if not l.startswith(("extends", "transform", "extractor")))
    assert feeddef.diagnose(uncommented) == []


def test_tasks_yaml():
    parsed = tasks.parse_tasks(
        "tasks:\n  news:\n    feeds: { tags: [news] }\n  local:\n    feeds: { path: 'news/local/*', list: [a] }\n"
        "  morning:\n    pre: refresh\n    run: [news, local]\n    post: [a, b]\n    then: [aggregate]\n"
        "    concurrency: queue\n    timeout: 30m\n  cleanup:\n    call: purge\n"
    )
    assert parsed["news"].tags == ["news"]
    assert parsed["local"].paths == ["news/local/*"] and parsed["local"].suffixes == ["a"]
    morning = parsed["morning"]
    assert (morning.pre, morning.post, morning.run, morning.then) == (["refresh"], ["a", "b"], ["news", "local"],
                                                                     ["aggregate"])
    assert morning.timeout == 1800 and morning.post_timeout == 300 and morning.concurrency == "queue"
    assert parsed["cleanup"].call == "purge"
    with pytest.raises(feeddef.DefinitionError) as error:
        tasks.parse_tasks("tasks:\n  bad:\n    concurrency: sometimes\n    oops: 1\n")
    assert {d.line for d in error.value.diagnostics} == {3, 4}
    assert tasks.find_cycles(tasks.parse_tasks("tasks:\n  a: {run: [b]}\n  b: {run: [c]}\n  c: {run: [a]}\n  d: {run: [a]}\n")) \
        == {"a", "b", "c"}
    assert tasks.parse_duration("1h30m") == 5400 and tasks.parse_duration(45) == 45


def test_schedules_yaml():
    parsed = tasks.parse_schedules(
        "schedules:\n  - task: news\n    cron: '0 12 * * *'\n  - task: news\n    cron: '0 12 * * *'\n"
        "  - task: digest\n    cron: '30 8 * * mon'\n    timezone: Europe/Kyiv\n    misfire: run-once\n"
        "  - task: report\n    cron: '0 6 15 * *'\n    enabled: false\n"
    )
    assert [s.task for s in parsed] == ["news", "digest", "report"]  # the duplicate is ignored
    assert parsed[1].misfire == "run-once" and str(parsed[1].tzinfo()) == "Europe/Kyiv"
    assert parsed[2].enabled is False
    fires = parsed[1].next_fires(2)
    assert all(f.weekday() == 0 and (f.hour, f.minute) == (8, 30) for f in fires)
    for bad in ("cron: 'every day'", "cron: '0 12 * *'", "cron: '0 12 * * *'\n    timezone: Mars/Phobos"):
        with pytest.raises(feeddef.DefinitionError):
            tasks.parse_schedules(f"schedules:\n  - task: x\n    {bad}\n")
