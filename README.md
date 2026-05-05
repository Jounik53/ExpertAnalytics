# ExpertAnalytics

ExpertAnalytics — Windows-first инструмент диагностики утечек памяти и подозрительной активности.

## Новые функции
- Сборка в EXE из UI (кнопка "Собрать EXE")
- Эвристический движок обнаружения подозрительных процессов/майнеров
- Отдельное меню эвристик с включением/выключением правил
- Персистентные события UI (как журнал ОС)

## Сборка EXE
### Через UI
1. Откройте приложение.
2. Нажмите кнопку "Собрать EXE".
3. Готовый файл: `dist/ExpertAnalytics/ExpertAnalytics.exe`.

### Через командную строку
```powershell
pip install -r requirements.txt
pyinstaller --noconfirm --windowed --name ExpertAnalytics main.py
```

## Эвристики
Правила находятся в `app/application/services/heuristics_service.py`.
Поддерживаются базовые правила:
- подозрительный путь запуска
- ключевые слова майнера
- высокая CPU+RAM нагрузка
- необычное расположение бинарника

Каждое правило можно отключить/включить в UI на вкладке "Эвристики".

## Docker
```bash
docker build -t expertanalytics .
docker compose up --build
```

## Тесты
```powershell
pytest -q
```
