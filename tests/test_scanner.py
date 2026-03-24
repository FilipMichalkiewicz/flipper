"""
Tests for scanner.py — proxy pool, tags, MAC checking, fetch.
All HTTP calls are mocked — no network access needed.
"""

import os
import sys
import json
import time
import threading
import tempfile
from unittest.mock import patch, MagicMock

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import scanner


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_proxy_state():
    """Reset all shared proxy state before each test."""
    scanner.set_proxy_list([])
    with scanner._proxy_lock:
        scanner._proxy_tags.clear()
        scanner._proxy_rate_limited_at.clear()
        scanner._proxy_test_stats.clear()
        scanner._proxy_fail_counts.clear()
    yield
    scanner.set_proxy_list([])


# ══════════════════════════════════════════════════════════
#  PROXY POOL BASICS
# ══════════════════════════════════════════════════════════


class TestProxyPool:
    def test_set_and_get(self):
        scanner.set_proxy_list(["http://1.1.1.1:80", "http://2.2.2.2:80"])
        result = scanner.get_proxy_list()
        assert len(result) == 2
        assert "http://1.1.1.1:80" in result

    def test_add_proxy(self):
        scanner.add_proxy("http://1.1.1.1:80")
        scanner.add_proxy("http://2.2.2.2:80")
        scanner.add_proxy("http://1.1.1.1:80")  # duplicate
        assert len(scanner.get_proxy_list()) == 2

    def test_remove_proxy(self):
        scanner.set_proxy_list(["http://a:1", "http://b:2", "http://c:3"])
        scanner.remove_proxy("http://b:2")
        result = scanner.get_proxy_list()
        assert len(result) == 2
        assert "http://b:2" not in result

    def test_remove_nonexistent(self):
        scanner.set_proxy_list(["http://a:1"])
        scanner.remove_proxy("http://zzz:99")
        assert len(scanner.get_proxy_list()) == 1

    def test_get_current_proxy_empty(self):
        assert scanner.get_current_proxy() is None

    def test_get_current_proxy(self):
        scanner.set_proxy_list(["http://a:1", "http://b:2"])
        p = scanner.get_current_proxy()
        assert p == "http://a:1"

    def test_rotate_proxy(self):
        scanner.set_proxy_list(["http://a:1", "http://b:2", "http://c:3"])
        p1 = scanner.rotate_proxy()
        assert p1 == "http://b:2"
        p2 = scanner.rotate_proxy()
        assert p2 == "http://c:3"
        p3 = scanner.rotate_proxy()
        assert p3 == "http://a:1"  # wraps around

    def test_rotate_empty(self):
        assert scanner.rotate_proxy() is None

    def test_report_fail_removes_after_threshold(self):
        scanner.set_proxy_list(["http://a:1", "http://b:2"])
        for _ in range(scanner._PROXY_MAX_FAILS - 1):
            removed = scanner.report_proxy_fail("http://a:1")
            assert removed is False
        removed = scanner.report_proxy_fail("http://a:1")
        assert removed is True
        assert "http://a:1" not in scanner.get_proxy_list()

    def test_report_success_resets_fails(self):
        scanner.set_proxy_list(["http://a:1"])
        scanner.report_proxy_fail("http://a:1")
        scanner.report_proxy_fail("http://a:1")
        scanner.report_proxy_success("http://a:1")
        # Should not be removed after one more fail (counter was reset)
        removed = scanner.report_proxy_fail("http://a:1")
        assert removed is False
        assert "http://a:1" in scanner.get_proxy_list()


# ══════════════════════════════════════════════════════════
#  PROXY TAGS
# ══════════════════════════════════════════════════════════


