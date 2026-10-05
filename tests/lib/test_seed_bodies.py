"""`seed_bodies` — the file half of delivering a seed by command token."""

from __future__ import annotations

import os
import time

import pytest

from cockpit.lib import seed_bodies


def test_a_written_body_reads_back_byte_for_byte():
    body = "line one\n\n`tick` $HOME \"dq\" 'sq' \\ & | ;\nlast"
    seed_id = seed_bodies.write(body)
    assert seed_id is not None
    assert seed_bodies.read(seed_id) == body


@pytest.mark.covers("spawn.seed-id~1")
def test_an_id_that_could_name_another_file_is_refused():
    """The id arrives from the template's `$ARGUMENTS`, so anything but the
    shape `write` mints is refused before it reaches the filesystem."""
    seed_bodies.STATE_DIR.mkdir(parents=True, exist_ok=True)
    (seed_bodies.STATE_DIR.parent / "secret.txt").write_text("nope")
    assert seed_bodies.read("../secret") is None
    assert seed_bodies.read("ABCDEF123456") is None
    assert seed_bodies.read("") is None


def test_write_prunes_bodies_past_the_retention_window():
    old = seed_bodies.write("old")
    assert old is not None
    path = seed_bodies.STATE_DIR / f"{old}.txt"
    stale = time.time() - seed_bodies.RETAIN_SECONDS - 60
    os.utime(path, (stale, stale))

    fresh = seed_bodies.write("fresh")

    assert seed_bodies.read(old) is None
    assert fresh is not None and seed_bodies.read(fresh) == "fresh"


@pytest.mark.covers("spawn.seed-id~1")
def test_a_read_does_not_consume_the_body():
    """The seed retry re-sends the same token, which must still resolve."""
    seed_id = seed_bodies.write("body")
    assert seed_id is not None
    seed_bodies.read(seed_id)
    assert seed_bodies.read(seed_id) == "body"


def test_an_unwritable_store_returns_none_so_the_caller_types_the_body(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    seed_bodies.STATE_DIR = blocker / "seed-bodies"
    assert seed_bodies.write("body") is None


def test_command_installed_follows_the_template_file():
    assert seed_bodies.command_installed() is False
    seed_bodies.COMMAND_PATH.parent.mkdir(parents=True)
    seed_bodies.COMMAND_PATH.write_text("x")
    assert seed_bodies.command_installed() is True


def test_main_prints_the_body_without_adding_anything(capsys):
    seed_id = seed_bodies.write("do the thing\nthen this")
    assert seed_id is not None
    assert seed_bodies.main([seed_id]) == 0
    assert capsys.readouterr().out == "do the thing\nthen this"


@pytest.mark.covers("spawn.seed-id~1")
def test_main_explains_a_miss_instead_of_printing_nothing(capsys):
    """The output becomes the session's turn, so a blank one would leave the
    session with no task and no reason."""
    assert seed_bodies.main(["0123456789ab"]) == 0
    out = capsys.readouterr().out
    assert "no stored task" in out and "0123456789ab" in out


def test_the_token_names_the_bundled_command():
    assert seed_bodies.token("0123456789ab") == "/cockpit-seed 0123456789ab"
