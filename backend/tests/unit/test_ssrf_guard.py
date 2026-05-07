"""T-Gap 5: Unit tests for assert_safe_spoolman_url."""

import pytest

from backend.app.api.routes._spoolman_helpers import assert_safe_spoolman_url


class TestSsrfGuardAccepted:
    """URLs that must be accepted (normal Spoolman topologies)."""

    def test_localhost_http(self):
        assert_safe_spoolman_url("http://localhost:7912")

    def test_localhost_https(self):
        assert_safe_spoolman_url("https://localhost:7912")

    def test_loopback_ipv4(self):
        assert_safe_spoolman_url("http://127.0.0.1:7912")

    def test_rfc1918_192_168(self):
        assert_safe_spoolman_url("http://192.168.1.50:7912")

    def test_rfc1918_10_x(self):
        assert_safe_spoolman_url("http://10.0.0.5:7912")

    def test_rfc1918_172_16(self):
        assert_safe_spoolman_url("http://172.16.0.1:7912")

    def test_hostname_based(self):
        assert_safe_spoolman_url("http://spoolman.local:7912")

    def test_internal_dns(self):
        assert_safe_spoolman_url("http://internal.corp:7912")

    def test_https_with_path(self):
        assert_safe_spoolman_url("https://spoolman.example.com/api")


class TestSsrfGuardRejected:
    """URLs that must be rejected as SSRF-dangerous."""

    def test_cloud_metadata_169_254(self):
        with pytest.raises(ValueError, match="Spoolman URL"):
            assert_safe_spoolman_url("http://169.254.169.254/latest/meta-data/")

    def test_alibaba_cloud_metadata(self):
        with pytest.raises(ValueError):
            assert_safe_spoolman_url("http://100.100.100.200/latest/meta-data/")

    def test_multicast_ipv4(self):
        with pytest.raises(ValueError):
            assert_safe_spoolman_url("http://224.0.0.1:7912")

    def test_unspecified_ipv4(self):
        with pytest.raises(ValueError):
            assert_safe_spoolman_url("http://0.0.0.0:7912")

    def test_scheme_file(self):
        with pytest.raises(ValueError, match="http or https"):
            assert_safe_spoolman_url("file:///etc/passwd")

    def test_scheme_gopher(self):
        with pytest.raises(ValueError, match="http or https"):
            assert_safe_spoolman_url("gopher://spoolman.local:7912")

    def test_scheme_dict(self):
        with pytest.raises(ValueError, match="http or https"):
            assert_safe_spoolman_url("dict://localhost:1234/")

    def test_numeric_encoded_decimal(self):
        # 2130706433 == 127.0.0.1 in decimal — must be rejected
        with pytest.raises(ValueError):
            assert_safe_spoolman_url("http://2130706433:7912")

    def test_numeric_encoded_hex(self):
        # 0x7f000001 == 127.0.0.1 in hex — must be rejected
        with pytest.raises(ValueError):
            assert_safe_spoolman_url("http://0x7f000001:7912")

    def test_ipv4_mapped_ipv6_metadata(self):
        # ::ffff:169.254.169.254 — IPv4-mapped IPv6 bypass attempt
        with pytest.raises(ValueError):
            assert_safe_spoolman_url("http://[::ffff:169.254.169.254]:7912")

    def test_multicast_ipv6(self):
        with pytest.raises(ValueError):
            assert_safe_spoolman_url("http://[ff02::1]:7912")

    def test_unspecified_ipv6(self):
        with pytest.raises(ValueError):
            assert_safe_spoolman_url("http://[::]:7912")

    def test_aws_imds_ipv6_blocked(self):
        """F10: AWS IMDS IPv6 address fd00:ec2::254 must be blocked."""
        with pytest.raises(ValueError):
            assert_safe_spoolman_url("http://[fd00:ec2::254]:7912")
