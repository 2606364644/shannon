"""TOTP Generator CLI Tool — RFC 6238 compliant.

Generates Time-based One-Time Passwords using only the Python standard library.
"""

import argparse
import hashlib
import hmac
import json
import re
import struct
import sys
import time


def base32_decode(encoded: str) -> bytes:
    """Decode a base32-encoded string to bytes.

    Implements base32 decoding per RFC 4648 using only the standard library.
    Validates that input matches the base32 alphabet before decoding.

    Args:
        encoded: A base32-encoded string (case-insensitive).

    Returns:
        The decoded bytes.

    Raises:
        ValueError: If the input contains invalid characters.
    """
    # Normalize: uppercase and strip whitespace
    encoded = encoded.strip().upper()

    # Validate: only A-Z, 2-7, and = for padding
    if not re.fullmatch(r"[A-Z2-7]+=*", encoded):
        raise ValueError(
            f"Invalid base32 input: contains characters outside the base32 alphabet"
        )

    # Use the standard library base32 decoder
    import base64

    return base64.b32decode(encoded)


def generate_hotp(secret: str, counter: int, digits: int = 6) -> str:
    """Generate an HOTP code per RFC 4226.

    Args:
        secret: Base32-encoded shared secret.
        counter: The counter value (8-byte big-endian integer).
        digits: Number of digits in the output code (default 6).

    Returns:
        The HOTP code as a zero-padded string.

    Raises:
        ValueError: If the secret is invalid base32.
    """
    key = base32_decode(secret)

    # Encode counter as 8-byte big-endian unsigned integer
    counter_bytes = struct.pack(">Q", counter)

    # Compute HMAC-SHA1
    hmac_hash = hmac.new(key, counter_bytes, hashlib.sha1).digest()

    # Dynamic truncation (RFC 4226, Section 5.3)
    offset = hmac_hash[-1] & 0x0F
    truncated = (
        ((hmac_hash[offset] & 0x7F) << 24)
        | ((hmac_hash[offset + 1] & 0xFF) << 16)
        | ((hmac_hash[offset + 2] & 0xFF) << 8)
        | (hmac_hash[offset + 3] & 0xFF)
    )

    # Extract the rightmost `digits` digits
    code = truncated % (10**digits)

    # Zero-pad to the requested number of digits
    return str(code).zfill(digits)


def generate_totp(secret: str, time_step: int = 30, digits: int = 6) -> str:
    """Generate a TOTP code per RFC 6238.

    TOTP is defined as HOTP with a time-based counter:
        counter = floor(current_unix_time / time_step)

    Args:
        secret: Base32-encoded shared secret.
        time_step: Time step in seconds (default 30).
        digits: Number of digits in the output code (default 6).

    Returns:
        The TOTP code as a zero-padded string.

    Raises:
        ValueError: If the secret is invalid base32.
    """
    current_time = int(time.time())
    counter = current_time // time_step
    return generate_hotp(secret, counter, digits)


def main() -> None:
    """CLI entry point for TOTP generation.

    Parses --secret argument and outputs JSON to stdout:
      Success: {"status":"success","totpCode":"123456","expiresIn":<sec>}
      Error:   {"status":"error","message":"...","retryable":false}

    Exit codes: 0 for success, 1 for error.
    """
    parser = argparse.ArgumentParser(
        description="Generate a TOTP code from a base32 secret"
    )
    parser.add_argument(
        "--secret",
        required=True,
        help="Base32-encoded shared secret (e.g. JBSWY3DPEHPK3PXP)",
    )
    args = parser.parse_args()

    try:
        totp_code = generate_totp(args.secret)
        current_time = int(time.time())
        expires_in = 30 - (current_time % 30)

        output = {
            "status": "success",
            "totpCode": totp_code,
            "expiresIn": expires_in,
        }
        print(json.dumps(output))
        sys.exit(0)

    except ValueError as e:
        output = {
            "status": "error",
            "message": str(e),
            "retryable": False,
        }
        print(json.dumps(output), file=sys.stderr)
        sys.exit(1)

    except Exception as e:
        output = {
            "status": "error",
            "message": f"Unexpected error: {e}",
            "retryable": False,
        }
        print(json.dumps(output), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
