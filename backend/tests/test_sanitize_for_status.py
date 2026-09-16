"""F5: the status/log sanitisation line.

Keys and auth headers are never rendered to logs or the Floor; hostnames
and short opaque tokens (deploy ids, session ids) pass through untouched,
because an operator needs them to name what they are looking at.
"""

from __future__ import annotations

from app.factory.build.sanitize import sanitize_for_status


class TestSanitizeForStatus:
    def test_hostnames_survive(self):
        assert (
            sanitize_for_status("deploy https://api.render.com/v1/services/srv-x/deploys")
            == "deploy https://api.render.com/v1/services/srv-x/deploys"
        )

    def test_short_tokens_survive(self):
        assert (
            sanitize_for_status("receipt dep-dakod9jl550s73ev30j0 accepted")
            == "receipt dep-dakod9jl550s73ev30j0 accepted"
        )

    def test_bearer_headers_are_redacted(self):
        assert (
            sanitize_for_status("Authorization: Bearer abc123\nGET /v1/deploys")
            == "[redacted]\nGET /v1/deploys"
        )

    def test_api_key_headers_are_redacted(self):
        assert (
            sanitize_for_status("X-Api-Key: rnd_abcdefghijklmnopqrst")
            == "[redacted]"
        )

    def test_key_assignments_keep_the_name_and_drop_the_value(self):
        assert (
            sanitize_for_status("DEEPSEEK_API_KEY=sk-secretvalue1234")
            == "DEEPSEEK_API_KEY=[redacted]"
        )

    def test_provider_style_keys_are_redacted(self):
        assert (
            sanitize_for_status("failed with sk-abcdefghijklmnopqrst123456")
            == "failed with [redacted]"
        )

    def test_long_blob_secrets_are_redacted(self):
        blob = "x" * 64
        assert sanitize_for_status(f"sig {blob} rejected") == "sig [redacted] rejected"

    def test_none_and_non_strings_pass_through(self):
        assert sanitize_for_status(None) == ""
        assert sanitize_for_status("") == ""
