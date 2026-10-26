"""Native Windows Tk interface over the shared assistant and streaming pipeline."""

import os
import tkinter as tk
import uuid
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from api_keys import ApiKey
from file_context import SUPPORTED_TYPES, folder_paths, validate_paths
from windows_platform import (AppHelper, Hotkeys, exclude_from_capture, get_displays,
                              place_window)


class Overlay:
    def __init__(self, root):
        self.window = tk.Toplevel(root)
        self.window.title('SOBES — ответ')
        self.window.geometry('560x600')
        self.window.attributes('-topmost', True)
        self.window.protocol('WM_DELETE_WINDOW', self.hide)
        self.text = ScrolledText(self.window, wrap='word', font=('Consolas', 11),
                                 background='#10151e', foreground='#eef3fa',
                                 insertbackground='white', padx=14, pady=14, state='disabled')
        self.text.pack(fill='both', expand=True)
        self._follow = False

    def show_text(self, text, reset_scroll=False):
        previous = self.text.yview()[0]
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', text.replace('⌥', 'Alt+'))
        self.text.configure(state='disabled')
        if reset_scroll:
            self._follow = False
            self.text.yview_moveto(0)
        else:
            self.text.yview_moveto(1 if self._follow else previous)
        self.window.deiconify()

    def hide(self):
        self.window.withdraw()

    def scroll(self, distance):
        self._follow = distance > 0
        self.text.yview_scroll(6 if distance > 0 else -6, 'units')


