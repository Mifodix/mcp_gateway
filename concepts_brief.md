# MCP Gateway

* **Author:** Не указан
* **Date:** 2026-11-20
* **Status:** Обновлено после изменения модели идентичности и выбора FastMCP; ожидается повторное подтверждение автором. Runtime proof-of-concept остаётся обязательной проверкой перед реализацией.

## Overview

Отдельный сервис для существующих многопользовательских agent harnesses, запущенных в Kubernetes. Gateway поддерживает только последнюю stateless-версию MCP, динамически подключает десятки MCP-серверов с тысячами tools и добавляет к имени каждого tool префикс имени предоставляющего его сервера.

Gateway имеет три основные функции:

1. Выполнение MCP-tools с передачей исходных end-to-end HTTP-заголовков с идентификаторами и контекстом в upstream MCP-сервер без изменений. Секретный пользовательский токен и `token_version` не используются. Transport hop-by-hop заголовки (`Host`, `Content-Length`, `Connection` и аналогичные) формируются заново по правилам HTTP и не копируются механически.
2. Постепенное раскрытие каталога tools: семантический поиск через внешний OpenAI-compatible embedder и получение описания конкретного tool. Для поиска достаточно полного перебора векторов; HNSW не требуется.
3. Асинхронный Python code mode на отдельных Linux-хостах с microVM, сохранением полного состояния интерпретатора и MCP-клиентом внутри VM. Runtime по умолчанию — microsandbox; Firecracker допускается только как fallback при выявлении проблем с microsandbox.

MCP-facing часть переиспользует FastMCP под Apache-2.0: внешние серверы регистрируются через Proxy Provider, а собственный scheduler обновляет их списки tools. Endpoints управления серверами, семантический индекс и code mode являются собственными компонентами gateway.

## Analysis

### Статус Goldfish review

| Reviewer | Направление | Статус |
| --- | --- | --- |
| `egm-design` | Архитектура, стоимость, квоты | completed |
| `egm-codebase-details` | Наблюдаемость и диагностика | completed |
| `egm-zoom-out` | Упрощение, границы продукта, reuse | completed |
| `egm-comprehension` | API, ясность, противоречия | completed |
| `egm-implementation-readiness` | Совместимость и готовность к rollout | completed |
| `egm-structural` | Границы компонентов и tech debt | completed |
| `egm-inventive-catalyst` | Разрешение противоречий | completed после одного retry |

Первичный синтезатор был остановлен ограничением запросов к провайдеру. Дедупликация и синтез выполнены основным агентом по фактическим отчётам всех reviewers; ни один обзор не засчитан как completed без итогового отчёта.

### Согласованные требования, которые не меняются по рекомендации reviewers

#### Каталог и поиск

