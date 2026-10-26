import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from AppKit import NSAlertFirstButtonReturn, NSApplication, NSModalResponseOK
from Foundation import NSMutableIndexSet

from file_context import MAX_FILES, folder_paths
from file_context_ui import FileContextPanel
import file_context_ui
from preferences import Preferences


class FolderFilesTests(unittest.TestCase):
    def test_folder_includes_supported_nested_files_and_skips_dependencies_and_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = []
            for name in ('main.py', 'src/service.py', 'notes.md', 'image.png',
                         '.secret.py', '.venv/lib.py', 'node_modules/test.js',
                         '__pycache__/test.py', 'venv/lib.py'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('text')
                if name in ('main.py', 'src/service.py', 'notes.md'):
                    expected.append(str(path))
            (root / 'link.py').symlink_to(root / 'main.py')
            (root / 'loop').symlink_to(root, target_is_directory=True)
            self.assertCountEqual(folder_paths(root), expected)

    def test_folder_scan_is_bounded_without_returning_partial_results(self):
        with patch('file_context.os.walk', return_value=[('/tmp', [], ['x.py'] * 5001)]):
            with self.assertRaisesRegex(ValueError, 'Папка слишком большая'):
                folder_paths('/tmp')


class FileContextPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = NSApplication.sharedApplication()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.preferences = Preferences(self.root / 'settings.json')
        self.changed = Mock()
        self.panel = FileContextPanel.alloc().initWithPreferences_changed_(self.preferences, self.changed)
        self.addCleanup(self.panel._panel.orderOut_, None)
        self.paths = []
        for index in range(3):
            path = self.root / f'{index}.py'
            path.write_text(f'value = {index}\n')
            self.paths.append(str(path))
        self.panel.add_paths(self.paths)
        self.changed.reset_mock()

    def test_remove_multiple_selected_rows_preserves_other_files_and_disk_files(self):
        selection = NSMutableIndexSet.indexSet()
        selection.addIndex_(0)
        selection.addIndex_(2)
        self.panel._list._table.selectRowIndexes_byExtendingSelection_(selection, False)
        self.panel.remove_(None)
        self.assertEqual(self.preferences.context_files, [self.paths[1]])
        self.assertEqual(Preferences(self.preferences.path).context_files, [self.paths[1]])
        self.assertTrue(all(Path(path).is_file() for path in self.paths))
        self.changed.assert_called_once_with()

    def test_select_all_and_clear_persist_empty_context(self):
        self.panel.selectAll_(None)
        self.assertEqual(self.panel._list.selected_paths(), self.paths)
        self.panel.clear_(None)
        self.assertEqual(self.panel._list._table.numberOfRows(), 0)
        self.assertEqual(Preferences(self.preferences.path).context_files, [])
        self.assertTrue(all(Path(path).is_file() for path in self.paths))

    def test_save_failure_preserves_context_and_selection(self):
        self.panel.selectAll_(None)
        with patch.object(self.preferences, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.panel.save_paths([])
        self.assertEqual(self.preferences.context_files, self.paths)
        self.assertEqual(self.panel._list.selected_paths(), self.paths)
        self.changed.assert_not_called()

    def test_duplicate_add_does_not_duplicate_files_or_invalidate_current_answer(self):
        self.panel.add_paths(self.paths)
        self.assertEqual(self.preferences.context_files, self.paths)
        self.changed.assert_not_called()

    def test_over_limit_add_keeps_existing_context(self):
        extras = []
        for index in range(MAX_FILES):
            path = self.root / f'extra{index}.py'
            path.write_text('pass')
            extras.append(str(path))
        with self.assertRaisesRegex(ValueError, 'не больше'):
            self.panel.add_paths(extras)
        self.assertEqual(self.preferences.context_files, self.paths)
        self.changed.assert_not_called()

    def test_folder_preview_adds_only_selected_files(self):
        folder = self.root / 'project'
        folder.mkdir()
        for name in ('a.py', 'b.py'):
            (folder / name).write_text('pass')
        picker, alert = Mock(), Mock()
        picker.runModal.return_value = NSModalResponseOK
        picker.URL.return_value.path.return_value = str(folder)

        def choose_one():
            view = alert.setAccessoryView_.call_args.args[0]
            table = view.subviews()[0].documentView()
            selection = NSMutableIndexSet.indexSet()
            selection.addIndex_(1)
            table.selectRowIndexes_byExtendingSelection_(selection, False)
            return NSAlertFirstButtonReturn

        alert.runModal.side_effect = choose_one
        with patch.object(file_context_ui, 'NSOpenPanel') as open_panel, \
                patch.object(file_context_ui, 'NSAlert') as alert_class:
            open_panel.openPanel.return_value = picker
            alert_class.alloc.return_value.init.return_value = alert
            self.panel.addFolder_(None)
        self.assertEqual(self.preferences.context_files, self.paths + [str(folder / 'b.py')])

    def test_cancelling_folder_preview_keeps_context(self):
        picker, alert = Mock(), Mock()
        picker.runModal.return_value = NSModalResponseOK
        picker.URL.return_value.path.return_value = str(self.root)
        alert.runModal.return_value = 1001
        with patch.object(file_context_ui, 'NSOpenPanel') as open_panel, \
                patch.object(file_context_ui, 'NSAlert') as alert_class:
            open_panel.openPanel.return_value = picker
            alert_class.alloc.return_value.init.return_value = alert
            self.panel.addFolder_(None)
        self.assertEqual(self.preferences.context_files, self.paths)
        self.changed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
