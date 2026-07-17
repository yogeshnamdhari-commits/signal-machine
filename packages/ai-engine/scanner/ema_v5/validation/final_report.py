"""
EMA_V5 Final Report — Comprehensive final report for the entire EMA_V5 system.
Isolated from existing reporting systems.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from loguru import logger

from .system_validator import EMAv5SystemValidator
from .production_readiness import EMAv5ProductionReadiness


class EMAv5FinalReport:
    """Generates the final comprehensive report for EMA_V5."""

    def __init__(self) -> None:
        self._validator = EMAv5SystemValidator()
        self._readiness = EMAv5ProductionReadiness()

    def generate(self) -> Dict[str, Any]:
        """Generate the final comprehensive report."""
        logger.info("📊 EMA_V5 final report generation")

        # Run all validations
        system_validation = self._validator.validate_all()
        production_readiness = self._readiness.check_all()

        # Get system stats
        system_stats = self._get_system_stats()

        return {
            "report_type": "final",
            "version": "1.0.0",
            "generated_at": time.time(),
            "generated_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "executive_summary": self._executive_summary(system_validation, production_readiness, system_stats),
            "system_validation": system_validation,
            "production_readiness": production_readiness,
            "system_stats": system_stats,
            "module_inventory": self._module_inventory(),
            "recommendations": self._get_all_recommendations(system_validation, production_readiness),
        }

    def _executive_summary(self, validation: Dict, readiness: Dict, stats: Dict) -> Dict[str, Any]:
        """Generate executive summary."""
        return {
            "status": "PRODUCTION READY" if readiness["ready"] else "NOT READY",
            "validation_passed": validation["passed"],
            "validation_failed": validation["failed"],
            "readiness_checks": readiness["total_checks"],
            "readiness_passed": readiness["passed"],
            "total_packages": stats["packages"],
            "total_files": stats["total_files"],
            "total_lines": stats["total_lines"],
            "phases_completed": 20,
        }

    def _get_system_stats(self) -> Dict[str, Any]:
        """Get system statistics."""
        import os
        from pathlib import Path

        # Count packages
        packages = []
        for item in Path("scanner/ema_v5").iterdir():
            if item.is_dir() and not item.name.startswith("__"):
                packages.append(item.name)

        # Count Python files
        py_files = list(Path("scanner/ema_v5").rglob("*.py"))

        # Count total lines
        total_lines = 0
        for f in py_files:
            try:
                with open(f) as fh:
                    total_lines += sum(1 for _ in fh)
            except Exception:
                pass

        return {
            "packages": len(packages),
            "package_list": sorted(packages),
            "total_files": len(py_files),
            "total_lines": total_lines,
        }

    def _module_inventory(self) -> Dict[str, Any]:
        """Get complete module inventory."""
        import os
        from pathlib import Path

        inventory = {}
        for item in Path("scanner/ema_v5").iterdir():
            if item.is_dir() and not item.name.startswith("__"):
                py_files = list(item.glob("*.py"))
                inventory[item.name] = {
                    "files": len(py_files),
                    "file_list": [f.name for f in py_files],
                }

        # Add standalone modules
        standalone = ["scanner.py", "signal_engine.py", "state_manager.py",
                     "config.py", "cache.py", "utils.py", "trade_manager.py",
                     "regime_engine.py", "trend_engine.py", "pullback_engine.py",
                     "candle_engine.py", "volume_engine.py", "confidence_engine.py"]
        inventory["core"] = {
            "files": len([f for f in standalone if Path(f"scanner/ema_v5/{f}").exists()]),
            "file_list": [f for f in standalone if Path(f"scanner/ema_v5/{f}").exists()],
        }

        return inventory

    def _get_all_recommendations(self, validation: Dict, readiness: Dict) -> List[str]:
        """Get all recommendations."""
        recommendations = []

        # From validation
        for result in validation.get("results", []):
            if not result["passed"]:
                recommendations.append(f"Fix: {result['details']}")

        # From readiness
        recommendations.extend(readiness.get("recommendations", []))

        # General recommendations
        if not recommendations:
            recommendations.append("System is production ready")
            recommendations.append("Consider setting up monitoring alerts")
            recommendations.append("Review security configuration for production")
            recommendations.append("Set up automated backups")

        return recommendations

    def to_markdown(self) -> str:
        """Convert final report to markdown."""
        report = self.generate()
        summary = report["executive_summary"]

        lines = []
        lines.append("# EMA V5 — Final System Report")
        lines.append(f"\nGenerated: {report['generated_at_str']}")
        lines.append(f"Version: {report['version']}")

        lines.append("\n## Executive Summary")
        lines.append(f"\n**Status**: {summary['status']}")
        lines.append(f"\n- Validation: {summary['validation_passed']}/{summary['validation_passed'] + summary['validation_failed']} passed")
        lines.append(f"- Production Readiness: {summary['readiness_passed']}/{summary['readiness_checks']} checks passed")
        lines.append(f"- Packages: {summary['total_packages']}")
        lines.append(f"- Files: {summary['total_files']}")
        lines.append(f"- Lines: {summary['total_lines']}")
        lines.append(f"- Phases Completed: {summary['phases_completed']}")

        lines.append("\n## Module Inventory")
        inventory = report["module_inventory"]
        for module, info in inventory.items():
            lines.append(f"\n### {module.replace('_', ' ').title()}")
            lines.append(f"Files: {info['files']}")
            for f in info.get("file_list", []):
                lines.append(f"- {f}")

        lines.append("\n## Recommendations")
        for rec in report.get("recommendations", []):
            lines.append(f"- {rec}")

        return "\n".join(lines)
