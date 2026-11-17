"""A throwaway project for tests that build a server or need a Project: a game folder with an empty Data folder and
a state folder in a temporary directory. (It replaced test_service.ServiceTests when the GECK editor tools were
removed, 2026-11-18.)"""
import tempfile
from pathlib import Path

from geck_mcp.config import Project


class ProjectFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)   # per-thread connections close on GC
        root = Path(self.tmp.name)
        self.p = Project(project_id='test', game_root=root / 'game', state_dir=root / 'state')
        (self.p.game_root / 'Data').mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()
