# ARGOS XDMA — DMA-интеграция в код (P2 готов) — 2026-06-04

## Что сделано (используя субагента + ручная реализация)

### Агентская проверка исправила ложное допущение
Раньше считалось «драйвер работает» по `Status=OK`. Агент копнул реестр:
- `Service` ПУСТОЙ, службы `XDMA` НЕТ, DeviceClasses `{74c7e4a9-...}` НЕТ.
- → device interface XDMA не существует, DMA узлы (\control,\h2c_0,\c2h_0) не открыть.
- Реальный драйвер = oem57.inf (НЕ oem18 — то XGecu USB). install_fixed_v2.ps1 прерван.
Детали — в ARGOS_XDMA_Driver_Install_2026-06-01.md (раздел КОРРЕКЦИЯ 2026-06-04).

### P2 — реализован DMA-доступ в коде (чистый софт, ничего не сломано)
Файл: `src/connectivity/xilinx_fpga.py`. Добавлено:
- `_xdma_base_path()` — получает DevicePath через SetupAPI по GUID
  `{74c7e4a9-6d5d-4a70-bc0d-20691dff9e9d}` (ctypes, без pywin32). None если
  интерфейс не зарегистрирован (= драйвер не привязан).
- `dma_read(node, offset, length)` — CreateFileW + SetFilePointerEx + ReadFile.
  node: control|user|c2h_0|h2c_0|...
- `dma_probe()` — читает \control offset 0x0 → XDMA Identifier (0x1fc0xxxx),
  надёжная проверка железа независимо от bitstream.
- action `dma_test`/`dma_probe`/`dma` в `command()` → доступно через MCP tool xilinx_fpga.

### Проверка (фактический запуск)
- `py_compile` → ✓ компилируется.
- `fpga.dma_probe()` → честно вернул:
  `interface_registered: false`, note: "драйвер не привязан... довести oem57.inf".
  Код НЕ врёт о состоянии — отражает реальность. После привязки драйвера вернёт
  device_path + control_id_hex + xdma_signature_ok.

## Осталось (требует тебя — UAC/админ/Test Mode)

### P1 — довести привязку драйвера XDMA (от Администратора)
```powershell
bcdedit | Select-String testsigning    # должен быть ON (иначе: bcdedit /set testsigning on + reboot)
pnputil /add-driver "C:\Windows\System32\DriverStore\FileRepository\xdma.inf_amd64_a2cc941a44e25429\xdma.inf" /install
$iid='PCI\VEN_10EE&DEV_7022&SUBSYS_28011E00&REV_00\6&375FA39C&0&0038020B'
pnputil /restart-device -d $iid
# КРИТЕРИЙ УСПЕХА:
(Get-PnpDeviceProperty -InstanceId $iid -KeyName DEVPKEY_Device_Service).Data  # → "XDMA"
Test-Path "HKLM:\SYSTEM\CurrentControlSet\Services\XDMA"                         # → True
```
После этого `xilinx_fpga dma_test` через MCP покажет реальную сигнатуру XDMA.

### P3 — идентифицировать bitstream (Vivado HW Manager)
Bitstream прошит во flash FPGA (файлов на дисках нет). Прочитать:
`J:\2025.2\Vivado\bin\vivado.bat -mode tcl` → connect_hw_server → get_hw_devices.
Без .ltx/.xpr карта AXI-адресов user BAR неизвестна — для осмысленного user-чтения
нужен исходный проект или пересборка с известной адресной картой.

## Статус
- [x] Агентская проверка реального состояния (исправлено: драйвер НЕ привязан)
- [x] P2: DMA-код в xilinx_fpga.py (реализован, компилируется, честно отражает состояние)
- [x] Записано в Obsidian
- [ ] P1: привязка драйвера (нужен админ/UAC — команда выше)
- [ ] P3: идентификация bitstream через Vivado HW Manager

*Подход: проверка через агента → исправление ложного допущения → безопасный код →
проверка → отчёт. Опасное (P1, UAC/reboot) НЕ делал сам — оставил команду.*
