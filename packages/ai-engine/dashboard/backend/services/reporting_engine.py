"""
Reporting Engine — PDF, JSON, CSV, HTML report generation.

Generates:
- daily_report, weekly_report, monthly_report
- risk_report, execution_report, portfolio_report, arbitrage_report
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class ReportingEngine:
    """
    Institutional-grade reporting engine.
    Generates reports in multiple formats.
    """

    REPORTS_DIR = Path("data/reports")

    def __init__(self) -> None:
        self.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        self._generated_reports: List[Dict[str, Any]] = []

    def generate_report(
        self,
        report_type: str,
        data: Dict[str, Any],
        format: str = "json",
    ) -> Path:
        """Generate a report of the specified type and format."""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{report_type}_{timestamp}"

        if format == "json":
            path = self.REPORTS_DIR / f"{filename}.json"
            with open(path, "w") as f:
                json.dump(data, f, indent=2, default=str)
        elif format == "csv":
            import csv
            path = self.REPORTS_DIR / f"{filename}.csv"
            # Flatten nested dicts for CSV
            flat = self._flatten_dict(data)
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(flat.keys())
                writer.writerow(flat.values())
        elif format == "html":
            path = self.REPORTS_DIR / f"{filename}.html"
            html = self._generate_html(report_type, data)
            with open(path, "w") as f:
                f.write(html)
        else:
            path = self.REPORTS_DIR / f"{filename}.json"
            with open(path, "w") as f:
                json.dump(data, f, indent=2, default=str)

        self._generated_reports.append({
            "report_type": report_type,
            "format": format,
            "path": str(path),
            "generated_at": time.time(),
        })

        logger.info("[Reporting] Generated {} report: {}", report_type, path)
        return path

    def generate_daily_report(self, data: Dict[str, Any]) -> Path:
        """Generate daily performance report."""
        return self.generate_report("daily", data, "json")

    def generate_weekly_report(self, data: Dict[str, Any]) -> Path:
        """Generate weekly performance report."""
        return self.generate_report("weekly", data, "json")

    def generate_monthly_report(self, data: Dict[str, Any]) -> Path:
        """Generate monthly performance report."""
        return self.generate_report("monthly", data, "json")

    def generate_risk_report(self, data: Dict[str, Any]) -> Path:
        """Generate risk report."""
        return self.generate_report("risk", data, "json")

    def generate_execution_report(self, data: Dict[str, Any]) -> Path:
        """Generate execution quality report."""
        return self.generate_report("execution", data, "json")

    def generate_portfolio_report(self, data: Dict[str, Any]) -> Path:
        """Generate portfolio report."""
        return self.generate_report("portfolio", data, "json")

    def generate_arbitrage_report(self, data: Dict[str, Any]) -> Path:
        """Generate arbitrage report."""
        return self.generate_report("arbitrage", data, "json")

    def _flatten_dict(self, d: Dict[str, Any], parent_key: str = "", sep: str = ".") -> Dict[str, Any]:
        """Flatten a nested dictionary."""
        items: List[tuple] = []
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(self._flatten_dict(v, new_key, sep).items())
            elif isinstance(v, list):
                items.append((new_key, json.dumps(v, default=str)))
            else:
                items.append((new_key, v))
        return dict(items)

    def _generate_html(self, report_type: str, data: Dict[str, Any]) -> str:
        """Generate an HTML report."""
        sections = []
        for key, value in data.items():
            if isinstance(value, dict):
                rows = "".join(
                    f"<tr><td>{k}</td><td>{v}</td></tr>"
                    for k, v in value.items()
                )
                sections.append(f"""
                <div class="section">
                    <h3>{key.replace('_', ' ').title()}</h3>
                    <table>{rows}</table>
                </div>
                """)
            elif isinstance(value, list):
                sections.append(f"""
                <div class="section">
                    <h3>{key.replace('_', ' ').title()}</h3>
                    <p>{len(value)} items</p>
                </div>
                """)
            else:
                sections.append(f"""
                <div class="metric">
                    <span class="label">{key.replace('_', ' ').title()}</span>
                    <span class="value">{value}</span>
                </div>
                """)

        return f"""<!DOCTYPE html>
<html>
<head>
    <title>{report_type.title()} Report</title>
    <style>
        body {{ font-family: 'Inter', sans-serif; background: #0a0a1a; color: #e0e0e0; padding: 40px; }}
        h1 {{ color: #4a9eff; border-bottom: 2px solid #2a2a4e; padding-bottom: 10px; }}
        h3 {{ color: #888; margin-top: 20px; }}
        table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
        td, th {{ padding: 8px 12px; border: 1px solid #2a2a4e; text-align: left; }}
        th {{ background: #1a1a2e; color: #4a9eff; }}
        .metric {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #1a1a2e; }}
        .label {{ color: #888; }}
        .value {{ font-weight: bold; color: #fff; }}
        .section {{ margin: 20px 0; }}
    </style>
</head>
<body>
    <h1>{report_type.title()} Report</h1>
    <p>Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}</p>
    {''.join(sections)}
</body>
</html>"""

    def get_generated_reports(self) -> List[Dict[str, Any]]:
        """Get list of generated reports."""
        return self._generated_reports[-100:]
