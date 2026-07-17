"""
EMA_V5 Final Integration — Wires all modules together with unified entry point.
Isolated from existing integration systems.
"""
from .module_registry import EMAv5ModuleRegistry
from .lifecycle_manager import EMAv5LifecycleManager
from .unified_entry import EMAv5UnifiedEntry

__all__ = [
    "EMAv5ModuleRegistry",
    "EMAv5LifecycleManager",
    "EMAv5UnifiedEntry",
]
