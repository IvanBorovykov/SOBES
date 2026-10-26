"""Native file lists and explicit folder preview for voice questions."""

from pathlib import Path

import objc
from AppKit import (NSAlert, NSAlertFirstButtonReturn, NSAppearance, NSAppearanceNameDarkAqua,
                    NSBackingStoreBuffered, NSButton, NSFloatingWindowLevel, NSFont,
                    NSModalResponseOK, NSOpenPanel, NSPanel, NSScrollView, NSTableColumn,
                    NSTableView, NSTextField, NSView, NSWindowStyleMaskClosable, NSWindowStyleMaskTitled)
from Foundation import NSObject, NSMakeRect, NSNotFound

from file_context import MAX_FILES, folder_paths, validate_paths


class FileList(NSObject):
    """Shared multi-selection list for current context and folder candidates."""

    def initWithPaths_(self, paths):
        self = objc.super(FileList, self).init()
        if self is None:
            return None
        self.paths = list(paths)
        self._scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 736, 270))
        self._scroll.setHasVerticalScroller_(True)
        self._scroll.setHasHorizontalScroller_(True)
        self._table = NSTableView.alloc().initWithFrame_(self._scroll.bounds())
        self._table.setAllowsMultipleSelection_(True)
        self._table.setAllowsEmptySelection_(True)
        self._table.setUsesAlternatingRowBackgroundColors_(True)
        self._table.setRowHeight_(25)
        self._table.setAccessibilityLabel_('Файлы — выделите несколько с помощью ⌘ или Shift')
        for identifier, title, width in (('name', 'Файл', 250), ('folder', 'Папка', 460)):
            column = NSTableColumn.alloc().initWithIdentifier_(identifier)
            column.setTitle_(title)
            column.setWidth_(width)
            column.setEditable_(False)
            self._table.addTableColumn_(column)
        self._table.setDataSource_(self)
        self._scroll.setDocumentView_(self._table)
        return self

    def numberOfRowsInTableView_(self, table):
        return len(self.paths)

    def tableView_objectValueForTableColumn_row_(self, table, column, row):
        path = Path(self.paths[row])
        if column.identifier() == 'name':
            return path.name
        return str(path.parent)

    @objc.python_method
    def selected_paths(self):
        indexes = self._table.selectedRowIndexes()
        index = indexes.firstIndex()
        result = []
        while index != NSNotFound:
            result.append(self.paths[index])
            index = indexes.indexGreaterThanIndex_(index)
        return result

    @objc.python_method
    def replace_paths(self, paths):
        self._table.deselectAll_(None)
        self.paths = list(paths)
        self._table.reloadData()

    def selectAll_(self, sender):
        self._table.selectAll_(sender)


