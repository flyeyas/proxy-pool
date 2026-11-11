# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     test_hourly_log_handler.py
   Description :   整点切割, 七天清理与并发写入回归测试
   Author :        JHao
   date：          2026/11/11
-------------------------------------------------
   Change Activity:
                   2026/11/11: 使用模拟时钟验证切割与清理, 验证 spawn/fork 并发
-------------------------------------------------
"""
__author__ = 'JHao'

import logging
import multiprocessing
import os
import threading
from datetime import datetime

import pytest

import handler.hourlyLogHandler as hourly
from handler.logHandler import listLogFiles, readRecentLines


def _make_logger(filename):
    log = logging.Logger("hourly_test", logging.INFO)
    handler = hourly.HourlyRotatingFileHandler(filename)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    return log, handler


def _write_worker(filename, now, barrier, prefix, inherited=None):
    hourly._now = lambda: now
    if inherited is None:
        log, handler = _make_logger(filename)
    else:
        log, handler = inherited
    try:
        barrier.wait(timeout=15)
        for number in range(80):
            log.info("%s-%s", prefix, number)
    finally:
        handler.close()


@pytest.fixture
def clock(monkeypatch):
    value = [datetime(2026, 11, 11, 10, 37).timestamp()]
    monkeypatch.setattr(hourly, "_now", lambda: value[0])
    return value


def test_rotates_at_clock_hour_not_startup_minute(tmp_path, clock):
    path = tmp_path / "fetch.log"
    log, handler = _make_logger(path)
    try:
        log.info("旧小时")
        clock[0] = datetime(2026, 11, 11, 11, 0).timestamp()
        log.info("新小时")
        assert (tmp_path / "fetch.log.2026111110").read_text() == "旧小时\n"
        assert path.read_text() == "新小时\n"
    finally:
        handler.close()


def test_restart_in_same_hour_keeps_current_records(tmp_path, clock):
    path = tmp_path / "fetch.log"
    first, first_handler = _make_logger(path)
    first.info("first")
    first_handler.close()
    second, second_handler = _make_logger(path)
    try:
        second.info("second")
        assert path.read_text().splitlines() == ["first", "second"]
        assert not list(tmp_path.glob("fetch.log.20*"))
    finally:
        second_handler.close()


def test_restart_after_hour_archives_previous_records(tmp_path, clock):
    path = tmp_path / "fetch.log"
    first, first_handler = _make_logger(path)
    first.info("before restart")
    first_handler.close()
    clock[0] += 3600
    second, second_handler = _make_logger(path)
    try:
        second.info("after restart")
        assert (tmp_path / "fetch.log.2026111110").read_text() == "before restart\n"
        assert path.read_text() == "after restart\n"
    finally:
        second_handler.close()


def test_stale_handler_reopens_file_rotated_by_another_handler(tmp_path, clock):
    path = tmp_path / "fetch.log"
    first, first_handler = _make_logger(path)
    second, second_handler = _make_logger(path)
    try:
        first.info("old")
        clock[0] += 3600
        second.info("new from second")
        first.info("new from first")
        assert (tmp_path / "fetch.log.2026111110").read_text() == "old\n"
        assert path.read_text().splitlines() == ["new from second", "new from first"]
    finally:
        first_handler.close()
        second_handler.close()


def test_sparse_archives_expire_by_age_even_when_touched(tmp_path, clock):
    expired = tmp_path / "fetch.log.2026110310"
    recent = tmp_path / "fetch.log.2026111010"
    expired.write_text("old")
    recent.write_text("recent")
    removed = hourly.cleanupExpiredLogs(tmp_path, clock[0])
    assert str(expired) in removed
    assert not expired.exists()
    assert recent.read_text() == "recent"


def test_seven_day_hour_boundary_is_kept(tmp_path, clock):
    boundary = tmp_path / "fetch.log.2026110410"
    older = tmp_path / "fetch.log.2026110409"
    boundary.write_text("within retained hour")
    older.write_text("expired")
    hourly.cleanupExpiredLogs(tmp_path, clock[0])
    assert boundary.exists()
    assert not older.exists()


def test_cleanup_rotates_idle_log_and_reopens_existing_handler(tmp_path, clock):
    path = tmp_path / "fetch.log"
    log, handler = _make_logger(path)
    try:
        log.info("idle hour")
        clock[0] += 3600
        hourly.cleanupExpiredLogs(tmp_path, clock[0])
        assert (tmp_path / "fetch.log.2026111110").read_text() == "idle hour\n"
        assert path.read_text() == ""
        log.info("back in use")
        assert path.read_text() == "back in use\n"
    finally:
        handler.close()


def test_cleanup_expires_legacy_active_log_without_new_writes(tmp_path, clock):
    path = tmp_path / "legacy.log"
    path.write_text("eight days ago")
    old_time = datetime(2026, 11, 3, 10).timestamp()
    os.utime(path, (old_time, old_time))
    hourly.cleanupExpiredLogs(tmp_path, clock[0])
    assert path.read_text() == ""
    assert not list(tmp_path.glob("legacy.log.20*"))


def test_constructor_cleans_expired_archives(tmp_path, clock):
    expired = tmp_path / "other.log.2026110310"
    expired.write_text("old")
    _, handler = _make_logger(tmp_path / "fetch.log")
    try:
        assert not expired.exists()
    finally:
        handler.close()


def test_collision_preserves_both_hourly_archives(tmp_path, clock):
    path = tmp_path / "fetch.log"
    log, handler = _make_logger(path)
    try:
        log.info("current")
        archive = tmp_path / "fetch.log.2026111110"
        archive.write_text("earlier archive\n")
        clock[0] += 3600
        log.info("next hour")
        assert archive.read_text() == "earlier archive\n"
        assert (tmp_path / "fetch.log.2026111110.1").read_text() == "current\n"
        assert path.read_text() == "next hour\n"
    finally:
        handler.close()


def test_cleanup_ignores_unrelated_files_directories_and_symlinks(tmp_path, clock):
    unrelated = tmp_path / "notes.txt"
    invalid = tmp_path / "fetch.log.2026999999"
    directory = tmp_path / "fetch.log.2026110310"
    target = tmp_path / "keep.txt"
    link = tmp_path / "fetch.log.2026110210"
    unrelated.write_text("keep")
    invalid.write_text("keep")
    directory.mkdir()
    target.write_text("keep")
    link.symlink_to(target)
    hourly.cleanupExpiredLogs(tmp_path, clock[0])
    assert unrelated.exists() and invalid.exists() and directory.is_dir()
    assert link.is_symlink() and target.read_text() == "keep"


def test_invalid_marker_is_repaired_without_losing_current_records(tmp_path, clock):
    path = tmp_path / "fetch.log"
    path.write_text("existing\n")
    os.utime(path, (clock[0], clock[0]))
    (tmp_path / "fetch.log.lock").write_text("broken")
    log, handler = _make_logger(path)
    try:
        log.info("new")
        assert path.read_text().splitlines() == ["existing", "new"]
        assert (tmp_path / "fetch.log.lock").read_text() == "2026111110"
    finally:
        handler.close()


def test_log_api_reads_wait_for_rotation(tmp_path, clock, monkeypatch):
    monkeypatch.setattr("handler.logHandler.LOG_PATH", str(tmp_path))
    path = tmp_path / "fetch.log"
    log, handler = _make_logger(path)
    log.info("old")
    clock[0] += 3600
    renamed = threading.Event()
    continue_rotation = threading.Event()
    reader_done = threading.Event()
    results = []
    original_rename = hourly.os.rename

    def paused_rename(source, destination):
        original_rename(source, destination)
        renamed.set()
        assert continue_rotation.wait(5)

    monkeypatch.setattr(hourly.os, "rename", paused_rename)
    writer = threading.Thread(target=lambda: log.info("new"))

    def read_log():
        results.append(readRecentLines("fetch", 1))
        reader_done.set()

    reader = threading.Thread(target=read_log)
    try:
        writer.start()
        assert renamed.wait(5)
        reader.start()
        assert not reader_done.wait(0.1)
        continue_rotation.set()
        writer.join(5)
        reader.join(5)
        assert results == [(True, ["new"])]
        assert listLogFiles() == ["fetch"]
    finally:
        continue_rotation.set()
        writer.join(5)
        if reader.ident is not None:
            reader.join(5)
        handler.close()


@pytest.mark.parametrize("start_method", ["spawn", "fork"])
def test_multiprocess_rotation_keeps_every_record(tmp_path, clock, start_method):
    if start_method not in multiprocessing.get_all_start_methods():
        pytest.skip("process start method unavailable")
    path = tmp_path / "shared.log"
    log, handler = _make_logger(path)
    log.info("previous hour")
    clock[0] = datetime(2026, 11, 11, 11, 0).timestamp()
    context = multiprocessing.get_context(start_method)
    barrier = context.Barrier(4)
    inherited = (log, handler) if start_method == "fork" else None
    processes = [context.Process(target=_write_worker,
        args=(str(path), clock[0], barrier, "child" + str(index), inherited))
        for index in range(3)]
    try:
        for process in processes:
            process.start()
        barrier.wait(timeout=15)
        for number in range(80):
            log.info("parent-%s", number)
        for process in processes:
            process.join(20)
            assert not process.is_alive()
            assert process.exitcode == 0
        assert (tmp_path / "shared.log.2026111110").read_text() == "previous hour\n"
        records = path.read_text().splitlines()
        expected = {prefix + "-" + str(number)
            for prefix in ("parent", "child0", "child1", "child2") for number in range(80)}
        assert len(records) == 320
        assert set(records) == expected
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            if process.pid is not None:
                process.join(5)
        handler.close()
