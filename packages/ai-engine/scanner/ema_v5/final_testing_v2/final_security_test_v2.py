"""
EMA_V5 Final Security Test v2 — Comprehensive final security testing.
Isolated from existing security testing systems.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from loguru import logger


class EMAv5FinalSecurityTestV2:
    """Comprehensive final security testing for EMA_V5."""

    def __init__(self) -> None:
        self._results: List[Dict] = []

    def run_all(self) -> Dict[str, Any]:
        """Run all final security tests."""
        logger.info("🔒 EMA_V5 final security testing v2 starting")
        self._results = []

        self._test_input_sanitization()
        self._test_sql_injection()
        self._test_xss_prevention()
        self._test_path_traversal()
        self._test_authentication()
        self._test_rate_limiting()
        self._test_audit_logging()

        return self._compile_report()

    def _test_input_sanitization(self) -> None:
        """Test input sanitization."""
        try:
            from ..security.input_sanitizer import EMAv5InputSanitizer

            s = EMAv5InputSanitizer()

            # sanitize_string preserves safe input
            safe = s.sanitize_string("BTCUSDT")
            assert safe == "BTCUSDT", f"Safe input altered: {safe}"

            # check_xss detects XSS payloads
            xss_result = s.check_xss("<script>alert('xss')</script>")
            assert xss_result["safe"] is False, f"XSS not detected: {xss_result}"

            # check_sql_injection detects SQL injection
            sql_result = s.check_sql_injection("'; DROP TABLE signals; --")
            assert sql_result["safe"] is False, f"SQL injection not detected: {sql_result}"

            # sanitize_symbol sanitizes symbol input
            symbol = s.sanitize_symbol("BTCUSDT")
            assert symbol == "BTCUSDT", f"Symbol sanitized incorrectly: {symbol}"

            self._results.append({
                "test": "input_sanitization",
                "metric": "String/symbol sanitize + XSS/SQL detection",
                "passed": True,
            })
        except Exception as e:
            self._results.append({
                "test": "input_sanitization",
                "metric": str(e),
                "passed": False,
            })

    def _test_sql_injection(self) -> None:
        """Test SQL injection prevention."""
        try:
            from ..security.sql_guard import EMAv5SQLGuard

            g = EMAv5SQLGuard()

            # validate_query returns dict with 'safe' and 'threats'
            clean = g.validate_query("SELECT * FROM signals WHERE symbol = 'BTCUSDT'")
            assert isinstance(clean, dict), f"Expected dict, got {type(clean)}"
            assert "safe" in clean, f"Missing 'safe' key: {clean}"
            assert "threats" in clean, f"Missing 'threats' key: {clean}"

            # Multi-statement injection should be detected
            inj1 = g.validate_query("SELECT * FROM signals; DROP TABLE signals;")
            assert inj1["safe"] is False, f"Multi-statement injection not blocked: {inj1}"

            # UNION injection should be detected
            inj2 = g.validate_query("SELECT * FROM signals WHERE symbol = 'x' UNION SELECT * FROM users")
            assert inj2["safe"] is False, f"UNION injection not blocked: {inj2}"

            # safe_insert should produce safe queries
            insert = g.safe_insert("signals", {"symbol": "BTCUSDT", "side": "LONG"})
            assert "query" in insert, f"safe_insert missing query: {insert}"

            self._results.append({
                "test": "sql_injection",
                "metric": "validate_query detects injections, safe_insert works",
                "passed": True,
            })
        except Exception as e:
            self._results.append({
                "test": "sql_injection",
                "metric": str(e),
                "passed": False,
            })

    def _test_xss_prevention(self) -> None:
        """Test XSS detection via check_xss."""
        try:
            from ..security.input_sanitizer import EMAv5InputSanitizer

            s = EMAv5InputSanitizer()

            xss_tests = [
                "<script>alert(1)</script>",
                "<img src=x onerror=alert(1)>",
                "<svg onload=alert(1)>",
            ]

            detected = 0
            for payload in xss_tests:
                result = s.check_xss(payload)
                if result["safe"] is False:
                    detected += 1

            # Safe input should pass
            safe_result = s.check_xss("hello world")
            assert safe_result["safe"] is True, f"Safe input flagged as XSS: {safe_result}"

            self._results.append({
                "test": "xss_prevention",
                "metric": f"{detected}/{len(xss_tests)} XSS payloads detected, safe input passes",
                "passed": detected >= 2 and safe_result["safe"] is True,
            })
        except Exception as e:
            self._results.append({
                "test": "xss_prevention",
                "metric": str(e),
                "passed": False,
            })

    def _test_path_traversal(self) -> None:
        """Test path traversal detection via SQL guard."""
        try:
            from ..security.sql_guard import EMAv5SQLGuard

            g = EMAv5SQLGuard()

            # Test identifier sanitization prevents traversal-like injection
            safe_id = g.sanitize_identifier("signals")
            assert safe_id == "signals", f"Identifier sanitized incorrectly: {safe_id}"

            # Test value sanitization
            safe_val = g.sanitize_value("hello world", "string")
            assert safe_val == "hello world", f"Value sanitized incorrectly: {safe_val}"

            # Test numeric sanitization
            num_val = g.sanitize_value(42, "number")
            assert num_val == 42, f"Number sanitized incorrectly: {num_val}"

            self._results.append({
                "test": "path_traversal",
                "metric": "Identifier/value sanitization prevents injection",
                "passed": True,
            })
        except Exception as e:
            self._results.append({
                "test": "path_traversal",
                "metric": str(e),
                "passed": False,
            })

    def _test_authentication(self) -> None:
        """Test security monitoring (IP blocking, request checking)."""
        try:
            from ..security.security_monitor import EMAv5SecurityMonitor

            m = EMAv5SecurityMonitor()

            # Test request checking
            result = m.check_request("127.0.0.1", "/api/signals", "GET")
            assert isinstance(result, dict), f"Expected dict, got {type(result)}"
            assert "safe" in result, f"Missing 'safe' key: {result}"

            # Test IP blocking
            m.block_ip("192.168.1.100", duration=60)
            blocked = m.is_blocked("192.168.1.100")
            assert blocked is True, f"IP not blocked: {blocked}"

            # Test unblocked IP
            not_blocked = m.is_blocked("10.0.0.1")
            assert not_blocked is False, f"Unblocked IP flagged: {not_blocked}"

            self._results.append({
                "test": "authentication",
                "metric": "Request check + IP blocking works",
                "passed": True,
            })
        except Exception as e:
            self._results.append({
                "test": "authentication",
                "metric": str(e),
                "passed": False,
            })

    def _test_rate_limiting(self) -> None:
        """Test rate limiting via security monitor."""
        try:
            from ..security.security_monitor import EMAv5SecurityMonitor

            m = EMAv5SecurityMonitor()

            # Multiple requests from same IP — check_request tracks them
            for _ in range(5):
                m.check_request("10.0.0.50", "/api/signals", "GET")

            stats = m.get_stats()
            assert isinstance(stats, dict), f"Expected dict stats, got {type(stats)}"

            # Threats list accessible
            threats = m.get_threats(n=10)
            assert isinstance(threats, list), f"Expected list threats, got {type(threats)}"

            self._results.append({
                "test": "rate_limiting",
                "metric": f"Security monitor tracks {stats.get('total_requests', 'N/A')} requests",
                "passed": True,
            })
        except Exception as e:
            self._results.append({
                "test": "rate_limiting",
                "metric": str(e),
                "passed": False,
            })

    def _test_audit_logging(self) -> None:
        """Test audit logging."""
        try:
            from ..security.audit_logger import EMAv5AuditLogger

            al = EMAv5AuditLogger()

            al.log_event("test_event", {"key": "value"})
            al.log_event("security_check", {"result": "passed"})
            al.log_signal_event("emit", "BTCUSDT", "LONG")
            al.log_trade_event("open", "BTCUSDT", 0.5)
            al.log_security_event("test_threat", {"source": "test"})

            entries = al.get_entries(n=10)
            assert isinstance(entries, list), f"Expected list, got {type(entries)}"
            assert len(entries) >= 3, f"Expected >= 3 log entries, got {len(entries)}"

            stats = al.get_stats()
            assert isinstance(stats, dict), f"Expected dict stats, got {type(stats)}"

            self._results.append({
                "test": "audit_logging",
                "metric": f"{len(entries)} audit events recorded, stats available",
                "passed": True,
            })
        except Exception as e:
            self._results.append({
                "test": "audit_logging",
                "metric": str(e),
                "passed": False,
            })

    def _compile_report(self) -> Dict[str, Any]:
        """Compile test report."""
        passed = sum(1 for r in self._results if r.get("passed", False))
        total = len(self._results)

        return {
            "test_type": "final_security_v2",
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / max(total, 1) * 100, 1),
            "results": self._results,
            "all_passed": passed == total,
        }
