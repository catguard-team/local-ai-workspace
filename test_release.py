"""Release packaging rejects private state and linked or injected source files."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('release_check', Path(__file__).parent / 'scripts/check_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'README.md').write_text('Example project', encoding='utf-8')

    def manifest(self, value):
        (self.root / 'release-files.txt').write_text(value, encoding='utf-8')

    def test_local_state_is_excluded_even_when_present(self):
        (self.root / '.runtime').mkdir()
        (self.root / '.runtime/app.db').write_bytes(b'private')
        self.manifest('README.md\n')
        self.assertEqual([name for name, _ in release.source_files(self.root)], ['README.md'])

    def test_private_traversal_and_binary_files_rejected(self):
        for name in ('.runtime/app.db', '../outside.txt', 'identity.pem', 'agent/note.txt'):
            with self.subTest(name=name):
                self.manifest(name)
                with self.assertRaises(ValueError):
                    release.source_files(self.root)
        (self.root / 'README.md').write_bytes(b'bad\0data')
        self.manifest('README.md')
        with self.assertRaises(ValueError):
            release.source_files(self.root)

    def test_private_key_detection_does_not_echo_content(self):
        text = '-----BEGIN ' + 'PRIVATE KEY-----\n' + 'synthetic-private-data'
        (self.root / 'README.md').write_text(text, encoding='utf-8')
        self.manifest('README.md')
        with self.assertRaises(ValueError) as context:
            release.source_files(self.root)
        self.assertNotIn('synthetic-private-data', str(context.exception))


if __name__ == '__main__':
    unittest.main()

