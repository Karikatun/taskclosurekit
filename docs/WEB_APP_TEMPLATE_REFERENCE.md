# Нейтральный пример engineering configuration

[Контракт](../examples/contract-ui-engineering-v21.json) и [presets](../examples/presets-ui-engineering-v21.json) используют placeholder repository/runtime paths и небольшую модульную структуру source/tests. Это конфигурационный пример, а не проверка прикладного проекта. Toolchain выбирает проект через отдельно проверенную конфигурацию; ядро не требует конкретного runtime, фреймворка или workflow. Имя этого документа сохранено для существующих ссылок.

Перед CREATE оператор адаптирует executable/argv, relevant inputs, frozen dispatcher и permitted outputs. Presets/v1 сохраняют строгие limits; v2 policy описана в [контракте интеграции](INTEGRATION.md). Ни один пример не устанавливает tools или dependencies. Реальное поведение проверяют disposable [engineering fixtures](../examples/engineering-fixture/README.md) и integration pilot.
