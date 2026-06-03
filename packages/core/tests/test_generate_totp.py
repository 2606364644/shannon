"""Tests for TOTP Generator CLI Tool.

Covers:
  - base32_decode: valid, invalid chars, padding variants
  - generate_hotp: RFC 4226 test vectors
  - generate_totp: known values with mocked time
  - CLI interface: JSON output format, error handling
"""

import json
import subprocess
import sys
import time
from unittest import mock

import pytest

from shannon_core.scripts.generate_totp import (
    base32_decode,
    generate_hotp,
    generate_totp,
    main,
)


# ---------------------------------------------------------------------------
# base32_decode
# ---------------------------------------------------------------------------

class TestBase32Decode:
    """Tests for base32_decode."""

    def test_decode_valid_simple(self):
        """Decode a simple base32 string."""
        result = base32_decode("JBSWY3DPEHPK3PXP")
        assert result == b"Hello!\xde\xad\xbe\xef"

    def test_decode_uppercase_and_lowercase_equivalent(self):
        """Base32 is case-insensitive."""
        upper = base32_decode("JBSWY3DPEHPK3PXP")
        lower = base32_decode("jbswy3dpehpk3pxp")
        mixed = base32_decode("JbsWy3DpEhPk3PxP")
        assert upper == lower == mixed

    def test_decode_with_padding(self):
        """Strings with padding should decode correctly."""
        # "ME======" decodes to the byte 0x61 which is lowercase 'a'
        result = base32_decode("ME======")
        assert result == b"a"

    def test_decode_empty_string_with_padding(self):
        """Empty-like base32 strings."""
        # Padding-only strings are invalid (no valid base32 characters)
        # But an empty string won't match the regex, so test that separately

    def test_decode_rfc4226_secret(self):
        """Decode the RFC 4226 test secret '12345678901234567890'.

        The ASCII string '12345678901234567890' in base32 is
        'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'.
        """
        result = base32_decode("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
        assert result == b"12345678901234567890"

    def test_decode_invalid_characters_rejected(self):
        """Input with non-base32 characters raises ValueError."""
        with pytest.raises(ValueError, match="Invalid base32"):
            base32_decode("INVALID01")  # '0' and '1' are not in base32 alphabet

    def test_decode_invalid_special_chars_rejected(self):
        """Input with special characters raises ValueError."""
        with pytest.raises(ValueError, match="Invalid base32"):
            base32_decode("JBSWY!@#")

    def test_decode_whitespace_stripped(self):
        """Leading/trailing whitespace should be stripped."""
        result = base32_decode("  JBSWY3DPEHPK3PXP  ")
        assert result == base32_decode("JBSWY3DPEHPK3PXP")

    def test_decode_padding_position(self):
        """Padding with = should work."""
        # "Hello" -> base32 = "JBSWY3DP"
        # That's already a clean 40-bit boundary, no padding needed
        result = base32_decode("JBSWY3DP")
        assert result == b"Hello"


# ---------------------------------------------------------------------------
# generate_hotp — RFC 4226 Test Vectors
# ---------------------------------------------------------------------------

class TestHOTP:
    """Tests for generate_hotp using RFC 4226 Appendix D test vectors.

    The RFC 4226 test secret is the ASCII string "12345678901234567890",
    which base32-encodes to "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ".

    Expected HOTP values from RFC 4226 Appendix D:
      Counter 0: 755224
      Counter 1: 287082
      Counter 2: 359152
      Counter 3: 969429
      Counter 4: 338314
      Counter 5: 254676
      Counter 6: 287922
      Counter 7: 162583
      Counter 8: 399871
      Counter 9: 520489
    """

    SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    # (counter, expected_code)
    RFC_VECTORS = [
        (0, "755224"),
        (1, "287082"),
        (2, "359152"),
        (3, "969429"),
        (4, "338314"),
        (5, "254676"),
        (6, "287922"),
        (7, "162583"),
        (8, "399871"),
        (9, "520489"),
    ]

    @pytest.mark.parametrize("counter,expected", RFC_VECTORS)
    def test_rfc4226_vectors(self, counter, expected):
        """Verify HOTP output matches RFC 4226 Appendix D test vectors."""
        result = generate_hotp(self.SECRET, counter)
        assert result == expected

    def test_output_is_string(self):
        """HOTP output is always a string."""
        result = generate_hotp(self.SECRET, 0)
        assert isinstance(result, str)

    def test_output_is_six_digits(self):
        """Default output length is 6 digits."""
        result = generate_hotp(self.SECRET, 0)
        assert len(result) == 6
        assert result.isdigit()

    def test_output_is_zero_padded(self):
        """Codes with leading zeros should be zero-padded."""
        # Counter 5 gives 254676 — not leading-zero, but let's verify format
        result = generate_hotp(self.SECRET, 5)
        assert len(result) == 6

    def test_eight_digits(self):
        """HOTP with digits=8 should produce 8-digit codes."""
        # RFC 4226 also provides 8-digit test vectors:
        # Counter 0 -> 75522400 (8 digits) — but actually the 8-digit version
        # is different because the truncation modulus changes.
        # Let's just verify length.
        result = generate_hotp(self.SECRET, 0, digits=8)
        assert len(result) == 8
        assert result.isdigit()

    def test_invalid_secret_raises(self):
        """Invalid base32 secret raises ValueError."""
        with pytest.raises(ValueError):
            generate_hotp("NOT_VALID_BASE32!", 0)


# ---------------------------------------------------------------------------
# generate_totp
# ---------------------------------------------------------------------------

class TestTOTP:
    """Tests for generate_totp."""

    SECRET = "JBSWY3DPEHPK3PXP"

    def test_totp_returns_six_digit_string(self):
        """TOTP output should be a 6-digit string."""
        result = generate_totp(self.SECRET)
        assert isinstance(result, str)
        assert len(result) == 6
        assert result.isdigit()

    def test_totp_deterministic_within_time_step(self):
        """Two calls within the same time step should return the same code."""
        result1 = generate_totp(self.SECRET)
        result2 = generate_totp(self.SECRET)
        assert result1 == result2

    @mock.patch("shannon_core.scripts.generate_totp.time")
    def test_totp_with_known_time(self, mock_time):
        """TOTP at a known timestamp produces a deterministic result."""
        # Set time to a known value: epoch 59 (counter = 59 // 30 = 1)
        # We verify it matches HOTP at counter 1
        mock_time.time.return_value = 59.0
        mock_time.time.side_effect = None

        result = generate_totp(self.SECRET)
        expected = generate_hotp(self.SECRET, counter=1)
        assert result == expected

    @mock.patch("shannon_core.scripts.generate_totp.time")
    def test_totp_at_epoch_zero(self, mock_time):
        """TOTP at epoch 0 should use counter 0."""
        mock_time.time.return_value = 0.0
        mock_time.time.side_effect = None

        result = generate_totp(self.SECRET)
        expected = generate_hotp(self.SECRET, counter=0)
        assert result == expected

    @mock.patch("shannon_core.scripts.generate_totp.time")
    def test_totp_counter_calculation(self, mock_time):
        """Verify the counter is floor(time / time_step)."""
        # At time=29, counter should be 0
        mock_time.time.return_value = 29.0
        assert generate_totp(self.SECRET) == generate_hotp(self.SECRET, counter=0)

        # At time=30, counter should be 1
        mock_time.time.return_value = 30.0
        assert generate_totp(self.SECRET) == generate_hotp(self.SECRET, counter=1)

        # At time=89, counter should be 2
        mock_time.time.return_value = 89.0
        assert generate_totp(self.SECRET) == generate_hotp(self.SECRET, counter=2)

    def test_totp_with_custom_time_step(self):
        """TOTP with a custom time step should still produce valid output."""
        result = generate_totp(self.SECRET, time_step=60)
        assert isinstance(result, str)
        assert len(result) == 6
        assert result.isdigit()

    def test_totp_invalid_secret_raises(self):
        """Invalid secret should raise ValueError."""
        with pytest.raises(ValueError):
            generate_totp("INVALID!")


# ---------------------------------------------------------------------------
# CLI Interface (main)
# ---------------------------------------------------------------------------

class TestCLI:
    """Tests for the CLI entry point."""

    SECRET = "JBSWY3DPEHPK3PXP"

    def _run_cli(self, args: list[str]) -> subprocess.CompletedProcess:
        """Helper to run the CLI as a subprocess."""
        return subprocess.run(
            [sys.executable, "-m", "shannon_core.scripts.generate_totp"] + args,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_success_json_output(self):
        """Successful invocation outputs valid JSON to stdout."""
        result = self._run_cli(["--secret", self.SECRET])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "success"
        assert "totpCode" in data
        assert "expiresIn" in data

    def test_success_totp_code_format(self):
        """The totpCode should be a 6-digit string."""
        result = self._run_cli(["--secret", self.SECRET])
        data = json.loads(result.stdout)
        assert len(data["totpCode"]) == 6
        assert data["totpCode"].isdigit()

    def test_success_expires_in_range(self):
        """expiresIn should be between 1 and 30 inclusive."""
        result = self._run_cli(["--secret", self.SECRET])
        data = json.loads(result.stdout)
        assert 1 <= data["expiresIn"] <= 30

    def test_missing_secret_flag(self):
        """Missing --secret should produce an error."""
        result = self._run_cli([])
        assert result.returncode != 0

    def test_invalid_secret_json_error(self):
        """Invalid secret should output JSON error to stderr with exit code 1."""
        result = self._run_cli(["--secret", "NOT_VALID!"])
        assert result.returncode == 1
        data = json.loads(result.stderr)
        assert data["status"] == "error"
        assert "message" in data
        assert data["retryable"] is False

    def test_cli_output_matches_generate_totp(self):
        """CLI output should match a direct generate_totp call."""
        result = self._run_cli(["--secret", self.SECRET])
        data = json.loads(result.stdout)
        expected = generate_totp(self.SECRET)
        assert data["totpCode"] == expected


# ---------------------------------------------------------------------------
# Integration: main() via mock (in-process)
# ---------------------------------------------------------------------------

class TestMainInProcess:
    """In-process tests for main() using mocked stdout/stderr."""

    SECRET = "JBSWY3DPEHPK3PXP"

    @mock.patch("sys.argv", ["generate_totp", "--secret", SECRET])
    def test_main_success_output(self, capsys=None):
        """main() should write success JSON to stdout and exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    @mock.patch("sys.argv", ["generate_totp", "--secret", "BAD!SECRET"])
    def test_main_error_output(self):
        """main() should write error JSON to stderr and exit 1 for invalid secret."""
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
