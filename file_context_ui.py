"""Native picker for the explicit document set used by file questions."""

from pathlib import Path

import objc
from AppKit import (NSAlert, NSAppearance, NSAppearanceNameDarkAqua, NSBackingStoreBuffered,
                    NSButton, NSFloatingWindowLevel, NSFont, NSModalResponseOK, NSOpenPanel,
                    NSPanel, NSPopUpButton, NSTextField, NSWindowStyleMaskClosable, NSWindowStyleMaskTitled)
from Foundation import NSObject, NSMakeRect

from file_context import validate_paths


class FileContextPanel(NSObject):
    def initWithPreferences_changed_(self, preferences, changed):
        self = objc.super(FileContextPanel, self).init()
        if self is None:
            return None
        self._preferences, self._changed = preferences, changed
        self._panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(100, 100, 660, 300), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered, False)
        self._panel.setTitle_('Файлы для голосового вопроса')
        self._panel.setReleasedWhenClosed_(False)
        self._panel.setLevel_(NSFloatingWindowLevel + 2)
        self._panel.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        content = self._panel.contentView()
        label = NSTextField.wrappingLabelWithString_(
            'Добавьте материалы, задайте вопрос вслух и нажмите «Вопрос по файлам» (⌥A).\n'
            'Используется звук выбранного устройства. Экран не отправляется.')
        label.setFrame_(NSMakeRect(20, 232, 620, 50))
        content.addSubview_(label)
        self._popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(18, 188, 624, 30), False)
        self._popup.setAccessibilityLabel_('Выбранные контекстные файлы')
        content.addSubview_(self._popup)
        self._info = NSTextField.wrappingLabelWithString_('')
        self._info.setFrame_(NSMakeRect(20, 62, 620, 112))
        self._info.setFont_(NSFont.systemFontOfSize_(12))
        content.addSubview_(self._info)
        for title, action, x, width in [('Добавить файлы…', 'add:', 18, 200),
                                         ('Убрать из контекста', 'remove:', 228, 210),
                                         ('Закрыть', 'close:', 532, 110)]:
            button = NSButton.buttonWithTitle_target_action_(title, self, action)
            button.setFrame_(NSMakeRect(x, 18, width, 32))
            content.addSubview_(button)
        return self

    @objc.python_method
    def refresh(self):
        self._popup.removeAllItems()
        for path in self._preferences.context_files:
            self._popup.addItemWithTitle_(f'{Path(path).name}' + ('' if Path(path).is_file() else ' — недоступен'))
            self._popup.lastItem().setRepresentedObject_(path)
        self._popup.setToolTip_('\n'.join(self._preferences.context_files))
        self._info.setStringValue_(
            f'Выбрано файлов: {len(self._preferences.context_files)}. Выбор сохраняется после перезапуска.\n\n'
            'TXT, Markdown, исходники, CSV/JSON, PDF с текстом и DOCX. До 20 файлов; '
            'общий текст до 300 000 символов. Сканам PDF нужен OCR.\n\n'
            'Текст выбранных файлов и аудиовопрос отправляются в Gemini только при нажатии ⌥A. '
            'Ответ должен содержать ссылки на файл и страницу/строку. Оригиналы не изменяются.')

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
        alert.runModal()

    def add_(self, sender):
        picker = NSOpenPanel.openPanel()
        picker.setAllowsMultipleSelection_(True)
        picker.setCanChooseDirectories_(False)
        picker.setCanChooseFiles_(True)
        picker.setTitle_('Выберите материалы для голосовых вопросов')
        if picker.runModal() != NSModalResponseOK:
            return
        try:
            added = validate_paths([str(url.path()) for url in picker.URLs()])
            paths = list(dict.fromkeys([*self._preferences.context_files, *added]))
            if len(paths) > 20:
                raise ValueError('Можно выбрать не больше 20 файлов.')
            self.save_paths(paths)
        except (OSError, ValueError) as error:
            self.report(error)

    def remove_(self, sender):
        item = self._popup.selectedItem()
        if item is None:
            return
        try:
            self.save_paths([path for path in self._preferences.context_files if path != item.representedObject()])
        except OSError as error:
            self.report(error)

    def close_(self, sender):
        self._panel.orderOut_(None)
