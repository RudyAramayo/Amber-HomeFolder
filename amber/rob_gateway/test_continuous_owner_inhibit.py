"""No hardware I/O: legacy CAN/startup inhibit behavior."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import rob_amber_init_can as initializer


class InhibitTests(unittest.TestCase):
    def test_initializer_blocks_before_mapping_or_any_subprocess(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inhibit'
            path.touch()
            with mock.patch.object(initializer, 'CONTINUOUS_OWNER_BLOCK_PATH', str(path)), \
                 mock.patch.object(initializer.os, 'geteuid', return_value=0), \
                 mock.patch.object(initializer.sys, 'argv', ['rob-amber-init-can']), \
                 mock.patch.object(initializer, 'load_mapping') as mapping, \
                 mock.patch.object(initializer.subprocess, 'run') as run, \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    initializer.main()
                mapping.assert_not_called()
                run.assert_not_called()

    def test_inhibit_never_expires_and_unreadable_is_not_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inhibit'
            with mock.patch.object(initializer, 'CONTINUOUS_OWNER_BLOCK_PATH', str(path)), \
                 contextlib.redirect_stderr(io.StringIO()):
                initializer.require_legacy_ownership()
                path.touch()
                os.utime(path, (0, 0))
                with self.assertRaises(SystemExit):
                    initializer.require_legacy_ownership()
                path.unlink()
                path.symlink_to(Path(directory) / 'missing')
                with self.assertRaises(SystemExit):
                    initializer.require_legacy_ownership()
                with mock.patch.object(initializer.os, 'lstat', side_effect=PermissionError):
                    with self.assertRaises(SystemExit):
                        initializer.require_legacy_ownership()

    def test_rc_local_guard_blocks_before_network_setup(self):
        source = Path(__file__).resolve().parents[2] / 'system/etc/rc.local'
        if not source.exists():
            source = Path(__file__).with_name('rc.local.reviewed')
        text = source.read_text()
        prefix = text.split('/usr/sbin/ip link', 1)[0]
        self.assertIn('require_legacy_ownership', prefix)
        self.assertIn('require_legacy_ownership\n/usr/sbin/runuser', text)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inhibit'
            fixture = prefix.replace(initializer.CONTINUOUS_OWNER_BLOCK_PATH, str(path))
            fixture += '\necho SAFE_FIXTURE_CONTINUATION\n'
            missing = subprocess.run(['/bin/bash', '-c', fixture], text=True, capture_output=True)
            self.assertEqual(missing.returncode, 0)
            path.symlink_to(Path(directory) / 'missing')
            blocked = subprocess.run(['/bin/bash', '-c', fixture], text=True, capture_output=True)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertNotIn('SAFE_FIXTURE_CONTINUATION', blocked.stdout)


if __name__ == '__main__':
    unittest.main()
