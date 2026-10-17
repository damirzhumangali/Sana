"""Synthetic analyzers used only by automated tests; these do not analyze video."""

import os
import time


def no_violation(video_path):
    assert video_path.read_bytes() == b"synthetic video"
    return {"verdict": "no_violation", "violations": []}


def invalid_result(video_path):
    return {"verdict": "violation", "violations": []}


def raises_error(video_path):
    raise RuntimeError("private diagnostic information")


def slow(video_path):
    time.sleep(10)
    return {"verdict": "no_violation", "violations": []}


def crashes(video_path):
    os._exit(7)
