"""Durability and file-mode guarantees of atomic_bytes (imports only cdcad._common)."""
import os
import stat

import pytest

from cdcad._common import atomic_bytes


def test_failed_replace_keeps_the_old_file_and_leaves_no_temporary(tmp_path, monkeypatch):
    target = tmp_path/"model.stl"
    atomic_bytes(target, b"first")

    def fail(source, destination):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        atomic_bytes(target, b"second")
    assert target.read_bytes() == b"first"
    assert [p.name for p in tmp_path.iterdir()] == ["model.stl"]


def test_data_is_synced_before_it_replaces_the_target(tmp_path, monkeypatch):
    events = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd):
        events.append("fsync")
        real_fsync(fd)

    def replace(source, destination):
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    atomic_bytes(tmp_path/"a.bin", b"x")
    assert events == ["fsync", "replace"]
    assert (tmp_path/"a.bin").read_bytes() == b"x"


def test_parent_directories_are_created_and_binary_data_is_exact(tmp_path):
    data = bytes(range(256)) + b"\r\n\x1a\r\n"
    atomic_bytes(tmp_path/"deep"/"er"/"blob.bin", data)
    assert (tmp_path/"deep"/"er"/"blob.bin").read_bytes() == data


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_files_get_the_mode_a_normal_write_would_not_owner_only(tmp_path):
    previous = os.umask(0o022)
    try:
        atomic_bytes(tmp_path/"a.bin", b"x")
        atomic_bytes(tmp_path/"a.bin", b"y")  # replacing must not tighten it either
    finally:
        os.umask(previous)
    assert stat.S_IMODE((tmp_path/"a.bin").stat().st_mode) == 0o644