* На первом этапе каждому пользователю и чату доступны все серверы и tools. Позже набор будет определять внешний сервис авторизации.
* Внешняя доверенная система добавляет и удаляет MCP-серверы через endpoints gateway. Внутри gateway используется [FastMCP Proxy Provider](https://github.com/PrefectHQ/fastmcp/blob/main/docs/servers/providers/proxy.mdx) для регистрации upstream, proxying и стандартного префиксования tools; FastMCP распространяется под Apache-2.0.
* Собственный scheduler периодически вызывает `tools/list` через FastMCP; период настраивается. По результатам обновляются доступный каталог и семантический поисковый индекс.
* При ошибке `tools/list` scheduler делает tools сервера недоступными и удаляет их из доступного каталога и индекса, но **не удаляет FastMCP-регистрацию сервера**. На последующих циклах он продолжает refresh и возвращает tools после успешного получения списка. Внешняя система остаётся единственным владельцем добавления и удаления регистрации сервера.
* После обнаружения удаления tool новые вызовы отклоняются, а уже выполняющиеся продолжаются.
* Недоступность embedder отключает только семантический поиск. Вызовы tools и code mode продолжаются.
* Индекс привязан к уникальному имени embedding-модели. При смене модели он полностью перестраивается, старый индекс удаляется. Семантический поиск является обязательным требованием; lexical search не является его заменой.

#### Идентичность и code mode

* Чат персонален. Основной агент и каждый сабагент имеют отдельную microVM.
* `run id` обозначает run конкретного агента или сабагента и уникален в пределах чата. Доверенный сервис передаёт стабильный `user id` отдельным полем через harness. Поэтому `(стабильный user id, chat id, run id)` однозначно адресует VM; замечание reviewers о недостающем agent id снимается этим определением и должно быть внесено в glossary.
* Задачи внутри каждой VM выполняются последовательно. Асинхронность относится к submit/poll API, а не к параллельному исполнению в одном интерпретаторе.
* Одна задача равна одному фрагменту кода, отправленному за один submit. После завершения фрагмента, если Python-процесс и VM работоспособны, создаётся новый snapshot полного состояния. Исключение пользовательского кода является результатом задачи и не мешает snapshot.
* На VM хранится только snapshot последнего выполненного фрагмента: он содержит значения переменных и диск после всех предыдущих фрагментов. Прежний snapshot сохраняется до durable-фиксации нового и только затем удаляется. Результат фрагмента становится доступен для polling только после durable-фиксации результата и нового snapshot во внешнем хранилище.
* Внешняя система управляет логическим временем жизни VM. Удаление чата удаляет все его VM.
* После простоя VM освобождает CPU и RAM, а при новой задаче восстанавливается из snapshot. Полное логическое состояние разрешено хранить как базовый образ и incremental/COW deltas памяти и диска; snapshot завершён только после durable-фиксации всех необходимых ссылок и deltas в S3 и PostgreSQL.
* Образ VM содержит стабильный Python, заранее выбранные библиотеки и MCP-клиент. Доступ в Интернет и Python-репозитории закрыт. Tools вызываются только через gateway.
* Agent harness может отменять ожидающие и выполняющиеся задачи и задаёт предельное время исполнения каждой задачи. Таймаут ограничивает только фактическую работу Python-кода: ожидание в очереди, восстановление VM и последующая фиксация snapshot в него не входят. Срок доступности результата для опроса настраивается.
* После отмены по запросу или таймауту VM восстанавливается из snapshot, созданного непосредственно перед текущим фрагментом. Частичные внутренние изменения этого фрагмента не сохраняются, а состояние всех предыдущих фрагментов остаётся. Внешние side effects уже выполненных MCP-вызовов не откатываются.
* Автоматического повторного выполнения прерванного кода нет. Последний успешный snapshot и очередь ещё не выполненного кода должны переживать отказ Linux-хоста и gateway.

#### Существующая инфраструктура

* S3 и PostgreSQL уже доступны. Допускается развёртывание новых open-source зависимостей, если их лицензии разрешают коммерческое использование; каждую лицензию нужно проверить отдельно. PostgreSQL также разрешено использовать как durable-очередь задач без обязательного отдельного queue service. KMS не требуется, поскольку секретный пользовательский токен отсутствует.

#### Идентификаторы и доверие

* Agent harness аутентифицируется через внешний доверенный сервис и передаёт по HTTPS только `user_id`, `chat_id`, `run_id` и другие идентификаторы. Секретный пользовательский токен и `token_version` не используются. Клиенты считаются доверенными; подделка ими заголовков исключена из текущей модели угроз.
* Набор идентификаторов внутри существующей VM-сессии неизменен. Исходные end-to-end HTTP-заголовки передаются в upstream MCP-сервер без изменений. Transport hop-by-hop заголовки формируются заново и не входят в это правило.
* Идентификаторы и исходные заголовки сохраняются в session registry для вызовов из VM, но не попадают в VM или host relay. MCP-клиент внутри VM получает только результат MCP-вызова. Внутренний `vm_session_id` в upstream не передаётся.
* Целевой MCP-сервер работает в доверенной среде и не обязан независимо удостоверять подлинность и полномочия вызова. Корректность идентификаторов и контроль динамической регистрации серверов являются основной границей безопасности.
* Gateway не получает и не хранит секреты, поэтому KMS, envelope-шифрование и crypto-shredding не требуются.

### Основные противоречия и риски

#### 1. Решено: одна задача равна одному фрагменту

Reviewers `egm-design`, `egm-comprehension`, `egm-structural` и `egm-inventive-catalyst` независимо нашли противоречие между snapshot после каждого «фрагмента кода» и откатом всех частичных изменений «задачи».

Автор уточнил модель: каждый submit создаёт отдельную задачу-фрагмент. Snapshot после выполненного фрагмента содержит всё состояние предыдущих фрагментов, поэтому хранить историю snapshots не нужно. Отмена или таймаут откатывает только текущий фрагмент к snapshot непосредственно перед ним. Противоречие снято; committed/speculative поколения внутри одной задачи не требуются. Прежний snapshot удаляется только после durable-фиксации нового.

#### 2. Выбрана базовая топология durable state

Gateway использует stateless MCP-протокол, но система обязана сохранять идентификаторы сессий, очереди, placement VM, snapshots, задачи и их результаты. Каталог и индекс также требуют согласования между репликами. Если оставить это состояние в памяти одной реплики, требования переживания отказа не выполняются.

Рекомендуемое направление — stateless compute tier и явный stateful control plane с внешними хранилищами:

* transactional store для VM-сессий, placement, задач, очередей и результатов;
* session registry с неизменяемыми идентификаторами и исходными заголовками;
* durable object storage для snapshots и локальный cache на Linux-хостах;
* каталог как источник истины, поисковый индекс как производный rebuildable artifact.

Enqueue должен быть durable до ответа `accepted`. Placement и dispatch требуют lease/fencing, чтобы две реплики или два хоста не исполняли одну VM-сессию одновременно. Внешние зависимости добавляют сетевые hops и собственные требования HA; это цена требуемой durability, а не необязательная оптимизация.

Имеющиеся S3 и PostgreSQL выбраны: S3 для snapshot blobs, PostgreSQL для session/task/queue/placement/context metadata и durable-очереди; отдельный queue service не требуется. Атомарной транзакции между S3 и PostgreSQL нет, поэтому результат нельзя фиксировать одновременно с загрузкой. Предлагаемый порядок: загрузить новый snapshot во временный S3 object и проверить checksum; затем в одной PostgreSQL-транзакции записать committed manifest, результат и новую placement/generation-версию; только после commit открыть результат и удалить прежний snapshot. Брошенные временные объекты и старые blobs удаляет фоновый GC.

#### 3. Допускаются incremental/COW snapshots; restore SLO требует spike

Автор разрешил хранить полное логическое состояние как базовый образ и incremental/COW deltas памяти и диска. Snapshot считается завершённым только после durable-фиксации всех необходимых ссылок и deltas в S3 и PostgreSQL. Это устраняет требование полной физической копии после каждой задачи, но не доказывает выполнимость restore SLO. Цена формата — цепочки поколений, compaction, integrity checks и более сложное восстановление.

До production-подтверждения microsandbox требуется spike; Firecracker проверяется как fallback только при выявлении проблем с microsandbox:

* сохранение и восстановление полного Python/interpreter state с реальным объёмом heap;
* размер и время snapshot, включая внешнюю durable-запись;
* warm restore на исходном хосте и cold restore после его потери;
* поддержка incremental/diff snapshots, copy-on-write и lazy loading выбранным runtime;
* поведение при отмене, падении VM и повреждении snapshot.

Цель «до секунды желательно, несколько секунд допустимо» относится к warm restore на исходном хосте и должна быть привязана к размеру snapshot. После потери хоста cold restore может занимать больше времени; числовой cold SLO пока не задан и должен измеряться отдельно. Инкрементальные snapshots и warm cache уменьшают объёмы, но добавляют цепочки поколений, compaction, проверку целостности и стоимость локального хранения.

##### Сравнение microVM runtime

| Кандидат | Подтверждённые возможности | Ограничения и работа gateway | Позиция |
| --- | --- | --- | --- |
| **Firecracker** | [Apache-2.0](https://raw.githubusercontent.com/firecracker-microvm/firecracker/main/LICENSE). [Snapshot API](https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/snapshot-support.md) сохраняет guest memory и VM state, поддерживает full и diff snapshots, on-demand mmap/COW loading. [Virtio-vsock](https://github.com/firecracker-microvm/firecracker/blob/main/docs/vsock.md) связывает AF_VSOCK в guest с AF_UNIX на хосте. | Diff snapshots остаются developer preview. Disk blobs управляет пользователь отдельно. Нужны собственные host agent, packaging, S3 transfer, encryption, integrity, GC и placement. Restore требует совместимых CPU/host/runtime; существующие vsock connections закрываются и должны быть созданы заново. | **Только fallback при проблемах с microsandbox.** Даёт контроль над механизмом, но требует больше собственной оркестрации. |
| **microsandbox** | [Apache-2.0](https://raw.githubusercontent.com/microsandbox/microsandbox/main/LICENSE). [Документация snapshots](https://github.com/microsandbox/microsandbox/blob/main/docs/sandboxes/snapshots.mdx) описывает full snapshots с disk, memory и processes, CoW memory cache, immutable checkpoints, incremental export и `.msb` archives, SDK на нужных языках. [Host sockets](https://github.com/microsandbox/microsandbox/blob/main/docs/networking/host-sockets.mdx) поддерживают virtio-vsock к локальному Unix socket на Linux/macOS и named pipe на Windows. | Проект сам обозначает статус beta и возможные breaking changes. Full snapshots локальные; документация не гарантирует cross-host migration и recovery внешних connections. Vsock routes локальные и **недоступны в managed cloud/multi-tenant deployment profile**, поэтому требуется local profile на отдельных Linux-хостах. Совместимость runtime/kernel/CPU/memory и reconnect приложений остаются обязанностью gateway. | **Выбран runtime по умолчанию.** Требует production-подтверждения через spike; при выявленных проблемах используется Firecracker fallback. |

Microsandbox проходит полную тестовую матрицу: переменные и open files после restore; поведение MCP-клиента после reconnect; warm restore; cold restore на совместимом хосте через S3; incremental chain import; повреждение/потеря delta; cancel/timeout rollback; удаление VM; запрет одновременного запуска двух VM из одного snapshot. Firecracker проходит ту же матрицу только при необходимости fallback. Последняя проверка обязательна из-за [предупреждения Firecracker](https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/snapshot-support.md) о повторном использовании entropy и уникального состояния при повторном resume; gateway должен исключать это через single-owner lease/fencing независимо от выбранного runtime.

**Выполнен Windows spike microsandbox 0.8.2.** На Windows 11 x64 с WHP подтверждены запуск Python 3.12.16 в VM с 512 MiB RAM, запрет egress к PyPI, full snapshot с памятью и процессом, последовательный warm restore после удаления source VM, rollback после отмены и CoW rollback. В финальных одиночных прогонах snapshot занял 618–632 ms, warm restore — 794–858 ms, rollback — 832–945 ms; это наблюдения на одном ноутбуке, а не production SLO. Сценарий и raw reports находятся в [spikes/microsandbox](spikes/microsandbox/README.md). Обнаружено важное ограничение: full capture отклоняется при активных managed jobs от `msb exec --detach`, поэтому постоянный Python kernel должен запускаться как VM workload вне managed-job tracking. Пользователь дополнительно подтвердил успешное завершение Linux spike; raw Linux report не предоставлен, поэтому его метрики здесь не приводятся. Cross-host cold restore, S3 и host relay остаются непроверенными; `run_linux.sh` сохранён для повторного запуска.

#### 4. Host relay подтверждён; секретный токен отсутствует

Автор подтвердил схему: MCP-клиент внутри VM обращается через `vsock` к host agent; host agent добавляет `vm_session_id` и по mTLS вызывает gateway; gateway проверяет placement и передаёт неизменяемые исходные end-to-end заголовки в upstream-запрос. Секретный токен и `token_version` не используются, поэтому KMS не требуется. Внутренний `vm_session_id` в upstream не передаётся.

Целевой MCP-сервер работает в доверенной среде и не обязан независимо проверять подлинность и полномочия вызова. Основной риск теперь заключается не в утечке bearer-токена, а в доверии к идентификаторам: компрометация доверенной среды или ошибочная регистрация сервера позволяет выдавать вызовы за произвольного пользователя. Контроль доверенной среды и регистрации является обязательной границей безопасности.

#### 5. Идентификаторы неизменяемы; версия не используется

Набор `user_id`, `chat_id`, `run_id` и других идентификаторов внутри существующей VM-сессии не меняется. Поэтому монотонная версия, правила её сравнения и обновление контекста без перезапуска Python больше не требуются. Ранее обсуждавшиеся перенос и сравнение токена удалены из модели.



#### 6. Major: отказоустойчивость задач требует явного state machine

Нужны версируемые состояния и durable transitions для `queued`, `dispatching`, `running`, `snapshotting`, `succeeded`, `failed`, `cancelled`, `timed_out`, `lost` и `expired`.

При инфраструктурном сбое выполняющаяся задача не повторяется; gateway должен записать терминальный infrastructure-failure result, чтобы polling не завис навсегда. Ещё не выполнявшиеся задачи сохраняются в PostgreSQL в исходном порядке, но после восстановления VM **не запускаются автоматически**: gateway обязан ждать отдельного вызова `resume` от harness. Срок хранения ожидающей задачи задаётся настройкой `queue_entry_ttl`. По его истечении задача получает терминальный статус `expired`, не запускается и удаляется из очереди без восстановления VM; результат об истечении доступен для polling в течение обычного TTL результата. Submit обязан содержать client-supplied `idempotency key`: повтор с тем же ключом возвращает исходную задачу и не создаёт второй запуск. Таймаут задачи ограничивает только работу Python-кода, исключая ожидание в очереди, восстановление VM и фиксацию snapshot. Осталось определить порядок отмены/удаления относительно записи нового snapshot.

#### 7. Major: отсутствуют публичные контракты API

Нужно явно разделить:

* MCP-facing surface для harness и VM: discovery, описание и вызов tools;
* HTTP/admin surface для регистрации серверов, VM lifecycle, submit/poll/cancel задач и отдельного `resume` очереди после восстановления;
* внутренний relay-facing surface с mTLS и привязкой VM-сессии к хосту.

Поиск должен определять query, top-k, поля результата и стабильный tool id. Describe должен возвращать полную схему конкретного tool. Следует решить, разрешён ли полный `tools/list` с тысячами entries или discovery является единственным рекомендуемым путём.

Ошибки должны быть машиночитаемыми: removed tool, server unavailable, queue full, timeout, cancelled, lost, result expired, capacity exceeded. Для tool names нужны разделитель, допустимый charset, ограничение длины, уникальность имени сервера и правила неизменности. Переименование сервера не должно молча ломать сохранённые harness transcripts.

#### 8. Major: snapshots и VM image должны быть совместимы по версиям

Snapshot manifest должен содержать schema version, image digest, VM-session id, snapshot generation, queue position и checksum. Нужно решить, какие комбинации image/snapshot допустимы при восстановлении и как происходит rollout нового Python image при живых сессиях. Автоматическое пересоздание VM при несовместимости потеряло бы состояние и не может быть молчаливым правилом.

#### 9. Blocker для эксплуатации: нет telemetry и audit-контракта

SLO без измерений непроверяем. Требуются:

* end-to-end correlation id через harness, queue, host, VM, snapshot и result polling;
* queue depth/age, task duration и terminal-state counters;
* snapshot size/duration/failure, время с последнего durable snapshot на VM, restore duration по warm/cold и integrity failures;
* placement/host-loss/recovery events и queryable session → host → snapshot map;
* причины удаления серверов из каталога, health истории, relay/mTLS/vsock errors и certificate expiry;
* session create/delete audit, перечень исходных идентификаторов в диагностике вызова и deletion-completion events;
* bounded-cardinality правила для user/chat/run labels и отдельные события для degradation, rollback и cascade deletion.

Секретный токен отсутствует, но идентификаторы и исходные заголовки не должны без необходимости попадать в логи, traces, метрики, ошибки, audit и дампы. Разрешён только явный allowlist диагностических полей; полный дамп заголовков запрещён и проверяется тестами redaction.

#### 10. Major: relay и placement являются частью hot path

Каждый вызов из VM идёт по пути guest → vsock → host agent/relay → mTLS → gateway → placement check → upstream. Нужны явный host agent, его уровень доверия, cert rotation/revocation и модель session placement. Gateway и session registry становятся частью code-mode availability SLO.

Предлагаемое reviewers разделение harness-facing и relay-facing deployments позволяет масштабировать и обновлять плоскости раздельно, но добавляет deployment coordination. Это архитектурная альтернатива, а не согласованное решение.

#### 11. Major: концепция объединяет два больших продукта

MCP aggregation/search и durable code-execution platform имеют разные сроки, риски и кандидаты на reuse. `egm-zoom-out` рекомендовал сначала выпустить aggregator-only MVP, а code mode вынести в отдельный этап. Автор отклонил такой staging для первого релиза: первая версия обязана включать весь описанный code mode с microVM. Раздельные spikes, независимое масштабирование компонентов и внутреннее разделение модулей остаются возможными, но выпуск aggregator-only MVP не является допустимой заменой полного первого релиза.

Для gateway и sandbox слоя есть внешние кандидаты, включая ContextForge, Microsoft mcgateway, LiteLLM, Firecracker/microsandbox и managed sandbox platforms. Их фактические возможности, snapshot semantics, поддержка custom identity-header relay, условия развёртывания на отдельных Linux-хостах и лицензии для коммерческого использования в этом обзоре не проверялись. Нельзя объявлять их подходящими только по названию.

#### 12. Меньшие риски каталога и поиска

* Полный перебор векторов достаточен при заявленном масштабе; HNSW действительно не нужен.
* Имя модели принимается как гарантия неизменной размерности по внешнему контракту, но дефенсивная проверка размерности при build всё равно нужна.
* При смене модели shadow build с атомарным переключением избежит простоя поиска, но временно удвоит индекс и фоновую работу. Это совместимо с удалением старого индекса после переключения и требует решения автора.
* При ошибке `tools/list` scheduler делает tools недоступными без удаления FastMCP-регистрации; после успешного refresh они возвращаются. Нужны reason-coded события для `refresh_failed`, `tools_unavailable`, `tools_restored` и `external_unregistered`, а также защита от лишнего re-embedding неизменившихся описаний.
* Для search QPS, embedder RPM/cost и snapshot/VM counts нет исходных чисел. Нагрузочная модель обязана появиться до окончательного sizing.

### Разрешения противоречий и их цена

| Паттерн | Что разрешает | Вторичная цена |
| --- | --- | --- |
| Extraction / Intermediary | Вынести durable state из gateway и отделить relay data plane | Дополнительные hops, HA-зависимости, deployment artifacts |
| Segmentation | Incremental snapshot chains | Цепочки, compaction, integrity, сложность восстановления |
| Preliminary Action | Локальные snapshot caches и при необходимости warm pools | RAM/NVMe стоимость и placement-логика |
| Dynamics | Перенос VM между хостами при внешних queue/snapshot | Lease/fencing и восстановление placement |
| Beforehand Cushioning | Shadow rebuild поискового индекса | Временное удвоение индекса и embedder-работы |

**Ideal Final Result:** любая реплика gateway может быть заменена без потери accepted задач, идентификаторов сессий, placement или snapshots; VM восстанавливается с ценой, зависящей от изменённых страниц, а не от полного размера памяти; секретные credentials отсутствуют; последовательность задач сохраняется при failover без автоматического повторения прерванного кода и без обещания exactly-once внешних side effects.

## Goals vs. Non-Goals

* **In-Scope Goals:** динамический каталог MCP-серверов; префиксация tools; выполнение tools с исходными end-to-end заголовками; семантический поиск и чтение описаний; асинхронный stateful Python code mode; полное логическое сохранение памяти и диска; управление временем жизни VM; восстановление после сбоев; неизменяемый identity context; telemetry и audit.
* **Out-of-Scope Non-Goals:** совместимость со старыми версиями MCP; общий Python interpreter для разных runs; доступ microVM в Интернет; автоматический повтор прерванного кода; гарантия ровно одного выполнения произвольных внешних side effects; per-server OAuth/API-key brokering в первой версии; HNSW.

## Alternatives Weighed

| Альтернатива | Оценка |
| --- | --- |
| Host relay + identity context в session registry | **Выбрано автором.** MCP-клиент через `vsock` обращается к host agent, который добавляет `vm_session_id` и по mTLS вызывает gateway. Gateway передаёт неизменяемые исходные end-to-end заголовки в upstream; секретный токен и `token_version` отсутствуют. Требуется strongly consistent placement. |
| Полные физические snapshot-копии | Простая модель, но неподтверждённые стоимость записи и restore SLO. Не принимать без spike. |
| Incremental/COW snapshots с полным логическим состоянием | **Допущено автором.** Уменьшает write amplification; добавляет цепочки, compaction и проверку ссылок. Runtime и restore SLO требуют spike. |
| Process-level checkpoint вместо VM snapshot | Может уменьшить состояние, но должен доказать сохранение всего требуемого Python/OS состояния и изоляцию. Не выбрано. |
| Явное externalized Python state вместо snapshots | Упрощает инфраструктуру, но не гарантирует полное notebook-состояние произвольного интерпретатора. Не удовлетворяет исходному требованию без дополнительных ограничений. |
| FastMCP как основа MCP-facing gateway | **Выбрано автором.** Apache-2.0, Proxy Provider, multi-server proxying, префиксование и eligible header forwarding. Динамические admin endpoints, scheduler, semantic index и code mode остаются собственными компонентами. |
| Собственный catalog delta/resync protocol | **Заменён решением FastMCP + scheduler:** внешняя система управляет составом серверов, FastMCP выполняет proxying, собственный scheduler обновляет списки tools. |
| Готовый sandbox backend вместо собственной оркестрации | Может сократить VM/snapshot работу. Нужно проверить separate-host deployment, full memory+disk snapshots, no-egress, custom relay и коммерческую лицензию. |
| Firecracker | **Только fallback при проблемах с microsandbox.** Apache-2.0, full/diff snapshots и on-demand loading; требуется собственная оркестрация, а diff snapshots пока developer preview. |
| microsandbox | **Runtime по умолчанию, выбран автором.** Apache-2.0, полные snapshots, CoW и SDK. Требует production-подтверждения; Firecracker используется только при выявленных проблемах. |
| KMS (OpenBao/Infisical) | **Не требуется:** gateway не получает и не хранит секретный токен; `token_version` исключён. |
| Aggregator первым этапом, code mode отдельным этапом | **Отклонено автором для первого релиза:** первая версия обязана включать полный code mode. Отдельные spikes и модульное разделение остаются допустимыми. |
| Lexical/BM25 search | Допустимый дополнительный baseline, но не замена обязательному semantic search. |

## Key Decisions & Staging for Next Steps

### Подтверждено после Goldfish review

1. **Task/snapshot model:** одна задача равна одному фрагменту за submit; хранится только последний snapshot; отмена откатывает текущий фрагмент; результат доступен для polling после durable-фиксации результата и snapshot.
2. **Durable state:** PostgreSQL является transactional store и durable-очередью; S3 хранит snapshots. Отдельный queue service не требуется.
3. **Identity boundary:** host relay и неизменяемые `user_id`, `chat_id`, `run_id` подтверждены. Секретный токен и `token_version` исключены; KMS не требуется.
4. **Failure semantics:** выполнявшаяся задача не повторяется; ожидающие задачи сохраняются и запускаются только после отдельного `resume`. Настраиваемый `queue_entry_ttl` ограничивает срок хранения ожидающих задач; истёкшие задачи получают статус `expired` без запуска и восстановления VM. Timeout исполнения включает только работу Python-кода. Обязателен client-supplied idempotency key.
5. **Runtime и SLO:** microsandbox выбран runtime по умолчанию; Firecracker возможен только как fallback при проблемах с microsandbox. Допускаются incremental/COW snapshots. Cold restore после потери хоста может быть длительнее warm и измеряется отдельно.
6. **Поставка:** первая версия обязана включать весь продукт — каталог, поиск, вызовы tools и полный code mode. Aggregator-only первый релиз не принят.

### Обязательная работа следующего этапа

Формальный design document должен определить публичные MCP/HTTP/relay contracts, lifecycle FastMCP providers при add/remove, scheduler refresh/failure semantics, task state machine и delete/cancel/snapshot ordering, identity glossary, errors, tool naming, search/describe, result expiry, snapshot/image compatibility и нагрузочную модель. MCP-facing часть использует Python/FastMCP; язык host agent остаётся отдельным решением. До плана реализации нужен proof-of-concept microsandbox, включая cold restore, reconnect, single-owner fencing и restore из backups. Firecracker проверяется только при выявлении проблем с microsandbox. Кандидаты на reuse и лицензии всех выбранных зависимостей должны быть проверены на конкретных версиях.

По документации проверены базовые возможности и лицензии Firecracker и microsandbox; источники приведены выше. Runtime spikes, нагрузочные испытания и тесты кода не выполнялись: репозиторий не содержит кода или toolchain. Производительность snapshots, cold restore и production-пригодность выбранных runtime остаются недоказанными до этих проверок.
