"""
EMA_V5 Final Report — Comprehensive final report for the entire EMA_V5 system.
Isolated from existing reporting systems.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from loguru import logger

from .final_validator import EMAv5FinalValidator
from .production_checker import EMAv5ProductionChecker


class EMAv5FinalReport:
    """Generates the final comprehensive report for EMA_V5."""

    def __init__(self) -> None:
        self._validator = EMAv5FinalValidator()
        self._checker = EMAv5ProductionChecker()

    def generate(self) -> Dict[str, Any]:
        """Generate the final comprehensive report."""
        logger.info("📊 EMA_V5 final report generation")

        # Run all validations
        final_validation = self._validator.validate_all()
        production_check = self._checker.check_all()

        # Get system stats
        system_stats = self._get_system_stats()

        return {
            "report_type": "final",
            "version": "1.0.0",
            "generated_at": time.time(),
            "generated_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "executive_summary": self._executive_summary(final_validation, production_check, system_stats),
            "final_validation": final_validation,
            "production_check": production_check,
            "system_stats": system_stats,
            "module_inventory": self._module_inventory(),
            "recommendations": self._get_all_recommendations(final_validation, production_check),
        }

    def _executive_summary(self, validation: Dict, check: Dict, stats: Dict) -> Dict[str, Any]:
        """Generate executive summary."""
        return {
            "status": "PRODUCTION READY" if check["ready"] else "NOT READY",
            "validation_passed": validation["passed"],
            "validation_failed": validation["failed"],
            "check_passed": check["passed"],
            "check_failed": check["failed"],
            "total_packages": stats["packages"],
            "total_files": stats["total_files"],
            "total_lines": stats["total_lines"],
            "phases_completed": 25,
        }

    def _get_system_stats(self) -> Dict[str, Any]:
        """Get system statistics."""
        from pathlib import Path

        packages = []
        for item in Path("scanner/ema_v5").iterdir():
            if item.is_dir() and not item.name.startswith("__"):
                packages.append(item.name)

        py_files = list(Path("scanner/ema_v5").rglob("*.py"))

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
        from pathlib import Path

        inventory = {}
        for item in Path("scanner/ema_v5").iterdir():
            if item.is_dir() and not item.name.startswith("__"):
                py_files = list(item.glob("*.py"))
                inventory[item.name] = {
                    "files": len(py_files),
                    "file_list": [f.name for f in py_files],
                }

        return inventory

    def _get_all_recommendations(self, validation: Dict, check: Dict) -> List[str]:
        """Get all recommendations."""
        recommendations = []

        # From validation
        for result in validation.get("results", []):
            if not result["passed"]:
                recommendations.append(f"Fix: {result['details']}")

        # From check
        recommendations.extend(check.get("recommendations", []))

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
        lines.append(f"- Production Check: {summary['check_passed']}/{summary['check_passed'] + summary['check_failed']} passed")
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
