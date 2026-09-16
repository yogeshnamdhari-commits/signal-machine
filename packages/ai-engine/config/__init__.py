from .settings import config as _settings_config
from .runtime_defaults import apply_runtime_defaults

# Canonical effective configuration. Legacy settings remain readable as
# historical source, but runtime consumers receive normalized environment and
# L2 market-data defaults.
config = apply_runtime_defaults(_settings_config)

__all__ = ["config"]
