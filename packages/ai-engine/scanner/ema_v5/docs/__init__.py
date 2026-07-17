"""
EMA_V5 Documentation — Isolated documentation generation layer.
Generates API docs, architecture docs, user guides, and developer guides.
"""
from .api_docs import EMAv5APIDocs
from .architecture_docs import EMAv5ArchitectureDocs
from .user_guide import EMAv5UserGuide
from .developer_guide import EMAv5DeveloperGuide
from .doc_generator import EMAv5DocGenerator

__all__ = [
    "EMAv5APIDocs",
    "EMAv5ArchitectureDocs",
    "EMAv5UserGuide",
    "EMAv5DeveloperGuide",
    "EMAv5DocGenerator",
]