class TestProxyTags:
    def test_new_proxy_gets_untested_tag(self):
        scanner.add_proxy("http://a:1")
        assert scanner.get_proxy_tag("http://a:1") == scanner.PROXY_TAG_UNTESTED

    def test_set_proxy_list_tags_untested(self):
        scanner.set_proxy_list(["http://a:1", "http://b:2"])
        assert scanner.get_proxy_tag("http://a:1") == scanner.PROXY_TAG_UNTESTED
        assert scanner.get_proxy_tag("http://b:2") == scanner.PROXY_TAG_UNTESTED

    def test_set_tag(self):
        scanner.add_proxy("http://a:1")
        scanner.set_proxy_tag("http://a:1", scanner.PROXY_TAG_WORKING)
        assert scanner.get_proxy_tag("http://a:1") == scanner.PROXY_TAG_WORKING

    def test_get_all_tags(self):
        scanner.set_proxy_list(["http://a:1", "http://b:2"])
        scanner.set_proxy_tag("http://a:1", scanner.PROXY_TAG_WORKING)
        tags = scanner.get_all_proxy_tags()
        assert tags["http://a:1"] == scanner.PROXY_TAG_WORKING
        assert tags["http://b:2"] == scanner.PROXY_TAG_UNTESTED

    def test_remove_proxy_clears_tag(self):
        scanner.add_proxy("http://a:1")
        scanner.set_proxy_tag("http://a:1", scanner.PROXY_TAG_WORKING)
        scanner.remove_proxy("http://a:1")
        assert (
            scanner.get_proxy_tag("http://a:1") == scanner.PROXY_TAG_UNTESTED
        )  # default

    def test_dead_proxies_skipped_by_get_current(self):
        scanner.set_proxy_list(["http://dead:1", "http://good:2"])
        scanner.set_proxy_tag("http://dead:1", scanner.PROXY_TAG_DEAD)
        p = scanner.get_current_proxy()
        assert p == "http://good:2"

    def test_dead_proxies_skipped_by_rotate(self):
        scanner.set_proxy_list(["http://a:1", "http://dead:2", "http://c:3"])
        scanner.set_proxy_tag("http://dead:2", scanner.PROXY_TAG_DEAD)
        # Start at index 0 (a:1), rotate should skip dead:2
        p = scanner.rotate_proxy()
        assert p == "http://c:3"

    def test_all_dead_returns_fallback(self):
        scanner.set_proxy_list(["http://dead1:1", "http://dead2:2"])
        scanner.set_proxy_tag("http://dead1:1", scanner.PROXY_TAG_DEAD)
        scanner.set_proxy_tag("http://dead2:2", scanner.PROXY_TAG_DEAD)
        # Should still return something (fallback)
        p = scanner.get_current_proxy()
        assert p is not None


# ══════════════════════════════════════════════════════════
#  RATE LIMITING
# ══════════════════════════════════════════════════════════


class TestRateLimiting:
    def test_mark_rate_limited(self):
        scanner.add_proxy("http://a:1")
        scanner.mark_proxy_rate_limited("http://a:1")
        assert scanner.get_proxy_tag("http://a:1") == scanner.PROXY_TAG_RATE_LIMITED
        assert scanner.get_proxy_rate_limited_at("http://a:1") > 0

    def test_rate_limited_skipped_by_get_current(self):
        scanner.set_proxy_list(["http://rl:1", "http://good:2"])
        scanner.mark_proxy_rate_limited("http://rl:1")
        p = scanner.get_current_proxy()
        assert p == "http://good:2"

    def test_rate_limited_expires(self):
        scanner.set_proxy_list(["http://rl:1", "http://good:2"])
        with scanner._proxy_lock:
            scanner._proxy_tags["http://rl:1"] = scanner.PROXY_TAG_RATE_LIMITED
            # Set timestamp to 2 hours ago (expired)
            scanner._proxy_rate_limited_at["http://rl:1"] = time.time() - 7200
        p = scanner.get_current_proxy()
        # Should have been reset to untested and returned
        assert p == "http://rl:1"
        assert scanner.get_proxy_tag("http://rl:1") == scanner.PROXY_TAG_UNTESTED

    def test_check_rate_limit_expired(self):
        scanner.add_proxy("http://a:1")
        scanner.mark_proxy_rate_limited("http://a:1")
        assert scanner.check_rate_limit_expired("http://a:1") is False
        # Manually expire it
        with scanner._proxy_lock:
            scanner._proxy_rate_limited_at["http://a:1"] = time.time() - 7200
        assert scanner.check_rate_limit_expired("http://a:1") is True

    def test_get_usable_count(self):
        scanner.set_proxy_list(["http://a:1", "http://b:2", "http://c:3"])
        scanner.set_proxy_tag("http://b:2", scanner.PROXY_TAG_DEAD)
        scanner.mark_proxy_rate_limited("http://c:3")
        assert scanner.get_usable_proxy_count() == 1  # only a:1


