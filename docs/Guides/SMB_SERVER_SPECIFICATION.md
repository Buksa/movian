# Спецификация встроенного SMB-сервера Movian

Данный документ представляет собой функциональную спецификацию встроенного SMB2/SMB3 сервера в Movian.

---

## 1. Спецификация конфигурации и настроек (Configuration Specs)

Настройки сервера сохраняются в базе данных параметров Movian в домене `smbserver`.

| Ключ настроек | Тип настроек | Значение по умолчанию | Диапазон/Формат | Функция-обработчик | Описание |
|---|---|---|---|---|---|
| `smbserver.enable` | `SETTING_BOOL` | `0` (Выключен) | `0` или `1` | `set_enable` | Переключатель активности сервера. При активации запускает поток прослушивания. |
| `smbserver.port` | `SETTING_STRING` | `"1445"` | TCP порт (`1`–`65535`) | `set_port` | Порт прослушивания входящих SMB2 соединений. |
| `smbserver.username` | `SETTING_STRING` | `""` (Анонимный) | Строка UTF-8 | `set_username` | Имя пользователя для входа. Если пустое, сервер разрешает анонимный гостевой вход. |
| `smbserver.password` | `SETTING_STRING` | `""` (Пустой) | Скрытая строка (Password) | `set_password` | Пароль для указанного пользователя. Передается в `libsmb2`, где проверяется NTLMv2-ответ клиента. |
| `smbserver.share` | `SETTING_STRING` | `"share"` | Имя шары (без слэшей) | `set_share_name` | Имя ресурса, экспортируемого сервером (например, `\\server\share`). |
| `smbserver.root` | `SETTING_STRING` | `"/"` | `vfs://...`, `file://...` или абсолютный путь | `set_share_root` | Корень экспортируемой шары. Пустое значение и `"/"` означают `vfs:///`, чтобы SMB вел себя как FTP/VFS export. Явный `vfs://...` сохраняется как VFS URL, явный filesystem path экспортируется как `file://...`. |

---

## 2. Спецификация обработчиков протокола SMB2 (Protocol Handler Specs)

Сервер поддерживает подмножество протокола SMB2/SMB3 через интерфейсы `libsmb2`.

### 2.1. Поддерживаемые команды и функции-обработчики

* **`smb_authorize`**: Вызывается при аутентификации пользователя. Настраивает NTLMv2-пароль с помощью `smb2_set_password` для внутренней проверки подлинности.
* **`smb_session_established`**: Финализирует сессию. Включает режим raw-передачи данных (passthrough) для приема буферов переименования/удаления файлов. Выделяет структуру `smb_connection_t`.
* **`smb_tree_connect`**: Монтирует шару. Проверяет имя ресурса. Разрешает монтирование настроенной disk share при совпадении имени с `smb_share_name`, а также минимальный `IPC$` pipe share для `srvsvc` enumeration.
* **`smb_create`**: Открывает или создает файлы/папки. Поддерживает режимы `FILE_OPEN`, `FILE_CREATE`, `FILE_OPEN_IF`, `FILE_OVERWRITE`, `FILE_OVERWRITE_IF` и `FILE_SUPERSEDE`. Обрабатывает флаги разграничения файлов и папок (`DIRECTORY_FILE`/`NON_DIRECTORY_FILE`).
* **`smb_close`**: Закрывает файловый хендл, удаляет файл при наличии флага `delete_on_close` (вызывая `vfs_unlink`/`vfs_rmdir`).
* **`smb_read`**: Читает данные из открытого файлового хендла Movian VFS.
* **`smb_write`**: Записывает данные в открытый файл. Поддерживает дозапись (Append).
* **`smb_query_directory`**: Сканирует директорию с помощью `vfs_scandir`. Поддерживает фильтрацию по wildcard-паттернам клиента с помощью `pattern_match()`, а также флаги `SMB2_RESTART_SCANS` и `SMB2_RETURN_SINGLE_ENTRY`.
* **`smb_query_info`**: Запрашивает метаданные о файле/директории. Возвращает атрибуты (`FILE_ATTRIBUTE_DIRECTORY`, `FILE_ATTRIBUTE_NORMAL`), размер, время создания/модификации/доступа, а также серийный номер тома и файловые идентификаторы (File Index / Volume SN). Поддерживает compound `CREATE + QUERY_INFO + CLOSE` requests, где последующие команды используют SMB2 related-operation all-`FF` File ID.
* **`smb_set_info`**: Изменяет метаданные (размер файла, времена, атрибуты) и выполняет переименование/перемещение файлов/директорий с помощью `vfs_rename()`.
* **`smb_destruction` / `smb_logoff`**: Очищает ресурсы сессии и закрывает все утекшие файловые дескрипторы сессии.
* **`smb_ioctl` / `SMB2_FSCTL_PIPE_TRANSCEIVE`**: Для pipe handle `srvsvc` обрабатывает минимальный DCE/RPC `NetrShareEnum` и возвращает настроенную disk share. Это обеспечивает навигацию `smb2://host:port/` через настоящую SMB host-root enumeration, а не через Movian-only fallback.