class WindowsApp:
    def __init__(self, core, preferences=None, keys=None, audio=None, start_realtime=True):
        self.core = core
        self.root = tk.Tk()
        self.root.title('SOBES — Windows')
        self.root.geometry('680x440')
        self.root.minsize(560, 380)
        self.root.attributes('-topmost', True)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.closed = False
        self._remember_job = None
        self._tick_job = None
        self._open_keys_job = None
        self._ticks = 0
        self._dialogs = {}
        self.hotkeys = None
        self.audio = self.assistant = None
        self.preferences = preferences if preferences is not None else core.Preferences()
        try:
            self.keys = keys if keys is not None else core.KeyPool(core.KeychainStore(),
                os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY'))
            self.overlay = Overlay(self.root)
            self.audio = audio if audio is not None else core.AudioRing(self.preferences.audio_source)
            self.assistant = core.Assistant(self.overlay, self.audio, self.keys)
            self.assistant.context_files = tuple(self.preferences.context_files)
            self.audio.on_status = lambda: AppHelper.callAfter(self.update_status)
            self.keys.on_change = lambda: AppHelper.callAfter(self.update_status)
            self._build_toolbar()
            self.displays = get_displays()
            if self.preferences.capture_display is None or 'MONITOR' in os.environ:
                index = core.MONITOR_INDEX - 1
                self.preferences.capture_display = (self.displays[index]['key'] if 0 <= index < len(self.displays)
                                                    else f'missing-monitor:{index + 1}')
            self.restore_layout()
            self._display_signature = self.display_signature()
            self.capture_protected = all([exclude_from_capture(self.root), exclude_from_capture(self.overlay.window)])
            self.hotkeys = Hotkeys({'A': self.assistant.ask_files,
                'Q': lambda: self.assistant.analyze(live=True, audio_only=True),
                'Z': lambda: self.assistant.analyze(live=True), 'X': self.assistant.analyze})
            if start_realtime:
                self.assistant.start_realtime(lambda: AppHelper.callAfter(self.update_status))
            self.overlay.show_text('Готов к работе.\n\nAlt+A — вопрос по выбранным файлам\n'
                'Alt+Q — вопрос голосом с историей диалога\nAlt+Z — короткая подсказка по экрану\n'
                'Alt+X — подробный ответ / код\n\nВыберите микрофон или звук компьютера в настройках.\n'
                'При включённом потоке звук передаётся Gemini. Кнопка «Пауза потока» останавливает передачу.\n'
                'Снимки экрана отправляются при Alt+Z, Alt+X или «Только экран».\n'
                'Закрытие панели кнопок полностью завершает приложение.', reset_scroll=True)
            self.root.bind('<Configure>', self._schedule_remember, add='+')
            self.overlay.window.bind('<Configure>', self._schedule_remember, add='+')
            self.root.bind('<Control-comma>', lambda event: self.open_settings())
            self.root.bind('<Control-q>', lambda event: self.close())
            self.update_status()
            self._tick_job = self.root.after(20, self._tick)
            if not self.keys.entries():
                self._open_keys_job = self.root.after(100, self.open_keys)
        except Exception:
            if self.audio is not None:
                self.audio.close()
            if self.assistant is not None:
                self.assistant.close()
            if self.hotkeys is not None:
                self.hotkeys.close()
            self.root.destroy()
            raise

    def _build_toolbar(self):
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill='both', expand=True)
        buttons = [('Вопрос по файлам · Alt+A', self.assistant.ask_files),
                   ('Файлы…', self.open_files),
                   ('Только звук · Alt+Q', lambda: self.assistant.analyze(live=True, audio_only=True)),
                   ('Подсказка · Alt+Z', lambda: self.assistant.analyze(live=True)),
                   ('Ответ / код · Alt+X', self.assistant.analyze),
                   ('Только экран', lambda: self.assistant.analyze(screen_only=True)),
                   ('Очистить', self.assistant.clear), ('Настройки…', self.open_settings),
                   ('↑ Прокрутить', lambda: self.overlay.scroll(-120)),
                   ('↓ Прокрутить', lambda: self.overlay.scroll(120))]
        for index, (title, callback) in enumerate(buttons):
            ttk.Button(frame, text=title, command=callback).grid(row=index // 2, column=index % 2,
                                                               sticky='ew', padx=3, pady=3)
        self.stream_button = ttk.Button(frame, text='Пауза потока', command=self.toggle_streaming)
        self.stream_button.grid(row=5, column=0, sticky='ew', padx=3, pady=3)
        ttk.Button(frame, text='API-ключи…', command=self.open_keys).grid(row=5, column=1, sticky='ew', padx=3, pady=3)
        ttk.Button(frame, text='Закрыть полностью', command=self.close).grid(row=6, column=0, columnspan=2,
                                                                          sticky='ew', padx=3, pady=3)
        self.status = tk.StringVar()
        ttk.Label(frame, textvariable=self.status, wraplength=630, justify='left').grid(
            row=7, column=0, columnspan=2, sticky='nw', padx=3, pady=8)
        frame.columnconfigure((0, 1), weight=1)

    def _tick(self):
        self._tick_job = None
        if self.closed:
            return
        try:
            AppHelper.drain()
            self.hotkeys.poll()
            self._ticks += 1
            if self._ticks % 100 == 0:
                signature = self.display_signature()
                if signature != self._display_signature:
                    self._display_signature = signature
                    self.restore_layout()
                self.update_status()
        finally:
            if not self.closed:
                self._tick_job = self.root.after(20, self._tick)

    def display_signature(self):
        return tuple((d['key'], tuple(d['work'].values())) for d in get_displays())

    def restore_layout(self):
        self.displays = get_displays()
        if not self.displays:
            self.assistant.capture_display_id = -1
            return
        by_key = {display['key']: display for display in self.displays}
        for kind, window in (('controls', self.root), ('overlay', self.overlay.window)):
            display = by_key.get(getattr(self.preferences, f'{kind}_display'), self.displays[0])
            setattr(self.preferences, f'{kind}_display', display['key'])
            work = display['work']
            window.update_idletasks()
            width = min(window.winfo_width(), work['width'])
            height = min(window.winfo_height(), max(150, work['height'] - 40))
            window.geometry(f'{width}x{height}')
            origin = getattr(self.preferences, f'{kind}_origin')
            x, y = origin if origin else (work['left'] + (20 if kind == 'controls' else work['width'] - width - 20),
                                         work['top'] + (20 if kind == 'controls' else 80))
            x = max(work['left'], min(x, work['left'] + work['width'] - width))
            y = max(work['top'], min(y, work['top'] + work['height'] - height - 35))
            place_window(window, x, y)
        capture = by_key.get(self.preferences.capture_display)
        self.assistant.capture_display_id = capture['id'] if capture else -1
        self.audio.set_seconds(self.preferences.audio_seconds)

    def _schedule_remember(self, event=None):
        if self.closed or (event is not None and event.widget not in (self.root, self.overlay.window)):
            return
        if self._remember_job:
            self.root.after_cancel(self._remember_job)
        self._remember_job = self.root.after(400, self.remember_windows)

    def remember_windows(self):
        if self._remember_job is not None:
            self.root.after_cancel(self._remember_job)
        self._remember_job = None
        displays = get_displays()
        for kind, window in (('controls', self.root), ('overlay', self.overlay.window)):
            if window.state() != 'normal':
                continue
            x, y = window.winfo_x(), window.winfo_y()
            setattr(self.preferences, f'{kind}_origin', [x, y])
            center_x, center_y = x + window.winfo_width() / 2, y + window.winfo_height() / 2
            for display in displays:
                bounds = display['region']
                if (bounds['left'] <= center_x < bounds['left'] + bounds['width']
                        and bounds['top'] <= center_y < bounds['top'] + bounds['height']):
                    setattr(self.preferences, f'{kind}_display', display['key'])
                    break
        try:
            self.preferences.save()
        except OSError as error:
            print(f'Не удалось сохранить положение окон: {error}')

    def update_status(self):
        if self.closed:
            return
        capture = next((d['title'] for d in get_displays() if d['key'] == self.preferences.capture_display), 'экран отключён')
        realtime = self.assistant._realtime
        enabled = realtime is not None and realtime.enabled
        self.stream_button.configure(text='Пауза потока' if enabled else 'Включить поток')
        lines = [f'Экран: {capture} · файлов: {len(self.preferences.context_files)}',
                 f'Аудио: {self.audio.error or self.audio.device_name} · {self.preferences.audio_seconds} с',
                 self.keys.status(), realtime.status() if realtime else 'Поток: остановлен']
        if not self.capture_protected:
            lines.append('Windows не подтвердил исключение окон из захвата.')
        if self.hotkeys:
            lines.extend(self.hotkeys.errors)
        self.status.set('\n'.join(lines))

    def toggle_streaming(self):
        self.assistant.toggle_realtime()
        self.update_status()

    def dialog(self, key, title, geometry):
        existing = self._dialogs.get(key)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_set()
            return None
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry(geometry)
        window.attributes('-topmost', True)
        self._dialogs[key] = window
        exclude_from_capture(window)
        return window

    def report(self, error, parent=None):
        messagebox.showerror('SOBES', self.keys.redact(str(error)), parent=parent or self.root)

    def open_settings(self):
        window = self.dialog('settings', 'SOBES — настройки', '650x490')
        if window is None:
            return
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill='both', expand=True)
        displays = get_displays()
        values = {}
        for name, title in (('capture_display', 'Экран для снимков'), ('overlay_display', 'Окно ответа'),
                            ('controls_display', 'Панель кнопок')):
            ttk.Label(frame, text=title).pack(anchor='w', pady=(6, 0))
            current = next((d['title'] for d in displays if d['key'] == getattr(self.preferences, name)), 'Экран отключён — выберите')
            variable = values[name] = tk.StringVar(value=current)
            ttk.Combobox(frame, textvariable=variable, values=[d['title'] for d in displays], state='readonly').pack(fill='x')
        ttk.Label(frame, text='Что слушать').pack(anchor='w', pady=(8, 0))
        source = tk.StringVar(value='Звук компьютера' if self.preferences.audio_source == 'system' else 'Микрофон')
        ttk.Combobox(frame, textvariable=source, values=['Микрофон', 'Звук компьютера'], state='readonly').pack(fill='x')
        ttk.Label(frame, text='Последние секунды аудио (1–60)').pack(anchor='w', pady=(8, 0))
        seconds = tk.StringVar(value=str(self.preferences.audio_seconds))
        ttk.Spinbox(frame, textvariable=seconds, from_=1, to=60, width=8).pack(anchor='w')
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=10)
        ttk.Button(row, text='↑ Выше', command=lambda: self.move_overlay(-80)).pack(side='left', padx=3)
        ttk.Button(row, text='↓ Ниже', command=lambda: self.move_overlay(80)).pack(side='left', padx=3)
        ttk.Button(row, text='Сбросить положение', command=lambda: self.move_overlay(None)).pack(side='left', padx=3)
        ttk.Label(frame, text='Звук компьютера: текущее устройство вывода Windows.\n'
                  'После смены наушников / динамиков нажмите «Применить».', wraplength=590).pack(anchor='w')

        def apply():
            try:
                current = {d['title']: d for d in get_displays()}
                if any(var.get() not in current for var in values.values()):
                    raise ValueError('Выберите подключённый экран в каждом списке.')
                duration = int(seconds.get())
                if not 1 <= duration <= 60:
                    raise ValueError('Длительность аудио должна быть от 1 до 60 секунд.')
                new_source = 'system' if source.get() == 'Звук компьютера' else 'microphone'
                # Explicit Apply reconnects the selected output, including after a device change.
                self.assistant.clear()
                self.audio.close()
                self.audio.source = None
                self.audio.set_source(new_source)
                self.assistant.clear()
                self.preferences.audio_source = new_source
                self.preferences.audio_seconds = duration
                for name, variable in values.items():
                    key = current[variable.get()]['key']
                    if name != 'capture_display' and key != getattr(self.preferences, name):
                        setattr(self.preferences, name.replace('_display', '_origin'), None)
                    setattr(self.preferences, name, key)
                self.restore_layout()
                self.preferences.save()
                self.update_status()
            except (OSError, ValueError) as error:
                self.report(error, window)

        ttk.Button(frame, text='Применить', command=apply).pack(side='right', padx=3, pady=8)
        ttk.Button(frame, text='Закрыть', command=window.destroy).pack(side='right', padx=3, pady=8)

    def move_overlay(self, distance):
        if distance is None:
            self.preferences.overlay_origin = None
        else:
            window = self.overlay.window
            self.preferences.overlay_origin = [window.winfo_x(), window.winfo_y() + distance]
        self.restore_layout()
        self.overlay.window.deiconify()
        self._schedule_remember()

    def open_keys(self):
        window = self.dialog('keys', 'SOBES — API-ключи Gemini', '660x610')
        if window is None:
            return
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Сохранённые профили — выберите существующий или добавьте новый').pack(anchor='w', pady=(0, 4))
        listing = ttk.Combobox(frame, state='readonly', takefocus=False)
        listing.pack(fill='x')
        fields = {}
        inputs = {}
        for name, title in (('name', 'Название'), ('project', 'Проект Google Cloud (необязательно)'),
                            ('secret', 'ВСТАВЬТЕ API-КЛЮЧ СЮДА (при редактировании можно оставить пустым)')):
            ttk.Label(frame, text=title).pack(anchor='w', pady=(9, 0))
            fields[name] = tk.StringVar()
            inputs[name] = ttk.Entry(frame, textvariable=fields[name], show='•' if name == 'secret' else '')
            inputs[name].pack(fill='x')

        def paste_secret():
            try:
                value = window.clipboard_get().strip()
            except tk.TclError:
                self.report(ValueError('В буфере обмена нет текста. Скопируйте API-ключ в Google AI Studio.'), window)
                return
            fields['secret'].set(value)
            inputs['secret'].focus_set()
            inputs['secret'].icursor('end')

        def control_key(event):
            # Match the physical V key so paste also works with Cyrillic layouts.
            if event.keycode == 86:
                paste_secret()
                return 'break'

        inputs['secret'].bind('<Control-KeyPress>', control_key)
        ttk.Button(frame, text='Вставить ключ из буфера', command=paste_secret).pack(anchor='w', pady=(6, 0))
        automatic = tk.BooleanVar(value=self.keys.auto_switch)
        info = tk.StringVar()
        selected = [None]
        entries = []

        def select(event=None):
            index = listing.current() - 1
            entry = entries[index] if 0 <= index < len(entries) else None
            selected[0] = entry.id if entry else None
            fields['name'].set(entry.name if entry else f'Ключ {len(entries) + 1}')
            fields['project'].set(entry.project if entry else '')
            fields['secret'].set('')

        def refresh(active=None):
            entries[:] = self.keys.entries()
            listing.configure(values=['Новый ключ — вставьте API-ключ в поле ниже'] + [
                e.name + (' (активный)' if e.id == self.keys.active_id else '') for e in entries])
            index = next((i + 1 for i, entry in enumerate(entries) if entry.id == active), 1 if entries else 0)
            listing.current(index)
            select()
            info.set(self.keys.status())
            self.update_status()

        def changed():
            self.assistant.credentials_changed()
            refresh(self.keys.active_id)

        def save():
            old = next((e for e in entries if e.id == selected[0]), None)
            entry = ApiKey(selected[0] or uuid.uuid4().hex, fields['name'].get().strip(),
                           fields['secret'].get().strip() or (old.secret if old else ''), fields['project'].get().strip())
            updated = [entry if e.id == entry.id else e for e in entries]
            if old is None:
                updated.append(entry)
            try:
                self.keys.replace(updated, entry.id, automatic.get())
                fields['secret'].set('')
                changed()
            except (OSError, ValueError) as error:
                self.report(error, window)

        def delete():
            if selected[0] and messagebox.askyesno('SOBES', 'Удалить выбранный ключ из приложения?', parent=window):
                remaining = [e for e in entries if e.id != selected[0]]
                active = self.keys.active_id if self.keys.active_id != selected[0] else (remaining[0].id if remaining else None)
                try:
                    self.keys.replace(remaining, active, automatic.get())
                    changed()
                except (OSError, ValueError) as error:
                    self.report(error, window)

        def auto_change():
            try:
                self.keys.replace(self.keys.entries(), self.keys.active_id, automatic.get())
                self.update_status()
            except (OSError, ValueError) as error:
                automatic.set(self.keys.auto_switch)
                self.report(error, window)

        listing.bind('<<ComboboxSelected>>', select)
        ttk.Checkbutton(frame, text='Автоматически переключать ключи при лимите', variable=automatic,
                        command=auto_change).pack(anchor='w', pady=10)
        ttk.Label(frame, text='Ключи зашифрованы DPAPI для текущего пользователя Windows.\n'
                  'Ключи одного проекта делят квоту. Остаток квоты не измеряется.', wraplength=610).pack(anchor='w')
        ttk.Label(frame, textvariable=info, wraplength=610).pack(anchor='w', pady=8)
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=10)

        def add():
            listing.current(0)
            select()
            inputs['secret'].focus_set()

        for title, callback in (('Добавить ключ', add), ('Сохранить и использовать', save), ('Удалить', delete)):
            ttk.Button(row, text=title, command=callback).pack(side='left', padx=3)
        ttk.Button(frame, text='Закрыть', command=window.destroy).pack(anchor='e')
        refresh(self.keys.active_id)
        window.update_idletasks()
        inputs['secret'].focus_force()

    def open_files(self):
        window = self.dialog('files', 'SOBES — контекст файлов', '780x430')
        if window is None:
            return
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill='both', expand=True)
        listing = tk.Listbox(frame, selectmode='extended', exportselection=False)
        listing.pack(fill='both', expand=True)
        ttk.Label(frame, text='До 20 файлов. Ctrl / Shift — выбрать несколько. Полный текст отправляется при Alt+A.').pack(anchor='w', pady=8)

        def refresh():
            listing.delete(0, 'end')
            for path in self.preferences.context_files:
                listing.insert('end', path)
            self.assistant.context_files = tuple(self.preferences.context_files)
            self.assistant._generation += 1
            self.update_status()

        def commit(paths):
            validated = list(validate_paths(paths))
            previous = self.preferences.context_files
            self.preferences.context_files = validated
            try:
                self.preferences.save()
            except OSError:
                self.preferences.context_files = previous
                raise
            refresh()

        def add_paths(paths):
            try:
                combined = list(dict.fromkeys([*self.preferences.context_files, *paths]))
                commit(combined)
            except (OSError, ValueError) as error:
                self.report(error, window)

        def choose_files():
            paths = filedialog.askopenfilenames(parent=window, title='Выберите материалы',
                filetypes=[('Документы и исходники', ' '.join('*' + suffix for suffix in sorted(SUPPORTED_TYPES))), ('Все файлы', '*.*')])
            if paths:
                add_paths(paths)

        def choose_folder():
            directory = filedialog.askdirectory(parent=window)
            if not directory:
                return
            try:
                candidates = folder_paths(directory)
            except (OSError, ValueError) as error:
                self.report(error, window)
                return
            picker = tk.Toplevel(window)
            picker.title('Выберите файлы из папки')
            picker.geometry('780x400')
            picker.attributes('-topmost', True)
            candidate_list = tk.Listbox(picker, selectmode='extended', exportselection=False)
            candidate_list.pack(fill='both', expand=True, padx=12, pady=12)
            for path in candidates:
                candidate_list.insert('end', path)
            ttk.Label(picker, text=f'Найдено: {len(candidates)}. Выберите до 20 файлов.').pack(anchor='w', padx=12)
            ttk.Button(picker, text='Выбрать все', command=lambda: candidate_list.selection_set(0, 'end')).pack(side='left', padx=12, pady=10)

            def add_selected():
                paths = [candidates[i] for i in candidate_list.curselection()]
                if paths:
                    add_paths(paths)
                    picker.destroy()

            ttk.Button(picker, text='Добавить выбранные', command=add_selected).pack(side='right', padx=12, pady=10)

        def remove(all_files=False):
            selected = set(listing.curselection())
            try:
                # Removing missing files must not require re-validating the remaining paths.
                previous = self.preferences.context_files
                self.preferences.context_files = [] if all_files else [p for i, p in enumerate(previous) if i not in selected]
                try:
                    self.preferences.save()
                except OSError:
                    self.preferences.context_files = previous
                    raise
                refresh()
            except OSError as error:
                self.report(error, window)

        row = ttk.Frame(frame)
        row.pack(fill='x')
        for title, callback in (('Добавить…', choose_files), ('Из папки…', choose_folder),
                                ('Убрать выбранные', remove), ('Убрать все', lambda: remove(True))):
            ttk.Button(row, text=title, command=callback).pack(side='left', padx=3)
        ttk.Button(row, text='Закрыть', command=window.destroy).pack(side='right', padx=3)
        refresh()

    def close(self):
        if self.closed:
            return
        self.remember_windows()
        self.closed = True
        for job in (self._tick_job, self._remember_job, self._open_keys_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
        if self.hotkeys:
            self.hotkeys.close()
        self.audio.close()
        self.assistant.close()
        self.root.destroy()


def run(core):
    try:
        app = WindowsApp(core)
    except (OSError, ValueError, tk.TclError) as error:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('SOBES — ошибка запуска', str(error), parent=root)
        root.destroy()
        raise
    try:
        app.root.mainloop()
    except KeyboardInterrupt:
        app.close()