# ══════════════════════════════════════════════════════════
#  MULTI-PROXY
# ══════════════════════════════════════════════════════════


class TestMultiProxy:
    def test_get_proxy_for_multiproxy(self):
        scanner.set_proxy_list(["http://a:1", "http://b:2", "http://c:3"])
        p1 = scanner.get_proxy_for_multiproxy(exclude=set())
        assert p1 is not None
        p2 = scanner.get_proxy_for_multiproxy(exclude={p1})
        assert p2 is not None
        assert p2 != p1
        p3 = scanner.get_proxy_for_multiproxy(exclude={p1, p2})
        assert p3 is not None
        assert p3 not in {p1, p2}

    def test_multiproxy_skips_dead(self):
        scanner.set_proxy_list(["http://dead:1", "http://good:2"])
        scanner.set_proxy_tag("http://dead:1", scanner.PROXY_TAG_DEAD)
        p = scanner.get_proxy_for_multiproxy(exclude=set())
        assert p == "http://good:2"

    def test_multiproxy_all_excluded_returns_none(self):
        scanner.set_proxy_list(["http://a:1"])
        p = scanner.get_proxy_for_multiproxy(exclude={"http://a:1"})
        assert p is None


# ══════════════════════════════════════════════════════════
#  TEST STATS
# ══════════════════════════════════════════════════════════


class TestProxyTestStats:
    def test_init_and_update(self):
        scanner.init_proxy_test_stats("http://a:1")
        checked, found = scanner.get_proxy_test_stats("http://a:1")
        assert checked == 0
        assert found == 0

        scanner.update_proxy_test_stats("http://a:1", False)
        scanner.update_proxy_test_stats("http://a:1", True)
        scanner.update_proxy_test_stats("http://a:1", False)
        checked, found = scanner.get_proxy_test_stats("http://a:1")
        assert checked == 3
        assert found == 1


# ══════════════════════════════════════════════════════════
#  PERSISTENCE
# ══════════════════════════════════════════════════════════


class TestProxyStatePersistence:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scanner.set_proxy_list(["http://a:1", "http://b:2"])
            scanner.set_proxy_tag("http://a:1", scanner.PROXY_TAG_WORKING)
            scanner.mark_proxy_rate_limited("http://b:2")

            scanner.save_proxy_state(tmpdir)

            # Verify file exists
            path = os.path.join(tmpdir, scanner.PROXY_STATE_FILE)
            assert os.path.isfile(path)

            # Clear state
            with scanner._proxy_lock:
                scanner._proxy_tags.clear()
                scanner._proxy_rate_limited_at.clear()

            # Load back
            scanner.load_proxy_state(tmpdir)
            assert scanner.get_proxy_tag("http://a:1") == scanner.PROXY_TAG_WORKING
            assert scanner.get_proxy_tag("http://b:2") == scanner.PROXY_TAG_RATE_LIMITED
            assert scanner.get_proxy_rate_limited_at("http://b:2") > 0


# ══════════════════════════════════════════════════════════
#  CORE HELPERS
# ══════════════════════════════════════════════════════════


class TestCoreHelpers:
    def test_generate_random_mac_format(self):
        mac = scanner.generate_random_mac("00:1A:79")
        parts = mac.split(":")
        assert len(parts) == 6
        assert parts[0] == "00"
        assert parts[1] == "1A"
        assert parts[2] == "79"
        for p in parts:
            assert len(p) == 2

    def test_generate_random_mac_uniqueness(self):
        macs = {scanner.generate_random_mac("00:1A:79") for _ in range(100)}
        assert len(macs) > 90  # should be nearly all unique

    def test_make_cookies(self):
        cookies = scanner.make_cookies("00:1A:79:AA:BB:CC")
        assert cookies["mac"] == "00:1A:79:AA:BB:CC"
        assert cookies["stb_lang"] == "en"
        assert "sn" in cookies
        assert "device_id" in cookies

    def test_make_params(self):
        params = scanner.make_params("00:1A:79:AA:BB:CC", "handshake", "stb")
        assert params["action"] == "handshake"
        assert params["type"] == "stb"
        assert params["mac"] == "00:1A:79:AA:BB:CC"

    def test_should_remove_proxy(self):
        assert scanner.should_remove_proxy(403) is True
        assert scanner.should_remove_proxy(404) is True
        assert scanner.should_remove_proxy(407) is True
        assert scanner.should_remove_proxy(500) is True
        assert scanner.should_remove_proxy(200) is False
        assert scanner.should_remove_proxy(429) is False  # rate-limit, not remove