class FileContextPanel(NSObject):
    def initWithPreferences_changed_(self, preferences, changed):
        self = objc.super(FileContextPanel, self).init()
        if self is None:
            return None
        self._preferences, self._changed = preferences, changed
        self._panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(100, 100, 780, 550), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered, False)
        self._panel.setTitle_('Файлы для голосового вопроса')
        self._panel.setReleasedWhenClosed_(False)
        self._panel.setLevel_(NSFloatingWindowLevel + 2)
        self._panel.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        content = self._panel.contentView()
        label = NSTextField.wrappingLabelWithString_(
            'Добавьте файлы или выберите нужные из папки. Задайте вопрос вслух и нажмите ⌥A.\n'
            'Несколько строк: ⌘ + щелчок или Shift. «Убрать» исключает файлы только из контекста.')
        label.setFrame_(NSMakeRect(20, 490, 740, 44))
        content.addSubview_(label)
        self._list = FileList.alloc().initWithPaths_([])
        self._list._scroll.setFrame_(NSMakeRect(20, 174, 740, 270))
        content.addSubview_(self._list._scroll)
        self._info = NSTextField.wrappingLabelWithString_('')
        self._info.setFrame_(NSMakeRect(20, 54, 740, 70))
        self._info.setFont_(NSFont.systemFontOfSize_(12))
        content.addSubview_(self._info)
        for title, action, x, y, width in (
            ('Добавить файлы…', 'add:', 18, 450, 200),
            ('Из папки…', 'addFolder:', 228, 450, 180),
            ('Выбрать все', 'selectAll:', 18, 134, 160),
            ('Убрать выбранные', 'remove:', 188, 134, 210),
            ('Убрать все', 'clear:', 408, 134, 160),
            ('Закрыть', 'close:', 650, 14, 110),
        ):
            button = NSButton.buttonWithTitle_target_action_(title, self, action)
            button.setFrame_(NSMakeRect(x, y, width, 32))
            content.addSubview_(button)
        return self

    @objc.python_method
    def refresh(self):
        self._list.replace_paths(self._preferences.context_files)
        missing = sum(not Path(path).is_file() for path in self._preferences.context_files)
        self._info.setStringValue_(
            f'В контексте: {len(self._preferences.context_files)} / {MAX_FILES} файлов'
            + (f' · Недоступно: {missing}' if missing else '')
            + '. Выбор сохраняется. Лимит контекста — 300 000 символов.\n'
            'Для кода передаются номера строк. Сохраните изменения в редакторе (⌘S) перед вопросом.\n'
            'Текст файлов и аудио отправляются Gemini при ⌥A. Для сканов PDF нужен OCR.')

    @objc.python_method
    def show(self):
        self.refresh()
        self._panel.center()
        self._panel.makeKeyAndOrderFront_(None)

    @objc.python_method
    def save_paths(self, paths):
        previous = self._preferences.context_files
        self._preferences.context_files = list(paths)
        try:
            self._preferences.save()
        except OSError:
            self._preferences.context_files = previous
            raise
        self._changed()
        self.refresh()

    @objc.python_method
    def report(self, error):
        alert = NSAlert.alloc().init()
        alert.setMessageText_('Не удалось изменить список файлов')
        alert.setInformativeText_(str(error))
        alert.window().setLevel_(NSFloatingWindowLevel + 3)
        alert.runModal()

    @objc.python_method
    def add_paths(self, paths):
        added = validate_paths(paths)
        merged = list(dict.fromkeys([*self._preferences.context_files, *added]))
        if len(merged) > MAX_FILES:
            raise ValueError(f'Можно выбрать не больше {MAX_FILES} файлов. Уберите лишние из выделения или контекста.')
        if merged != self._preferences.context_files:
            self.save_paths(merged)

    def add_(self, sender):
        picker = NSOpenPanel.openPanel()
        picker.setAllowsMultipleSelection_(True)
        picker.setCanChooseDirectories_(False)
        picker.setCanChooseFiles_(True)
        picker.setTitle_('Выберите материалы — можно сразу несколько файлов')
        picker.setLevel_(NSFloatingWindowLevel + 3)
        if picker.runModal() != NSModalResponseOK:
            return
        try:
            self.add_paths([str(url.path()) for url in picker.URLs()])
        except (OSError, ValueError) as error:
            self.report(error)

    def addFolder_(self, sender):
        picker = NSOpenPanel.openPanel()
        picker.setCanChooseDirectories_(True)
        picker.setCanChooseFiles_(False)
        picker.setAllowsMultipleSelection_(False)
        picker.setTitle_('Выберите папку для поиска файлов')
        picker.setLevel_(NSFloatingWindowLevel + 3)
        if picker.runModal() != NSModalResponseOK:
            return
        try:
            candidates = folder_paths(str(picker.URL().path()))
            if not candidates:
                raise ValueError('В папке нет поддерживаемых файлов: исходников, TXT, Markdown, CSV/JSON, PDF или DOCX.')
            listing = FileList.alloc().initWithPaths_(candidates)
            view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 736, 310))
            listing._scroll.setFrame_(NSMakeRect(0, 40, 736, 270))
            view.addSubview_(listing._scroll)
            button = NSButton.buttonWithTitle_target_action_('Выбрать все', listing, 'selectAll:')
            button.setFrame_(NSMakeRect(0, 0, 160, 32))
            view.addSubview_(button)
            alert = NSAlert.alloc().init()
            alert.setMessageText_(f'Выберите файлы из папки · найдено {len(candidates)}')
            alert.setInformativeText_(
                f'Выделите нужные строки (⌘ / Shift) или нажмите «Выбрать все». В контексте максимум {MAX_FILES} файлов. '
                'Включены подпапки; скрытые папки, venv, node_modules, __pycache__ и ссылки пропущены. '
                'Добавятся только выделенные файлы.')
            alert.setAccessoryView_(view)
            alert.addButtonWithTitle_('Добавить выбранные')
            alert.addButtonWithTitle_('Отмена')
            alert.window().setLevel_(NSFloatingWindowLevel + 3)
            while alert.runModal() == NSAlertFirstButtonReturn:
                try:
                    selected = listing.selected_paths()
                    if not selected:
                        raise ValueError('Выделите хотя бы один файл в списке.')
                    self.add_paths(selected)
                    break
                except (OSError, ValueError) as error:
                    self.report(error)
        except (OSError, ValueError) as error:
            self.report(error)

    def selectAll_(self, sender):
        self._list.selectAll_(sender)

    def remove_(self, sender):
        selected = set(self._list.selected_paths())
        if not selected:
            return
        try:
            self.save_paths([path for path in self._preferences.context_files if path not in selected])
        except OSError as error:
            self.report(error)

    def clear_(self, sender):
        if not self._preferences.context_files:
            return
        try:
            self.save_paths([])
        except OSError as error:
            self.report(error)

    def close_(self, sender):
        self._panel.orderOut_(None)
