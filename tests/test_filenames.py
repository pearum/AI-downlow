"""Filename sanitization and duplicate-handling tests."""

from pathlib import Path

import pytest

from app.utils.filenames import (
    build_filename,
    make_unique_path,
    sanitize_filename,
)


class TestSanitize:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ('a<b>c:"d/e\\f|g?h*i', "abcdefghi"),
            ("normal title.mp4", "normal title.mp4"),
            ("  padded  ", "padded"),
            ("trailing dot....", "trailing dot"),
            ("trailing space   ", "trailing space"),
            ("", "Untitled"),
            ("   ", "Untitled"),
            ("CON", "CON"),  # reserved handled in build_filename, not here
            ("video\x00null", "videonull"),
            ("line\nbreak", "line break"),
        ],
    )
    def test_sanitize(self, raw, expected):
        assert sanitize_filename(raw) == expected

    def test_removes_illegal_chars_in_middle(self):
        assert "abc" in sanitize_filename("a<b>c")

    def test_reserved_names_in_build(self):
        result = build_filename(
            "{title}",
            title="CON",
            creator=None,
            index=None,
            date_str=None,
            ext="mp4",
        )
        assert result == "CON (file).mp4"


class TestUniquePaths:
    def test_no_duplicate(self, tmp_path):
        target = tmp_path / "video.mp4"
        assert make_unique_path(target) == target

    def test_first_duplicate(self, tmp_path):
        target = tmp_path / "video.mp4"
        target.write_bytes(b"x")
        assert make_unique_path(target) == tmp_path / "video (1).mp4"

    def test_second_duplicate(self, tmp_path):
        target = tmp_path / "video.mp4"
        target.write_bytes(b"x")
        (tmp_path / "video (1).mp4").write_bytes(b"x")
        assert make_unique_path(target) == tmp_path / "video (2).mp4"


class TestBuildFilename:
    def test_creator_title_template(self):
        name = build_filename(
            "{creator} - {title}",
            title="My Video",
            creator="Channel Name",
            index=3,
            date_str="2026-01-01",
            ext="mp4",
        )
        assert name == "003 - Channel Name - My Video.mp4"

    def test_title_only(self):
        name = build_filename(
            "{title}",
            title="Solo",
            creator=None,
            index=None,
            date_str=None,
            ext="mp4",
        )
        assert name == "Solo.mp4"

    def test_missing_creator_falls_back(self):
        name = build_filename(
            "{creator} - {title}",
            title="Video",
            creator="",
            index=None,
            date_str=None,
            ext="mp4",
        )
        assert name == "Video.mp4"

    def test_invalid_template_key_does_not_crash(self):
        name = build_filename(
            "{unknown_key}",
            title="Video",
            creator=None,
            index=None,
            date_str=None,
            ext="mp4",
        )
        assert isinstance(name, str) and name.endswith(".mp4")

    def test_date_template(self):
        name = build_filename(
            "{date} - {title}",
            title="News",
            creator=None,
            index=None,
            date_str="2026-08-17",
            ext="mp4",
        )
        assert name == "2026-08-17 - News.mp4"

    def test_ext_changes(self):
        name = build_filename(
            "{title}",
            title="Audio",
            creator=None,
            index=None,
            date_str=None,
            ext="mp3",
        )
        assert name == "Audio.mp3"

    def test_nested_path_is_flat(self):
        name = build_filename(
            "{title}",
            title="a/b/c",
            creator=None,
            index=None,
            date_str=None,
            ext="mp4",
        )
        assert "/" not in name and "\\" not in name