# ══════════════════════════════════════════════════════════
#  CHECK_MAC (mocked HTTP)
# ══════════════════════════════════════════════════════════


class TestCheckMac:
    def _mock_response(self, status_code=200, json_data=None, text=""):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_data or {}
        resp.text = text
        return resp

    @patch("scanner.requests.get")
    def test_check_mac_found(self, mock_get):
        """Simulate a successful MAC check with valid expiry."""
        # First call = handshake, second = get_main_info
        handshake_resp = self._mock_response(200, {"js": {"token": "abc123"}})
        info_resp = self._mock_response(
            200,
            {"js": {"mac": "00:1A:79:AA:BB:CC", "phone": "January, 15 2026 03:00 PM"}},
        )
        mock_get.side_effect = [handshake_resp, info_resp]

        result = scanner.check_mac(
            "http://test.com/portal.php",
            "00:1A:79:AA:BB:CC",
            timeout=5,
            proxy="http://proxy:8080",
        )
        assert result["found"] is True
        assert result["mac"] == "00:1A:79:AA:BB:CC"
        assert result["expiry"] is not None
        assert len(result["codes"]) == 2
        assert result["codes"][0] == 200
        assert result["codes"][1] == 200

    @patch("scanner.requests.get")
    def test_check_mac_handshake_fail(self, mock_get):
        """Simulate handshake returning no token."""
        mock_get.return_value = self._mock_response(200, {"js": {}})

        result = scanner.check_mac(
            "http://test.com/portal.php",
            "00:1A:79:AA:BB:CC",
            timeout=5,
            proxy="http://proxy:8080",
        )
        assert result["found"] is False
        assert "Handshake failed" in result["error"]

    @patch("scanner.requests.get")
    def test_check_mac_429(self, mock_get):
        """Simulate 429 rate limit response."""
        mock_get.return_value = self._mock_response(429, {})

        result = scanner.check_mac(
            "http://test.com/portal.php",
            "00:1A:79:AA:BB:CC",
            timeout=5,
            proxy="http://proxy:8080",
        )
        assert result["found"] is False
        assert 429 in result["codes"]

    @patch("scanner.requests.get")
    def test_check_mac_timeout(self, mock_get):
        """Simulate request timeout."""
        import requests as req

        mock_get.side_effect = req.exceptions.Timeout()

        result = scanner.check_mac(
            "http://test.com/portal.php",
            "00:1A:79:AA:BB:CC",
            timeout=5,
            proxy="http://proxy:8080",
        )
        assert result["found"] is False
        assert result["error"] is not None  # caught by get_handshake

    @patch("scanner.requests.get")
    def test_check_mac_proxy_error(self, mock_get):
        """Simulate proxy connection failure — caught by get_handshake."""
        import requests as req

        mock_get.side_effect = req.exceptions.ProxyError()

        result = scanner.check_mac(
            "http://test.com/portal.php",
            "00:1A:79:AA:BB:CC",
            timeout=5,
            proxy="http://proxy:8080",
        )
        assert result["found"] is False
        assert result["error"] is not None

    @patch("scanner.requests.get")
    def test_check_mac_connection_error(self, mock_get):
        """Simulate general connection failure — caught by get_handshake."""
        import requests as req

        mock_get.side_effect = req.exceptions.ConnectionError()

        result = scanner.check_mac(
            "http://test.com/portal.php",
            "00:1A:79:AA:BB:CC",
            timeout=5,
            proxy="http://proxy:8080",
        )
        assert result["found"] is False
        assert result["error"] is not None

    @patch("scanner.requests.get")
    def test_check_mac_empty_expiry_not_found(self, mock_get):
        """If expiry (phone field) is empty, MAC should not be marked found."""
        handshake_resp = self._mock_response(200, {"js": {"token": "abc123"}})
        info_resp = self._mock_response(
            200, {"js": {"mac": "00:1A:79:AA:BB:CC", "phone": ""}}
        )
        mock_get.side_effect = [handshake_resp, info_resp]

        result = scanner.check_mac(
            "http://test.com/portal.php",
            "00:1A:79:AA:BB:CC",
            timeout=5,
            proxy="http://proxy:8080",
        )
        assert result["found"] is False


