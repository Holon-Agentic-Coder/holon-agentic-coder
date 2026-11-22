"""Tests for Antigravity docker session mounts and tmpfs configuration."""

import os
import unittest
from unittest.mock import patch

from sandbox_executor.cli import get_agent_session_mounts
from sandbox_executor.cli.antigravity_mounts import (
    ANTIGRAVITY_WRITABLE_TMPFS,
    antigravity_session_mounts,
)


class TestAntigravityMounts(unittest.TestCase):
    def test_antigravity_session_mounts_structure(self):
        mounts = antigravity_session_mounts("/path/to/session")
        self.assertEqual(mounts[0], "-v")
        self.assertEqual(mounts[1], "/path/to/session:/home/holon/.gemini/antigravity-cli:ro")
        for spec in ANTIGRAVITY_WRITABLE_TMPFS:
            self.assertIn("--tmpfs", mounts)
            self.assertIn(spec, mounts)

    @patch("sys.platform", "darwin")
    @patch("os.path.exists", return_value=True)
    def test_antigravity_session_mounts_darwin(self, mock_exists):
        mounts = get_agent_session_mounts("antigravity")
        self.assertIsInstance(mounts, list)
        self.assertIn("-v", mounts)
        self.assertTrue(any("/home/holon/.gemini/antigravity-cli:ro" in m for m in mounts))
        self.assertIn("--tmpfs", mounts)
        self.assertIn("/home/holon/.gemini/config:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/log:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/cache:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/brain:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/conversations:uid=1000,gid=1000", mounts)
        self.assertFalse(any(m.endswith("antigravity-cli:rw") for m in mounts))

    @patch("sys.platform", "linux")
    @patch("os.path.exists", return_value=True)
    def test_antigravity_linux_mounts(self, mock_exists):
        mounts = get_agent_session_mounts("antigravity")
        self.assertIsInstance(mounts, list)
        self.assertIn("-v", mounts)
        self.assertTrue(any("/home/holon/.gemini/antigravity-cli:ro" in m for m in mounts))
        self.assertIn("--tmpfs", mounts)
        self.assertIn("/home/holon/.gemini/config:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/log:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/cache:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/brain:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/conversations:uid=1000,gid=1000", mounts)
        self.assertFalse(any(m.endswith("antigravity-cli:rw") for m in mounts))

    @patch("sys.platform", "linux")
    @patch("os.path.exists")
    def test_antigravity_linux_dedicated_session_mounts(self, mock_exists):
        mock_exists.side_effect = lambda p: ".holon/sessions/antigravity" in p
        mounts = get_agent_session_mounts("antigravity-agent")
        self.assertIsInstance(mounts, list)
        self.assertIn("-v", mounts)
        self.assertTrue(any("/home/holon/.gemini/antigravity-cli:ro" in m for m in mounts))
        self.assertIn("--tmpfs", mounts)
        self.assertIn("/home/holon/.gemini/config:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/log:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/cache:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/brain:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/conversations:uid=1000,gid=1000", mounts)
        self.assertFalse(any(m.endswith("antigravity-cli:rw") for m in mounts))

    @patch("sys.platform", "linux")
    @patch("os.path.exists")
    @patch("os.getuid", return_value=1001, create=True)
    def test_antigravity_linux_dbus_mount(self, mock_uid, mock_exists):
        mock_exists.side_effect = lambda p: p in ("/run/user/1001/bus", os.path.expanduser("~/.gemini/antigravity-cli"))
        mounts = get_agent_session_mounts("antigravity-agent")
        self.assertFalse(any("bus" in m for m in mounts))
        self.assertTrue(any("/home/holon/.gemini/antigravity-cli:ro" in m for m in mounts))
        self.assertIn("--tmpfs", mounts)
        self.assertIn("/home/holon/.gemini/config:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/log:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/cache:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/brain:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/conversations:uid=1000,gid=1000", mounts)
        self.assertFalse(any(m.endswith("antigravity-cli:rw") for m in mounts))

    @patch("sys.platform", "darwin")
    @patch("os.path.exists", return_value=False)
    def test_antigravity_darwin_missing_session_without_key_exits(self, mock_exists):
        env = {k: v for k, v in os.environ.items() if k not in ("HOLON_AGENT_KEY", "HOLON_ANTIGRAVITY_SESSION_DIR")}
        with patch.dict(os.environ, env, clear=True), patch("sys.stderr"), self.assertRaises(SystemExit) as ctx:
            get_agent_session_mounts("antigravity")
        self.assertEqual(ctx.exception.code, 1)

    @patch("sys.platform", "darwin")
    @patch("os.path.exists")
    def test_antigravity_darwin_key_set_falls_back_to_host_cli(self, mock_exists):
        host_cli = os.path.expanduser("~/.gemini/antigravity-cli")
        mock_exists.side_effect = lambda p: p == host_cli
        with patch.dict(os.environ, {"HOLON_AGENT_KEY": "not-a-real-key"}):
            mounts = get_agent_session_mounts("antigravity")
        self.assertIn(f"{host_cli}:/home/holon/.gemini/antigravity-cli:ro", mounts)
        for spec in ANTIGRAVITY_WRITABLE_TMPFS:
            self.assertIn(spec, mounts)
