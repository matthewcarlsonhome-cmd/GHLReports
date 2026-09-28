"""The read-only contract in collector/ghl_client.py: only GET and two exact
search POSTs may leave the collector (SECURITY-SPEC SEC-11)."""

import pytest

from ..ghl_client import ALLOWED_POST_PATTERNS, GHLClient, GHLError


def allowed(path):
    return any(pattern.fullmatch(path) for pattern in ALLOWED_POST_PATTERNS)


def test_the_two_search_posts_the_collector_uses_are_allowed():
    assert allowed("/contacts/search")
    assert allowed("/social-media-posting/loc123/posts/list")


@pytest.mark.parametrize("path", [
    "/social-media-posting/loc123/posts",          # creates a post
    "/social-media-posting/loc123/posts/list/x",
    "/contacts/search/duplicate",
    "/contacts/",                                   # creates a contact
    "/opportunities/search",
])
def test_every_other_post_is_refused_before_any_network_call(path):
    client = GHLClient("tok-test")
    with pytest.raises(GHLError, match="refusing POST"):
        client.request("POST", path, json_body={})
    assert client.requests_made == 0


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_write_methods_are_refused(method):
    with pytest.raises(GHLError, match="refusing non-read method"):
        GHLClient("tok-test").request(method, "/contacts/abc")