### 2.2. Поведение VFS root

* Корень шары по умолчанию (`smbserver.root="/"`) резолвится в `vfs:///`, а не в raw `/` файловой системы.
* Это делает SMB2 server ближе к FTP server: root listing показывает экспортированные Movian VFS mappings. Если mappings отсутствуют, `vfs:///README.TXT` сообщает, что VFS export пуст.
* Явный локальный путь, например `/home/deck/Videos`, остается файловым экспортом и резолвится в `file:///home/deck/Videos`.
* SMB path components нормализуются из `\` в `/`, лишние слэши схлопываются, traversal components `..` отклоняются до обращения к VFS.

---

### 2.3. Host-root enumeration и related compound handles

* `smb2://host:port/` должен проходить через `IPC$` и pipe `srvsvc`. Минимальный `NetrShareEnum` возвращает только настроенную share и не включает network-neighborhood discovery.
* Movian SMB2 client и часть внешних клиентов используют compound `CREATE + QUERY_INFO + CLOSE` для `stat()`. В таких цепочках `QUERY_INFO` и `CLOSE` могут передавать all-`FF` File ID как related-operation placeholder. Сервер хранит последний созданный handle в сессии и резолвит этот placeholder в реальный File ID.
* Deep browse `smb2://host:port/share/zona/` должен логировать последовательность `Create OK` directory -> `QueryInfo: FILE/ALL` -> client `stat ok` -> `QueryDir`. Если `Create OK` есть, но `QueryInfo` нет, искать проблему в related compound File ID обработке.
* Password SMB3 signing пока проверяется отдельным diagnostic smoke: password SMB2 write/read является обязательным baseline, anonymous SMB2/SMB3 navigation является обязательным baseline, а password SMB3 можно сделать обязательным через `SMB_SERVER_SMOKE_REQUIRE_PASSWORD_SMB3=1`.

## 3. Таблица трансляции ошибок (Error Code Mappings)

Ошибки файловой системы VFS преобразуются в соответствующие NTSTATUS коды для SMB-клиентов через функцию `smb_errno_to_ntstatus`:

| POSIX errno / Ошибка VFS | Возвращаемый NTSTATUS | Описание |
|---|---|---|
| `ENOENT` | `SMB2_STATUS_OBJECT_NAME_NOT_FOUND` | Файл или каталог не найден. |
| `EACCES` | `SMB2_STATUS_ACCESS_DENIED` | Доступ запрещен (ошибка прав доступа). |
| `EEXIST` | `SMB2_STATUS_OBJECT_NAME_COLLISION` | Объект с таким именем уже существует. |
| `ENOTDIR` | `SMB2_STATUS_NOT_A_DIRECTORY` | Элемент пути не является каталогом. |
| `EISDIR` | `SMB2_STATUS_FILE_IS_A_DIRECTORY` | Операция над файлом была запрошена для каталога. |
| `ENOSPC`, `ENOMEM` | `SMB2_STATUS_INSUFFICIENT_RESOURCES` | Недостаточно памяти или дискового пространства. |
| `EROFS` | `SMB2_STATUS_MEDIA_WRITE_PROTECTED` | Файловая система смонтирована только для чтения. |
| Другие ошибки | `SMB2_STATUS_INTERNAL_ERROR` | Внутренняя ошибка сервера. |

---

## 4. Ограничения спецификации и лимиты (System Limits)

* **Максимальное число открытых файлов:** `64` открытых файловых хендла на одну сессию клиента (`SMB2_MAX_FILES`).
* **Размер буфера передачи File ID:** `16` байт (`SMB2_FD_SIZE`). Хранит 32-битный индекс слота и 32-битное поколение хендла для предотвращения коллизий устаревших дескрипторов.
* **Таймауты сокетов:** Контролируются внутренним циклом `select()` библиотеки `libsmb2`.
* **Максимальный размер блока I/O:** Ограничен спецификацией протокола SMB2 (клиент и сервер согласовывают `MaxReadSize` и `MaxWriteSize` при согласовании диалекта, обычно 64KB - 1MB).
