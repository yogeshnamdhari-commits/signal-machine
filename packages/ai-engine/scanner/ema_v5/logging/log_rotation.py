"""
EMA_V5 Log Rotation — Automatic log file rotation and cleanup.
Isolated from existing log rotation systems.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class RotationConfig:
    """Log rotation configuration."""
    max_size_mb: int = 10
    max_files: int = 7
    rotation_interval: str = "daily"  # daily, weekly, size
    compression: bool = False


class EMAv5LogRotation:
    """Automatic log file rotation and cleanup."""

    def __init__(self, config: Optional[RotationConfig] = None) -> None:
        self.config = config or RotationConfig()
        self._log_dir = Path("data/logs")
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def check_and_rotate(self, log_file: str) -> Dict[str, Any]:
        """Check if rotation is needed and perform it."""
        filepath = Path(log_file)
        if not filepath.exists():
            return {"rotated": False, "reason": "file not found"}

        file_size_mb = filepath.stat().st_size / (1024 * 1024)

        # Check size-based rotation
        if file_size_mb >= self.config.max_size_mb:
            return self._rotate_by_size(filepath)

        # Check time-based rotation
        if self.config.rotation_interval == "daily":
            file_age_days = (time.time() - filepath.stat().st_mtime) / 86400
            if file_age_days >= 1:
                return self._rotate_by_time(filepath)
        elif self.config.rotation_interval == "weekly":
            file_age_days = (time.time() - filepath.stat().st_mtime) / 86400
            if file_age_days >= 7:
                return self._rotate_by_time(filepath)

        return {"rotated": False, "reason": "no rotation needed"}

    def _rotate_by_size(self, filepath: Path) -> Dict[str, Any]:
        """Rotate log file by size."""
        timestamp = int(time.time())
        rotated_name = f"{filepath.stem}.{timestamp}{filepath.suffix}"
        rotated_path = filepath.parent / rotated_name

        try:
            filepath.rename(rotated_path)
            # Create new empty file
            filepath.touch()

            self._cleanup_old_files(filepath.parent, filepath.stem)

            logger.info("EMAv5 log rotated (size): {} → {}", filepath.name, rotated_name)
            return {"rotated": True, "reason": "size limit", "new_file": str(rotated_path)}
        except Exception as e:
            logger.error("EMAv5 log rotation failed: {}", e)
            return {"rotated": False, "reason": str(e)}

    def _rotate_by_time(self, filepath: Path) -> Dict[str, Any]:
        """Rotate log file by time."""
        timestamp = time.strftime("%Y%m%d")
        rotated_name = f"{filepath.stem}.{timestamp}{filepath.suffix}"
        rotated_path = filepath.parent / rotated_name

        try:
            if rotated_path.exists():
                # Append to existing rotated file
                with open(filepath, "rb") as src:
                    with open(rotated_path, "ab") as dst:
                        dst.write(src.read())
            else:
                filepath.rename(rotated_path)

            # Create new empty file
            filepath.touch()

            self._cleanup_old_files(filepath.parent, filepath.stem)

            logger.info("EMAv5 log rotated (time): {} → {}", filepath.name, rotated_name)
            return {"rotated": True, "reason": "time interval", "new_file": str(rotated_path)}
        except Exception as e:
            logger.error("EMAv5 log rotation failed: {}", e)
            return {"rotated": False, "reason": str(e)}

    def _cleanup_old_files(self, directory: Path, stem: str) -> None:
        """Remove old rotated files beyond max_files limit."""
        pattern = f"{stem}.*"
        files = sorted(directory.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)

        # Keep only max_files most recent
        for old_file in files[self.config.max_files:]:
            try:
                old_file.unlink()
                logger.debug("EMAv5 log cleanup: removed {}", old_file.name)
            except Exception:
                pass

    def get_log_files(self) -> List[Dict[str, Any]]:
        """Get all log files with metadata."""
        files = []
        for f in self._log_dir.glob("ema_v5*"):
            stat = f.stat()
            files.append({
                "name": f.name,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "modified": stat.st_mtime,
                "age_hours": round((time.time() - stat.st_mtime) / 3600, 1),
            })
        return sorted(files, key=lambda x: x["modified"], reverse=True)

    def cleanup_all(self) -> int:
        """Cleanup all old log files. Returns count removed."""
        count = 0
        for f in self._log_dir.glob("ema_v5*"):
            try:
                age_days = (time.time() - f.stat().st_mtime) / 86400
                if age_days > self.config.max_files:
                    f.unlink()
                    count += 1
            except Exception:
                pass
        return count
