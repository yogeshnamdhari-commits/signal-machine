"""
EMA_V5 Module Registry — Central registry for all EMA_V5 modules.
Provides dependency injection and module lifecycle management.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from loguru import logger


@dataclass
class ModuleInfo:
    """Information about a registered module."""
    name: str
    version: str = "1.0.0"
    description: str = ""
    dependencies: Set[str] = field(default_factory=set)
    instance: Any = None
    initialized: bool = False
    start_time: float = 0.0
    status: str = "registered"  # registered, initialized, running, stopped, error


class EMAv5ModuleRegistry:
    """Central registry for all EMA_V5 modules."""

    def __init__(self) -> None:
        self._modules: Dict[str, ModuleInfo] = {}
        self._factories: Dict[str, Callable] = {}

    def register(self, name: str, factory: Callable, dependencies: Optional[Set[str]] = None,
                description: str = "", version: str = "1.0.0") -> None:
        """Register a module with its factory function."""
        self._modules[name] = ModuleInfo(
            name=name,
            version=version,
            description=description,
            dependencies=dependencies or set(),
        )
        self._factories[name] = factory
        logger.debug("EMAv5 module registered: {} v{}", name, version)

    def get(self, name: str) -> Any:
        """Get a module instance (creates if not initialized)."""
        if name not in self._modules:
            raise KeyError(f"Module '{name}' not registered")

        module = self._modules[name]
        if module.instance is not None:
            return module.instance

        # Initialize on first access
        return self._initialize_module(name)

    def _initialize_module(self, name: str) -> Any:
        """Initialize a module and its dependencies."""
        module = self._modules[name]

        if module.initialized:
            return module.instance

        # Initialize dependencies first
        for dep in module.dependencies:
            if dep in self._modules and not self._modules[dep].initialized:
                self._initialize_module(dep)

        # Create instance
        factory = self._factories.get(name)
        if not factory:
            raise KeyError(f"No factory for module '{name}'")

        try:
            instance = factory()
            module.instance = instance
            module.initialized = True
            module.start_time = time.time()
            module.status = "running"
            logger.info("EMAv5 module initialized: {} v{}", name, module.version)
            return instance
        except Exception as e:
            module.status = "error"
            logger.error("EMAv5 module init failed: {} - {}", name, e)
            raise

    def initialize_all(self) -> Dict[str, bool]:
        """Initialize all registered modules."""
        results = {}
        for name in self._modules:
            try:
                self._initialize_module(name)
                results[name] = True
            except Exception as e:
                results[name] = False
                logger.error("EMAv5 module {} init failed: {}", name, e)
        return results

    def stop_all(self) -> None:
        """Stop all modules."""
        for name, module in self._modules.items():
            if module.initialized:
                module.status = "stopped"
                logger.info("EMAv5 module stopped: {}", name)

    def get_status(self) -> Dict[str, Any]:
        """Get status of all modules."""
        return {
            name: {
                "version": module.version,
                "status": module.status,
                "initialized": module.initialized,
                "uptime": round(time.time() - module.start_time, 1) if module.start_time else 0,
                "dependencies": list(module.dependencies),
            }
            for name, module in self._modules.items()
        }

    def list_modules(self) -> List[str]:
        """List all registered module names."""
        return list(self._modules.keys())

    def get_dependency_graph(self) -> Dict[str, List[str]]:
        """Get dependency graph."""
        return {
            name: list(module.dependencies)
            for name, module in self._modules.items()
        }
