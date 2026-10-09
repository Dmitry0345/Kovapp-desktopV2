#!/bin/sh
# Симулятор телефона Kovapp (с тестовым сервером; рабочий — --server https://…)
exec python3 "$(dirname "$0")/tools/simulator.py" "$@"
