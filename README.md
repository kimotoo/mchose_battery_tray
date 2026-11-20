# 迈从（MCHOSE）Windows 任务栏电量显示工具

轻量级、绿色免安装的 **Windows 10 / Windows 11** 任务栏嵌入式电量监控工具，支持迈从（MCHOSE）全系列多协议无线/有线鼠标（支持有线直连与 2.4G 无线接收器，热插拔实时识别），同时预留了键盘与硬件参数扩展接口。  



> [!WARNING]
> **全AI编程，完全不保证不完全的稳定性**

## 已支持鼠标型号

当前内置 6 套鼠标 HID 协议驱动（[驱动注册中心](drivers/registry.py)），按协议族支持以下型号（同时支持同系 2.4G 无线接收器自动识别）：

| 协议家族 | 适用型号 | 连接方式 / 关键标识 |
| :--- | :--- | :--- |
| **G3 V2 / A7e 配置协议**<br>(`0x55/0xAA`, [g3_v2.py](drivers/g3_v2.py)) | **G3 V2**、**G3 V2 Pro**、**A7e**、**A7e Pro** | 有线直连与 2.4G 接收器<br>VID `0x3837` / `0xA8A4` / `0xA8A5` |
| **G3 原始款被动流协议**<br>(`0xFF02`, [g3_classic.py](drivers/g3_classic.py)) | **G3 原始款** | 2.4G 接收器（SINO WEALTH `258A:0138`） |
| **迈从经典家族协议**<br>(`0x11/0x06`, [mchose_family.py](drivers/mchose_family.py)) | **A7**、**A7 Pro**、**A7 Ultra**、**A7 Ultra(RE)**、**A7X Ultra**<br>**A7 V2 Pro**、**A7 V2 Pro+**、**A7 V2 Ultra**、**A7 V2 Ultra+**<br>**M7**、**M7 Pro**、**M7 Ultra**<br>**L7**、**L7 Pro**、**L7 Pro+**、**L7 Ultra**、**L7 Ultra+**<br>**AX5 V2**、**A5 Ultra**、**A5 V2 Ultra**、**K7 Ultra**、**G3 Ultra** | 有线直连与 2.4G 接收器<br>VID `0x3837` / `0x5253` / `0x41E4` / `0x0BDA` |
| **迈从新一代家族协议**<br>(`0x4D/0x0900`, [mchose_family.py](drivers/mchose_family.py)) | **G3 V3**、**A7 V3**、**A7 V3 Pro**、**A7 V3 Pro+**、**A7 V3 Ultra**、**A7 V3 Ultra+**、**A7 V3-X**、**A7 V3 Magnetic**<br>**A5 V3 Pro**、**A5 V3 Ultra+**、**A5 V3 Ultra+(3955)**<br>**K7 V2 Pro**、**K7 V2 Pro+**、**K7 V2 Ultra**、**K7 V2 Ultra+**<br>**K5 Pro**、**K5 Ultra**、**K5 V2**、**R7 Ultra**、**V7**、**V7 Pro** | 有线直连与 2.4G 接收器<br>VID `0x3837` |
| **初代 A5 / AX5 协议**<br>(`0x2023`, [a5_legacy.py](drivers/a5_legacy.py)) | **A5 系列**（**A5 Pro**、**A5 Pro Max**）<br>**AX5 系列**（**AX5 Pro**、**AX5 Pro Max**） | 有线直连与 2.4G 接收器（VID `0x2023`） |
| **A5 Compx 方案协议**<br>(`0x3554`, [a5_compx.py](drivers/a5_compx.py)) | **A5** / **A5 Pro**（Compx 方案） | 有线 `0xF551` 与 2.4G `0xF550`（VID `0x3554`） |

> 注：未列出 PID 的同系无线设备只要采用上述相同通信协议族（如官方 1K/4K/8K 接收器），亦可自动匹配识别；当同时接入有线与无线通道时优先读取有线直连状态。

## 核心特性

- **Win10 / Win11 任务栏原生挂载与 TrafficMonitor 共存**：
  - Win10 挂入 `ReBarWindow32`、Win11 挂入 `Shell_TrayWnd` 子级，随任务栏同步 Z 序层级，弹出开始/搜索菜单或切换窗口时不丢失、不遮挡；
  - 自动检测 `TrafficMonitor` 等任务栏组件位置并向左/右避让，支持在右键菜单切换停靠方向与「仅托盘图标」模式。
- **双样式与动态连接显示**：
  - **双环双行**：默认显示 `鼠标: XX%` / `键盘: --`（未接入设备显示灰暗圆环与 `--`），后续可接入实际键盘驱动；
  - **鼠标胶囊图标**：仅显示鼠标电量，含底部在线/充电/休眠状态圆点。
- **官方一致的整 10 挡位电量与防误报**：按迈从官方 M HUB 分段规则将原始电量归档为整十挡位（`0..14->10%`、`15..24->20%`、…、`95..100->100%`），并自动过滤未就绪帧与异常 `0%` 瞬态帧（保留上次有效值）。
- **独立图标变色与主题自适应**：`<= 30%` 变黄、`<= 15%` 变红，充电态呈现绿色；文字始终随系统深浅色任务栏主题保持纯白或深黑。
- **热插拔、低电量通知与交互**：
  - 监听 `WM_DEVICECHANGE`（250ms 防抖），接收器或有线插入/拔出时立即刷新；
  - 鼠标悬停显示连接方式与设备详情；左键双击立即刷新；右键弹出控制菜单（样式/模式/停靠位置/刷新间隔/开机自启/退出）；
  - 设备电量 `<= 10%` 弹出一次原生 Windows 通知气泡，充电或回升至 `>= 15%` 后重置提醒锁。
- **极致轻量与隐私安全**：纯 Win32 原生窗口宿主（无需写注册表、无需 COM DLL、无需 `pystray`），绿色单文件 EXE，不写日志、不联网。

## 环境与编译打包

1. 在 `conda` 环境 `rec-demo` 中确认依赖（已安装的 `hidapi`、`Pillow`、`pyinstaller`）：
   ```bash
   pip install -r requirements.txt
   ```
2. 运行或打包为单文件 EXE：
   - 直接源码运行：
     ```bash
     python mchose_battery.py
     ```
   - 一键测试并打包（生成 `dist\迈从电量托盘.exe`）：
     ```bat
     build.bat
     ```

## 设计与协议文档

完整的软硬件架构与迈从全系列（G3 V2 `0x55/0xAA`、经典 `0x11/0x06`、新代 `0x4D/0x0900`、键盘 `0xFF02/0x13`）字节级协议规范详见 [DESIGN.md](DESIGN.md)。