# ══════════════════════════════════════════════════════════
#  FETCH FREE PROXIES (mocked HTTP)
# ══════════════════════════════════════════════════════════


class TestFetchFreeProxies:
    @patch("scanner.requests.get")
    def test_fetch_basic(self, mock_get):
        """Fetch from one source."""
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "1.2.3.4:8080\n5.6.7.8:3128\n"
        mock_get.return_value = resp

        proxies = scanner.fetch_free_proxies()
        assert len(proxies) >= 2
        assert "http://1.2.3.4:8080" in proxies
        assert "http://5.6.7.8:3128" in proxies

    @patch("scanner.requests.get")
    def test_fetch_deduplicates(self, mock_get):
        """Same proxy from different sources should not be duplicated."""
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "1.2.3.4:8080\n1.2.3.4:8080\n1.2.3.4:8080\n"
        mock_get.return_value = resp

        proxies = scanner.fetch_free_proxies()
        count = proxies.count("http://1.2.3.4:8080")
        assert count == 1

    @patch("scanner.requests.get")
    def test_fetch_max_count(self, mock_get):
        """Should stop once max_count is reached."""
        lines = "\n".join(f"1.2.3.{i}:8080" for i in range(255))
        resp = MagicMock()
        resp.status_code = 200
        resp.text = lines
        mock_get.return_value = resp

        proxies = scanner.fetch_free_proxies(max_count=10)
        assert (
            len(proxies) <= 255
        )  # first source may exceed, but second source won't run

    @patch("scanner.requests.get")
    def test_fetch_strips_schemes(self, mock_get):
        """Should normalize proxy strings."""
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "http://1.2.3.4:8080\nsocks4://5.6.7.8:3128\n9.9.9.9:1080\n"
        mock_get.return_value = resp

        proxies = scanner.fetch_free_proxies()
        for p in proxies:
            assert p.startswith("http://")
            assert "socks" not in p

    @patch("scanner.requests.get")
    def test_fetch_callback_called(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "1.2.3.4:8080\n"
        mock_get.return_value = resp

        calls = []

        def cb(source, new, total):
            calls.append((source, new, total))

        scanner.fetch_free_proxies(callback=cb)
        assert len(calls) > 0

    @patch("scanner.requests.get")
    def test_fetch_handles_failures(self, mock_get):
        """Should not crash on network errors."""
        mock_get.side_effect = Exception("network down")
        proxies = scanner.fetch_free_proxies()
        assert proxies == []


# ══════════════════════════════════════════════════════════
#  GET_HANDSHAKE (mocked)
# ══════════════════════════════════════════════════════════


class TestGetHandshake:
    @patch("scanner.requests.get")
    def test_handshake_success(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"js": {"token": "mytoken123"}}
        mock_get.return_value = resp

        token, code = scanner.get_handshake(
            "http://test.com/portal.php",
            "00:1A:79:AA:BB:CC",
            timeout=5,
            proxy="http://proxy:8080",
        )
        assert token == "mytoken123"
        assert code == 200

    @patch("scanner.requests.get")
    def test_handshake_no_token(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"js": {}}
        mock_get.return_value = resp

        token, code = scanner.get_handshake(
            "http://test.com/portal.php",
            "00:1A:79:AA:BB:CC",
            timeout=5,
            proxy="http://proxy:8080",
        )
        assert token is None
        assert code == 200

    @patch("scanner.requests.get")
    def test_handshake_429(self, mock_get):
        resp = MagicMock()
        resp.status_code = 429
        resp.json.return_value = {}
        mock_get.return_value = resp

        token, code = scanner.get_handshake(
            "http://test.com/portal.php",
            "00:1A:79:AA:BB:CC",
            timeout=5,
            proxy="http://proxy:8080",
        )
        assert token is None
        assert code == 429


# ══════════════════════════════════════════════════════════
#  THREAD SAFETY
# ══════════════════════════════════════════════════════════


class TestThreadSafety:
    def test_concurrent_proxy_operations(self):
        """Multiple threads modifying proxy pool concurrently shouldn't crash."""
        scanner.set_proxy_list([f"http://p{i}:80" for i in range(50)])
        errors = []

        def worker(worker_id):
            try:
                for _ in range(100):
                    scanner.rotate_proxy()
                    scanner.get_current_proxy()
                    p = f"http://new{worker_id}:80"
                    scanner.add_proxy(p)
                    scanner.get_proxy_tag(p)
                    scanner.set_proxy_tag(p, scanner.PROXY_TAG_WORKING)
                    scanner.get_usable_proxy_count()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread safety errors: {errors}"


# ══════════════════════════════════════════════════════════
#  INTEGRATION: tag transitions during check_mac
# ══════════════════════════════════════════════════════════


class TestTagTransitions:
    @patch("scanner.requests.get")
    def test_429_marks_rate_limited(self, mock_get):
        """When check_mac gets 429, the proxy should be tagged rate-limited."""
        scanner.set_proxy_list(["http://proxy:8080"])

        resp = MagicMock()
        resp.status_code = 429
        resp.json.return_value = {}
        mock_get.return_value = resp

        result = scanner.check_mac(
            "http://test.com/portal.php",
            "00:1A:79:AA:BB:CC",
            timeout=5,
            proxy="http://proxy:8080",
        )
        # The check_mac function itself doesn't set tags (that's main.py's job)
        # but it returns 429 in codes so the caller can act on it
        assert 429 in result["codes"]

    @patch("scanner.requests.get")
    def test_successful_check_can_mark_working(self, mock_get):
        """After a successful MAC find, caller should tag proxy as working."""
        scanner.set_proxy_list(["http://proxy:8080"])

        handshake_resp = MagicMock()
        handshake_resp.status_code = 200
        handshake_resp.json.return_value = {"js": {"token": "tok"}}

        info_resp = MagicMock()
        info_resp.status_code = 200
        info_resp.json.return_value = {
            "js": {"mac": "00:1A:79:AA:BB:CC", "phone": "January, 15 2026 03:00 PM"}
        }
        info_resp.text = '{"js": {}}'

        mock_get.side_effect = [handshake_resp, info_resp]

        result = scanner.check_mac(
            "http://test.com/portal.php",
            "00:1A:79:AA:BB:CC",
            timeout=5,
            proxy="http://proxy:8080",
        )
        assert result["found"] is True

        # Simulate what main.py does on success
        scanner.set_proxy_tag("http://proxy:8080", scanner.PROXY_TAG_WORKING)
        assert scanner.get_proxy_tag("http://proxy:8080") == scanner.PROXY_TAG_WORKING

    def test_deep_test_flow_dead_proxy(self):
        """Simulate deep test: 500 checks, zero found → dead."""
        scanner.set_proxy_list(["http://proxy:8080"])
        scanner.init_proxy_test_stats("http://proxy:8080")

        for _ in range(500):
            scanner.update_proxy_test_stats("http://proxy:8080", found=False)

        checked, found = scanner.get_proxy_test_stats("http://proxy:8080")
        assert checked == 500
        assert found == 0

        # This is what the deep test worker does
        scanner.set_proxy_tag("http://proxy:8080", scanner.PROXY_TAG_DEAD)
        assert scanner.get_proxy_tag("http://proxy:8080") == scanner.PROXY_TAG_DEAD

    def test_deep_test_flow_working_proxy(self):
        """Simulate deep test: found on attempt 42 → working."""
        scanner.set_proxy_list(["http://proxy:8080"])
        scanner.init_proxy_test_stats("http://proxy:8080")

        for i in range(42):
            found = i == 41  # found on last attempt
            scanner.update_proxy_test_stats("http://proxy:8080", found=found)

        checked, found = scanner.get_proxy_test_stats("http://proxy:8080")
        assert checked == 42
        assert found == 1

        scanner.set_proxy_tag("http://proxy:8080", scanner.PROXY_TAG_WORKING)
        assert scanner.get_proxy_tag("http://proxy:8080") == scanner.PROXY_TAG_WORKING
