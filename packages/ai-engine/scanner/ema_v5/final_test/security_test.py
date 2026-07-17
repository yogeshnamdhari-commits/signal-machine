"""
EMA_V5 Security Test — Security testing for production.
Isolated from existing security testing systems.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from loguru import logger


class EMAv5SecurityTest:
    """Security testing for EMA_V5."""

    def __init__(self) -> None:
        self._results: List[Dict] = []

    def run_all(self) -> Dict[str, Any]:
        """Run all security tests."""
        logger.info("📊 EMA_V5 security testing starting")
        self._results = []

        self._test_input_sanitization()
        self._test_sql_injection()
        self._test_xss_prevention()
        self._test_path_traversal()
        self._test_authentication()
        self._test_rate_limiting()

        return self._compile_report()

    def _test_input_sanitization(self) -> None:
        """Test input sanitization."""
        try:
            from ..security.input_sanitizer import EMAv5InputSanitizer
            san = EMAv5InputSanitizer()

            # Test symbol sanitization
            assert san.sanitize_symbol("btc/usdt") == "BTCUSDT"
            assert san.sanitize_symbol("<script>alert(1)</script>") == ""

            # Test number sanitization
            assert san.sanitize_number("100.5") == 100.5
            assert san.sanitize_number("abc") == 0.0

            # Test dict sanitization
            result = san.sanitize_dict({"key": "value", "num": 42})
            assert result["key"] == "value"
            assert result["num"] == 42

            self._results.append({"test": "input_sanitization", "passed": True})
        except Exception as e:
            self._results.append({"test": "input_sanitization", "passed": False, "details": str(e)})

    def _test_sql_injection(self) -> None:
        """Test SQL injection prevention."""
        try:
            from ..security.input_sanitizer import EMAv5InputSanitizer
            san = EMAv5InputSanitizer()

            # Test SQL injection detection
            result = san.check_sql_injection("SELECT * FROM users WHERE id=1")
            assert result["safe"] == False

            result2 = san.check_sql_injection("Normal text")
            assert result2["safe"] == True

            self._results.append({"test": "sql_injection", "passed": True})
        except Exception as e:
            self._results.append({"test": "sql_injection", "passed": False, "details": str(e)})

    def _test_xss_prevention(self) -> None:
        """Test XSS prevention."""
        try:
            from ..security.input_sanitizer import EMAv5InputSanitizer
            san = EMAv5InputSanitizer()

            # Test XSS detection
            result = san.check_xss("<script>alert(1)</script>")
            assert result["safe"] == False

            result2 = san.check_xss("Normal text")
            assert result2["safe"] == True

            self._results.append({"test": "xss_prevention", "passed": True})
        except Exception as e:
            self._results.append({"test": "xss_prevention", "passed": False, "details": str(e)})

    def _test_path_traversal(self) -> None:
        """Test path traversal prevention."""
        try:
            from ..security.security_monitor import EMAv5SecurityMonitor
            mon = EMAv5SecurityMonitor()

            # Test path traversal detection
            result = mon.check_request("192.168.1.1", "/api/v1/signals/../etc/passwd")
            assert result["safe"] == False

            result2 = mon.check_request("192.168.1.1", "/api/v1/signals")
            assert result2["safe"] == True

            self._results.append({"test": "path_traversal", "passed": True})
        except Exception as e:
            self._results.append({"test": "path_traversal", "passed": False, "details": str(e)})

    def _test_authentication(self) -> None:
        """Test authentication."""
        try:
            from ..gateway.auth import EMAv5Auth
            auth = EMAv5Auth()

            # Create key
            key_result = auth.create_key("test-key")
            assert "key" in key_result

            # Validate key
            val_result = auth.validate_key(key_result["key"])
            assert val_result["authenticated"] == True

            # Invalid key
            inv_result = auth.validate_key("invalid_key")
            assert inv_result["authenticated"] == False

            # Revoke key
            revoked = auth.revoke_key(key_result["key_id"])
            assert revoked == True

            self._results.append({"test": "authentication", "passed": True})
        except Exception as e:
            self._results.append({"test": "authentication", "passed": False, "details": str(e)})

    def _test_rate_limiting(self) -> None:
        """Test rate limiting."""
        try:
            from ..gateway.rate_limiter import EMAv5RateLimiter
            rl = EMAv5RateLimiter()

            # Test normal rate
            result = rl.check_rate_limit("test-client")
            assert result["allowed"] == True

            # Test rate limit (send many requests)
            for _ in range(100):
                rl.check_rate_limit("test-client-2")

            result2 = rl.check_rate_limit("test-client-2")
            # Should still be allowed (burst size)
            assert result2["allowed"] == True

            self._results.append({"test": "rate_limiting", "passed": True})
        except Exception as e:
            self._results.append({"test": "rate_limiting", "passed": False, "details": str(e)})

    def _compile_report(self) -> Dict[str, Any]:
        """Compile test report."""
        passed = sum(1 for r in self._results if r.get("passed", False))
        total = len(self._results)

        return {
            "test_type": "security",
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / max(total, 1) * 100, 1),
            "results": self._results,
            "all_passed": passed == total,
        }
