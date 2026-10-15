import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.agent import codex_executable, configured_codex_paths


class CodexConfigTests(unittest.TestCase):
    def test_bundled_defaults_expand_home(self):
        with patch.dict(os.environ, {}, clear=True):
            paths = configured_codex_paths()
        self.assertEqual(len(paths), 4)
        self.assertTrue(all(path.is_absolute() for path in paths))

    def test_custom_config_selects_first_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            binary = root / 'codex with spaces'
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o700)
            paths = os.pathsep.join([str(root / 'missing'), str(binary)])
            with patch.dict(os.environ, {'PR_RESOLVER_CODEX_PATHS': paths}, clear=True):
                with patch('scripts.agent.shutil.which', return_value=None):
                    self.assertEqual(codex_executable(), str(binary))

    def test_invalid_configuration_fails_clearly(self):
        for value in ('relative/codex', os.pathsep + '/tmp/codex',
                      '/tmp/codex' + os.pathsep, '   '):
            with self.subTest(value=value), patch.dict(os.environ, {'PR_RESOLVER_CODEX_PATHS': value}):
                with self.assertRaisesRegex(ValueError, 'PR_RESOLVER_CODEX_PATHS'):
                    configured_codex_paths()

    def test_empty_value_disables_fallback(self):
        with patch.dict(os.environ, {'PR_RESOLVER_CODEX_PATHS': ''}, clear=True):
            with patch('scripts.agent.shutil.which', return_value=None):
                with self.assertRaisesRegex(ValueError, 'not found'):
                    codex_executable()

    def test_tilde_expansion(self):
        with patch.dict(os.environ, {'PR_RESOLVER_CODEX_PATHS': '~/tools/codex'}):
            self.assertEqual(configured_codex_paths(), [Path.home() / 'tools/codex'])

    def test_explicit_override_and_path_precede_config(self):
        with patch.dict(os.environ, {'PR_RESOLVER_CODEX': '/chosen/codex'}, clear=True):
            with patch('scripts.agent.shutil.which', return_value='/chosen/codex'):
                with patch('scripts.agent.configured_codex_paths') as config:
                    self.assertEqual(codex_executable(), '/chosen/codex')
                    config.assert_not_called()
        with patch.dict(os.environ, {}, clear=True):
            with patch('scripts.agent.shutil.which', return_value='/path/codex'):
                with patch('scripts.agent.configured_codex_paths') as config:
                    self.assertEqual(codex_executable(), '/path/codex')
                    config.assert_not_called()
