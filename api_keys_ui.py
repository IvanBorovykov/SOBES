"""Native editor for named Gemini credentials; saved secrets are never displayed."""

import uuid

import objc
from AppKit import (
    NSAlert, NSAlertFirstButtonReturn, NSAppearance, NSAppearanceNameDarkAqua,
    NSBackingStoreBuffered, NSButton, NSControlStateValueOn, NSFloatingWindowLevel,
    NSFont, NSPanel, NSPopUpButton, NSSecureTextField, NSSwitchButton, NSTextField,
    NSWindowStyleMaskClosable, NSWindowStyleMaskTitled,
)
from Foundation import NSObject, NSMakeRect

from api_keys import ApiKey


class ApiKeysPanel(NSObject):
    def initWithPool_saved_(self, pool, saved):
        self = objc.super(ApiKeysPanel, self).init()
        if self is None:
            return None
        self._pool, self._saved = pool, saved
        self._selected = None
        self._panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(100, 100, 660, 454), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered, False,
        )
        self._panel.setTitle_("API-ключи Gemini")
        self._panel.setReleasedWhenClosed_(False)
        self._panel.setLevel_(NSFloatingWindowLevel + 2)
        self._panel.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        content = self._panel.contentView()
        self._popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(18, 400, 460, 28), False)
        self._popup.setTarget_(self)
        self._popup.setAction_("select:")
        self._popup.setAccessibilityLabel_("Сохранённые API-ключи")
        content.addSubview_(self._popup)
        button = NSButton.buttonWithTitle_target_action_("Добавить ключ", self, "add:")
        button.setFrame_(NSMakeRect(484, 398, 158, 32))
        content.addSubview_(button)
        self._fields = {}
        for name, title, y in (("name", "Название ключа", 370),
                               ("project", "Проект Google Cloud (необязательно)", 300),
                               ("secret", "API-ключ", 230)):
            label = NSTextField.labelWithString_(title)
            label.setFrame_(NSMakeRect(20, y, 620, 20))
            content.addSubview_(label)
            field = (NSSecureTextField if name == "secret" else NSTextField).alloc().initWithFrame_(
                NSMakeRect(20, y - 32, 620, 26))
            field.setAccessibilityLabel_(title)
            content.addSubview_(field)
            self._fields[name] = field
        self._fields["secret"].setPlaceholderString_("Вставьте ключ; при редактировании пустое поле сохраняет прежний")
        self._auto = NSButton.buttonWithTitle_target_action_(
            "Автоматически выбирать следующий доступный ключ при лимите", self, "autoSwitch:")
        self._auto.setButtonType_(NSSwitchButton)
        self._auto.setFrame_(NSMakeRect(20, 154, 620, 28))
        content.addSubview_(self._auto)
        self._info = NSTextField.wrappingLabelWithString_("")
        self._info.setFont_(NSFont.systemFontOfSize_(11))
        self._info.setFrame_(NSMakeRect(20, 62, 620, 82))
        content.addSubview_(self._info)
        for title, action, x, width in (("Удалить", "delete:", 18, 110),
                                       ("Закрыть", "close:", 342, 110),
                                       ("Сохранить и использовать", "save:", 458, 184)):
            button = NSButton.buttonWithTitle_target_action_(title, self, action)
            button.setFrame_(NSMakeRect(x, 18, width, 32))
            button.setFont_(NSFont.systemFontOfSize_(11))
            content.addSubview_(button)
        return self

    @objc.python_method
    def refresh(self, selected=None):
        self._popup.removeAllItems()
        for entry in self._pool.entries():
            self._popup.addItemWithTitle_(self._pool.redact(entry.name))
            self._popup.lastItem().setRepresentedObject_(entry.id)
            if entry.id == selected:
                self._popup.selectItem_(self._popup.lastItem())
        self._auto.setState_(NSControlStateValueOn if self._pool.auto_switch else 0)
        self.select_(None)

    @objc.python_method
    def show(self):
        self.refresh(self._pool.active_id)
        self._panel.center()
        self._panel.makeKeyAndOrderFront_(None)

    def select_(self, sender):
        item = self._popup.selectedItem()
        self._selected = item.representedObject() if item else None
        entry = next((entry for entry in self._pool.entries() if entry.id == self._selected), None)
        self._fields["name"].setStringValue_(entry.name if entry else "")
        self._fields["project"].setStringValue_(entry.project if entry else "")
        self._fields["secret"].setStringValue_("")
        self._info.setStringValue_(
            self._pool.status() + "\nКлючи сохраняются в Связке ключей macOS. Остаток квоты не измеряется.\n"
            "Переключение происходит после ошибки лимита, до начала ответа. "
            "Ключи одного проекта делят квоту; одинаковое название проекта объединяет их ожидание."
        )

    def add_(self, sender):
        self._popup.selectItemAtIndex_(-1)
        self._selected = None
        for field in self._fields.values():
            field.setStringValue_("")
        self._fields["name"].setStringValue_(f"Ключ {len(self._pool.entries()) + 1}")
        self._panel.makeFirstResponder_(self._fields["secret"])

    @objc.python_method
    def report(self, error):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("API-ключи не изменены")
        alert.setInformativeText_(self._pool.redact(str(error)))
        alert.runModal()

    def save_(self, sender):
        entries = self._pool.entries()
        old = next((entry for entry in entries if entry.id == self._selected), None)
        secret = self._fields["secret"].stringValue().strip() or (old.secret if old else "")
        entry = ApiKey(self._selected or uuid.uuid4().hex, self._fields["name"].stringValue().strip(),
                       secret, self._fields["project"].stringValue().strip())
        updated = [entry if item.id == entry.id else item for item in entries]
        if old is None:
            updated.append(entry)
        try:
            self._pool.replace(updated, entry.id, self._auto.state() == NSControlStateValueOn)
        except (ValueError, OSError) as error:
            self.report(error)
            return
        self._fields["secret"].setStringValue_("")
        self._saved()
        self.refresh(entry.id)

    def delete_(self, sender):
        if not self._selected:
            return
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Удалить выбранный API-ключ из приложения?")
        alert.addButtonWithTitle_("Удалить")
        alert.addButtonWithTitle_("Отмена")
        if alert.runModal() != NSAlertFirstButtonReturn:
            return
        entries = [entry for entry in self._pool.entries() if entry.id != self._selected]
        active = self._pool.active_id
        if active == self._selected:
            active = entries[0].id if entries else None
        try:
            self._pool.replace(entries, active, self._auto.state() == NSControlStateValueOn)
        except (ValueError, OSError) as error:
            self.report(error)
            return
        self._saved()
        self.refresh(active)

    def autoSwitch_(self, sender):
        try:
            self._pool.replace(self._pool.entries(), self._pool.active_id,
                               self._auto.state() == NSControlStateValueOn)
        except (ValueError, OSError) as error:
            self._auto.setState_(NSControlStateValueOn if self._pool.auto_switch else 0)
            self.report(error)

    def close_(self, sender):
        self._fields["secret"].setStringValue_("")
        self._panel.orderOut_(None)
