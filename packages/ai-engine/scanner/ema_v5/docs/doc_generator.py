"""
EMA_V5 Doc Generator — Aggregates all documentation into a single generator.
Isolated from existing documentation systems.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger

from .api_docs import EMAv5APIDocs
from .architecture_docs import EMAv5ArchitectureDocs
from .user_guide import EMAv5UserGuide
from .developer_guide import EMAv5DeveloperGuide


class EMAv5DocGenerator:
    """Generates all EMA_V5 documentation."""

    def __init__(self) -> None:
        self._api = EMAv5APIDocs()
        self._architecture = EMAv5ArchitectureDocs()
        self._user_guide = EMAv5UserGuide()
        self._developer_guide = EMAv5DeveloperGuide()

    def generate_all(self) -> Dict[str, Any]:
        """Generate all documentation."""
        return {
            "title": "EMA V5 Strategy Documentation",
            "version": "1.0.0",
            "generated_at": time.time(),
            "generated_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "api": self._api.generate(),
            "architecture": self._architecture.generate(),
            "user_guide": self._user_guide.generate(),
            "developer_guide": self._developer_guide.generate(),
        }

    def generate_api(self) -> Dict[str, Any]:
        """Generate API documentation only."""
        return self._api.generate()

    def generate_architecture(self) -> Dict[str, Any]:
        """Generate architecture documentation only."""
        return self._architecture.generate()

    def generate_user_guide(self) -> Dict[str, Any]:
        """Generate user guide only."""
        return self._user_guide.generate()

    def generate_developer_guide(self) -> Dict[str, Any]:
        """Generate developer guide only."""
        return self._developer_guide.generate()

    def to_markdown(self, docs: Optional[Dict] = None) -> str:
        """Convert documentation to markdown format."""
        if docs is None:
            docs = self.generate_all()

        lines = []
        lines.append(f"# {docs.get('title', 'EMA V5 Documentation')}")
        lines.append(f"\nVersion: {docs.get('version', '1.0.0')}")
        lines.append(f"Generated: {docs.get('generated_at_str', '')}")

        # API Documentation
        api = docs.get("api", {})
        if api:
            lines.append("\n## API Documentation")
            for module_name, module_data in api.get("modules", {}).items():
                lines.append(f"\n### {module_name.replace('_', ' ').title()}")
                if "class" in module_data:
                    lines.append(f"**Class**: `{module_data['class']}`")
                if "description" in module_data:
                    lines.append(f"\n{module_data['description']}")
                if "methods" in module_data:
                    lines.append("\n**Methods**:")
                    for method_name, method_data in module_data["methods"].items():
                        lines.append(f"\n#### `{method_name}`")
                        if "signature" in method_data:
                            lines.append(f"```python\n{method_data['signature']}\n```")
                        if "description" in method_data:
                            lines.append(f"\n{method_data['description']}")
                        if "returns" in method_data:
                            lines.append(f"\n**Returns**: {method_data['returns']}")

        # Architecture
        arch = docs.get("architecture", {})
        if arch:
            lines.append("\n## Architecture")
            overview = arch.get("overview", {})
            if overview:
                lines.append(f"\n{overview.get('description', '')}")
                lines.append("\n**Key Features**:")
                for feature in overview.get("key_features", []):
                    lines.append(f"- {feature}")

        # User Guide
        user = docs.get("user_guide", {})
        if user:
            lines.append("\n## User Guide")
            intro = user.get("introduction", {})
            if intro:
                lines.append(f"\n{intro.get('overview', '')}")

        # Developer Guide
        dev = docs.get("developer_guide", {})
        if dev:
            lines.append("\n## Developer Guide")
            setup = dev.get("setup", {})
            if setup:
                lines.append("\n**Requirements**:")
                for req in setup.get("requirements", []):
                    lines.append(f"- {req}")

        return "\n".join(lines)

    def summary(self) -> Dict[str, Any]:
        """Get documentation summary."""
        docs = self.generate_all()

        # Count modules documented
        api_modules = len(docs.get("api", {}).get("modules", {}))
        arch_modules = len(docs.get("architecture", {}).get("modules", {}))

        return {
            "total_modules_documented": api_modules,
            "architecture_modules": arch_modules,
            "sections": ["api", "architecture", "user_guide", "developer_guide"],
            "formats": ["dict", "markdown"],
        }
