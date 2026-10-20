#!/bin/bash

cd -- "$(dirname -- "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
    printf 'Не найдено Python-окружение. Выполни установку по README.md в этой папке.\n'
    read -r -p 'Нажми Enter, чтобы закрыть окно…'
    exit 1
fi

.venv/bin/python assistant.py
result=$?
if [ "$result" -ne 0 ]; then
    printf '\nПриложение завершилось с ошибкой. Проверь сообщение выше.\n'
    read -r -p 'Нажми Enter, чтобы закрыть окно…'
fi
exit "$result"
