# PRD 到 HarmonyRun 测试套件工程说明

本文档基于当前工程文件、脚本、配置和已有运行日志整理。当前工程目录为 `E:\black\prds`。

## 1. 工程功能

当前工程用于把 PRD Markdown 转换为 HarmonyRun/droidrun 可执行的黑盒 UI 测试套件，并可在生成后自动调用 HarmonyRun 真机执行。

核心能力包括：

- 从 `prd/` 目录读取 PRD。
- 通过本地 OpenCode HTTP API 调用 `harmonyrun-testcase-gen` skill 生成测试文件。
- 每个 PRD 生成一套四件套：
  - `{scene}-test-cases.json`：HarmonyRun 可执行测试套件。
  - `{scene}-test-cases.md`：测试用例说明。
  - `{scene}-app-card.md`：给执行 agent 使用的应用结构卡片。
  - `{scene}-agent-prompt.md`：给执行 agent 使用的行为约束。
- 按 PRD 所在子目录归档输出，例如：
  - `prd/requirement/tourist.md`
  - 输出到 `test-cases/requirement/tourist-test-suite/`
- 可继续调用 `harmonyrun test` 执行生成的 JSON，并把报告写入 `result/`。
- 支持单个 PRD、PRD 分类目录、全部 PRD 批量处理。
- 支持一多适配 PRD 的基线巡检用例生成。

## 2. 目录结构

```text
E:\black\prds
├─ config/
│  └─ config.yaml
├─ logs/
│  └─ generate-testcases-*.log
├─ prd/
│  ├─ full-generation/
│  ├─ one2many/
│  └─ requirement/
├─ result/
│  └─ <PRD分类>/<suite_id>/test_<timestamp>_<uuid>/
├─ scripts/
│  ├─ generate_harmonyrun_testcases.py
│  └─ Invoke-HarmonyRunTestcaseGen.ps1
├─ test-cases/
│  └─ <PRD分类>/<scene>-test-suite/
├─ generate-testcases.bat
└─ generate-testcases.sh
```

说明：

- `prd/`：PRD 输入目录。
- `test-cases/`：生成的测试套件目录。
- `result/`：HarmonyRun 执行结果目录，包含 `report.json`、`report.html`、截图、录屏、trace。
- `logs/`：批处理或脚本运行日志。
- `config/config.yaml`：默认运行配置。
- `scripts/generate_harmonyrun_testcases.py`：跨平台主入口。
- `generate-testcases.bat`：Windows 便捷入口，会自动写日志。

## 3. 架构与运行链路

整体链路如下：

```text
用户输入 PRD / 分类 / all
        │
        ▼
generate-testcases.bat 或 Python 主脚本
        │
        ▼
解析 config/config.yaml + CLI 参数
        │
        ▼
解析 PRD 目标列表
        │
        ▼
启动或复用 OpenCode server
        │
        ▼
向 OpenCode session 发送生成提示词
        │
        ▼
本地 skill: harmonyrun-testcase-gen 生成四件套
        │
        ▼
校验 JSON 可解析且包含 suite/test_cases
        │
        ▼
按 config 决定是否调用 harmonyrun test
        │
        ▼
输出 report.html/report.json/trajectory
```

### 3.1 主脚本职责

`scripts/generate_harmonyrun_testcases.py` 是当前主要入口，职责包括：

- 读取 `config/config.yaml`，并允许 CLI 参数覆盖配置。
- 支持输入：
  - 具体 PRD 文件路径。
  - PRD 分类别名，例如 `requirement`、`需求`、`full-generation`、`全新生成`。
  - `all`、`全部`、`全量`。
- 启动 OpenCode server：默认 `127.0.0.1:4096`。
- 创建 OpenCode session，并把生成提示词发给 OpenCode。
- 监听 OpenCode session 过程，打印 tool/message/patch 等日志。
- 校验生成 JSON 是否可解析，以及是否包含 `suite` 和 `test_cases`。
- 若 `suite.app_package` 与配置的 `app.bundlename` 不一致，会自动修正 JSON。
- 若 `workflow.generate_only=false`，继续调用 `harmonyrun test`。
- 汇总每个 PRD 的通过/失败状态。

### 3.2 PowerShell 脚本职责

`scripts/Invoke-HarmonyRunTestcaseGen.ps1` 是较早的 Windows PowerShell 实现，主要覆盖单 PRD 生成场景。当前 Python 脚本能力更完整，已经包含批量、配置读取、HarmonyRun 执行等能力。

### 3.3 BAT 入口职责

`generate-testcases.bat` 是 Windows 用户入口：

- 切换控制台编码到 UTF-8。
- 自动创建 `logs/`。
- 自动按时间戳写 `logs/generate-testcases-*.log`。
- 无参数时提示输入 PRD 路径、场景、分类或 `All`。
- 优先使用 `python`，找不到时使用 `py -3`。

## 4. HarmonyRun 工具介绍

HarmonyRun 在当前工程中承担“执行层”的角色。它不是用例生成器，而是真机黑盒 UI 自动化执行器：读取生成好的测试套件 JSON，启动目标 HarmonyOS 应用，按用例步骤驱动设备点击、输入、返回、等待，并把执行过程、截图、录屏和结构化报告落盘。

### 4.1 工具定位

在本工程链路里，OpenCode skill 负责“从 PRD 生成用例”，HarmonyRun 负责“把用例放到真机上跑”：

```text
PRD
  → OpenCode + harmonyrun-testcase-gen skill
  → {scene}-test-cases.json / app-card / agent-prompt
  → harmonyrun test
  → report.html / report.json / trace / screenshots
```

HarmonyRun 执行的是黑盒 UI 测试，不直接读取源码，也不验证接口返回、数据库状态或后端日志。用例是否通过，主要依赖执行 agent 在设备当前 UI 上看到的可观察结果，例如页面跳转、文本展示、按钮状态、弹窗、列表结构、布局表现等。

### 4.2 输入文件

HarmonyRun 的主要输入是 `{scene}-test-cases.json`。本工程生成的 JSON 会引用两个辅助文件：

- `suite.app_card`：应用卡片，描述已知页面、入口、关键区域、fixture 边界和一多适配规则。
- `suite.agent_prompt`：执行 agent 行为约束，规定如何按步骤执行、如何判定 checkpoint、如何处理权限弹窗、fixture 缺失和一多适配巡检边界。

JSON 的核心结构固定为：

```json
{
  "suite": {
    "id": "...",
    "name": "...",
    "app_package": "...",
    "app_card": "file:./xxx-app-card.md",
    "agent_prompt": "file:./xxx-agent-prompt.md"
  },
  "test_cases": []
}
```

本工程不在 JSON 中新增 `device_type`、`breakpoint`、`assertions`、`depends_on` 等非 schema 字段。断点、设备形态、fixture 条件和适配规则写入 `title`、`preconditions`、`checkpoint`、`expected_result` 或辅助 markdown。

### 4.3 执行阶段

一次 HarmonyRun 用例通常包含这些阶段：

- `setup`：启动或重启目标应用。
- `preconditions`：确认或建立前置状态。
- `test_execution`：按 `test_steps` 执行并验证 checkpoint。
- `teardown`：结束应用、停止录屏和日志采集。

每条用例执行过程中会持续采集设备状态、UI tree、截图和 action trace。报告中的 `failed_phase` 可以帮助判断失败发生在前置条件、测试执行还是收尾阶段。

### 4.4 输出结果

一次套件执行会生成套件级结果目录，常见文件包括：

- `report.html`：可视化报告，适合人工查看。
- `report.json`：结构化报告，适合脚本汇总。
- `cases/<case_id>/trace.jsonl`：逐步事件轨迹。
- `cases/<case_id>/screenshots/`：步骤截图和录屏。
- `cases/<case_id>/device_state/`：每步 UI tree 和设备上下文。

本工程会把 HarmonyRun 结果写入 `result/<PRD分类>/<suite_id>/test_<timestamp>_<uuid>/`。

### 4.5 本工程调用方式

主脚本最终调用的命令形态为：

```powershell
harmonyrun test -c <runtime-config> --save-trajectory <none|step|action> <test-cases.json>
```

可选参数由 `config.yaml` 或 CLI 控制：

- `--case MUST-01`：只执行指定 case。
- `--level L0`：执行指定等级范围。
- `--device <serial>`：指定设备，可重复传入。
- `--save-trajectory step`：控制轨迹保存粒度。

脚本会为每次运行生成 `.harmonyrun-runtime-config.yaml`，并覆盖 `logging.trajectory_path`，确保报告和轨迹写入本工程的 `result/` 目录。

### 4.6 能力边界

HarmonyRun 适合验证“用户能否在真实 UI 上完成某个短流程”以及“页面在目标窗口形态下是否明显适配正常”。它不适合承担后端造数、数据库清理、真实支付到账、真实通知送达、跨系统深度校验等任务。

因此，PRD 中依赖后台状态或指定数据的场景，必须通过 fixture 明确预置；没有 fixture 时，只能写入说明文档，不应直接生成 JSON 可执行用例。

## 5. 用例生成原则

本工程的用例生成遵循“先判断 PRD 类型，再选择生成策略”的原则。当前主要区分三类：元服务/业务功能开发、一多适配、混合型需求。

### 5.1 PRD 类型判定

| 类型 | 判定信号 | 生成策略 |
|------|----------|----------|
| 元服务/业务功能开发 | PRD 重点描述页面、入口、表单、业务流程、账号、权限、搜索、列表、详情、状态流转、校验规则 | 生成短链路、独立可运行、可由 UI 观察判定的业务功能用例 |
| 一多适配 | PRD 重点描述多设备、响应式、断点、横竖屏、自由窗口、折叠态、分栏、栅格、导航形态、弹窗形态 | 按窗口断点生成适配验证用例，重点检查布局、内容可读性和交互可达性 |
| 极简一多适配 | PRD 只有“实现 XX 元服务的一多适配”这类一句话，缺少页面清单、业务流程和断点设计 | 只生成可达页面适配基线巡检，不硬凑完整业务测试套件 |
| 混合型 | 同时包含业务功能和明确适配要求 | 先覆盖 L0 业务主链路，再补充 PRD 明确提到的一多适配点；不把所有业务用例机械复制到所有断点 |

分类结果应体现在 `{scene}-test-cases.md` 的概览中，方便人工理解这次生成到底是在验证业务功能、适配效果，还是只做基线巡检。

### 5.2 元服务/业务功能开发用例原则

元服务开发类 PRD 的目标是验证业务能力是否可用。当前生成策略不是把 PRD 里的所有页面、所有入口、所有状态穷举成大而全的测试集，而是优先生成 HarmonyRun 能稳定执行、结果能从 UI 直接判断的代表性用例。

生成目标：

- 证明 PRD 声明的核心用户路径可达、可操作、可观察。
- 覆盖关键页面结构、核心入口、主要成功路径和高风险校验规则。
- 输出可被 HarmonyRun 直接执行的 JSON，而不是人工测试清单。
- 对无法由 UI 稳定准备或验证的场景，在 markdown 中说明 fixture 缺口，不强行写入 JSON。

当前优先级：

- **L0 用例**：核心冒烟路径。优先覆盖启动后的核心页面、主入口、关键列表/详情/表单、主成功链路、最基础的账号或权限入口。
- **L1 用例**：重要分支和轻量异常。优先覆盖表单必填/格式校验、权限弹窗、账号未满足时的引导或阻断、空态、轻量状态切换。
- **L2 用例**：默认不主动生成，除非 schema、执行策略和用户目标都明确需要。适合弱网、复杂权限拒绝、边界值、外部系统依赖等低频场景。

可执行性分级：

| 等级 | 是否写入 JSON | 处理原则 |
|------|---------------|----------|
| A. UI 可直接执行 | 是 | 从稳定入口出发，短步骤完成操作，并能在 UI 上看到结果 |
| A+. 当前用例内可准备状态 | 是 | 把账号关联、授权等可跳过的准备步骤写进当前代表用例开头 |
| A++. 可持久保持的串行状态 | 谨慎写入 | 只用于权限等可跨 case 保持的状态；通过用例顺序和 `preconditions` 表达依赖 |
| B. 需要 fixture | 有 fixture 才写入 | 需要预置记录、指定账号、指定状态、空数据、mock 数据时，必须声明 fixture 来源 |
| C. 不适合黑盒自动化 | 不写入 JSON | 真实支付到账、真实退款、后台清库造数、跨系统深度校验等只记录在说明中 |

用例形态原则：

- **短链路优先**：每条用例覆盖一个明确功能点或一个短业务阶段，建议 3-5 步，最多 6 步。
- **可独立运行**：默认每条 case 都能从稳定入口开始，不依赖上一条普通用例产生的数据。
- **UI 可观察**：checkpoint 必须是页面可见状态，例如标题、按钮、弹窗、Toast、列表项、表单值、页面跳转。
- **单点验证**：一条用例只验证一个主要规则，避免把多个校验点塞进同一条长链路。
- **验证点去重**：同一业务规则、同一前置状态、同一期望结果只保留一条代表用例；不同入口但验证点相同，默认不重复生成。
- **不串长业务流**：不要把浏览、下单、支付、退款、通知等全部串成一条用例。

前置条件规则：

- `preconditions` 只写稳定、可确认的起始状态，例如“应用已启动；网络连接正常”。
- 账号关联、权限授权等若可由 UI 稳定建立，但不能跨 case 保持，应写进当前代表用例的 `test_steps`，而不是写成跨 case 依赖。
- 指定业务状态、后台预置数据、空数据、mock 数据、默认记录等必须有 fixture 来源；没有 fixture 时不写入 JSON。
- 依赖“已有一条可编辑/可删除数据”的场景，默认属于 fixture 场景；除非 PRD 或用户明确说明测试环境已预置。
- 真实支付、真实退款到账、真实通知送达、真实日历写入后跨系统验证等，不适合作为无 fixture 的黑盒 UI JSON 用例。

步骤编写规则：

- 每个 action 只做一个动作，不写“点击 A 后再点击 B”。
- UI 元素用 `【XX】` 标注，优先使用用户可见文本。
- 输入内容必须来自 PRD、fixture 或用户补充，不从模板虚构业务数据。
- 每次页面跳转、弹窗处理、表单进入、列表进入都拆成独立 step，并配置对应 checkpoint。
- 最后一个 checkpoint 必须是明确终点，满足后即可判定 expected_result。
- 长列表、横滑、瀑布流默认只验证当前可见结构，除非 fixture 明确保证数据量。

不生成或降级为说明的场景：

- PRD 只提到后台状态，但没有 UI 入口或可见结果。
- 需要后台造数、清库、指定订单/记录状态，但没有 fixture。
- 需要真实外部系统完成交易、退款、通知、日历写入等闭环。
- 需要遍历所有页面才能证明“全量功能正常”。
- 需要依赖上一条普通 case 新增的数据继续编辑或删除，但执行环境不能保证同一 app session 串行保持。

### 5.3 一多适配用例原则

一多适配类 PRD 的目标不是验证业务流完整性，而是验证目标窗口形态下 UI 是否满足响应式布局要求。当前生成原则是：按断点组织，而不是按“手机/平板/大屏”粗分。

断点写法：

- `widthBp=xs`：`(0, 320)` vp
- `widthBp=sm`：`[320, 600)` vp
- `widthBp=md`：`[600, 840)` vp
- `widthBp=lg`：`[840, 1440)` vp
- `widthBp=xl`：`[1440, +∞)` vp
- `heightBp=sm/md/lg`：按窗口高宽比区分

生成规则：

- 断点写入 `title`、`preconditions`、`checkpoint` 或 `expected_result`，不新增 JSON 字段。
- 优先生成 `widthBp=sm/md/lg` 的可执行用例；`xs/xl` 只在 PRD 或设备条件明确时追加。
- `heightBp` 只在 PRD 涉及横屏、类方形窗口、折叠态、自由窗口或高宽比差异时追加。
- 同一业务路径不机械复制到所有断点；只挑选能体现布局差异的页面或组件。
- 如果 PRD 缺少页面清单、入口清单和断点设计，只生成“可达页面适配基线巡检”。

通用规则用于判断页面有没有明显错乱：

- 首屏不空白、不黑屏、不持续加载。
- 页面元素无明显重叠、遮挡、错位、越界。
- 不出现非预期横向滚动或内容被裁切到不可读。
- 关键文字可读，关键按钮、输入框、列表项、导航入口可见且可点击。
- 图片、封面、卡片、图表比例合理，没有明显拉伸或压扁。
- 固定导航、悬浮按钮、底部操作区、弹窗不会遮挡关键内容。

定制规则用于判断是否符合 PRD 或最佳实践声明的目标布局：

- 栅格列数、间距、边距、span、offset、order 符合目标断点。
- 单栏、多栏、主从布局、左右分栏、上下分区、侧边栏、导航形态按断点切换。
- List、WaterFlow、Swiper、Grid、Tabs、Navigation、SideBarContainer、GridRow/GridCol 等组件按断点改变布局。
- 小宽度窗口和大宽度窗口下弹窗形态、表单布局、导航位置符合 PRD 声明。

基线巡检结论必须保守表达：

- 可以证明“本用例实际到达页面未发现明显适配问题”。
- 不能证明“所有页面都已适配”。
- 不能证明“业务功能完整正常”。
- 如果目标断点环境无法确认，应在结果中说明环境不匹配，而不是按设备名称推断。

## 6. 配置说明

配置文件：[config/config.yaml](config/config.yaml)

当前关键配置：

```yaml
paths:
  output_root: test-cases
  result_root: result

app:
  bundlename: com.huawei.rentandbuy

opencode:
  host: 127.0.0.1
  port: 4096
  timeout_minutes: 90
  keep_server: false

workflow:
  generate_only: false
  dry_run: false

harmonyrun:
  enabled: true
  config: null
  case: null
  level: null
  devices: []
  save_trajectory: step
  ignore_test_failure: false
```

注意：

- CLI 参数优先级高于 `config.yaml`。
- `app.bundlename` 会写入生成 JSON 的 `suite.app_package`。
- 当前 YAML 解析器是轻量实现，不支持 dash-style 列表，`devices` 必须写成内联列表，例如：

```yaml
devices: ["22M0223C20000053"]
```

## 7. 常用运行方式

### 7.1 运行单个 PRD

```powershell
.\generate-testcases.bat --prd-path prd\one2many\rentandbuy.md
```

或：

```powershell
python scripts\generate_harmonyrun_testcases.py --prd-path prd\one2many\rentandbuy.md
```

### 7.2 按分类运行

```powershell
python scripts\generate_harmonyrun_testcases.py --prd-path requirement
```

可用别名包括：

- `requirement`、`requirements`、`需求`
- `bug-fix`、`bugfix`、`bug`、`问题`、`修复`
- `full-generation`、`full`、`generation`、`全新生成`、`全新`

### 7.3 全量运行

```powershell
python scripts\generate_harmonyrun_testcases.py --prd-path all
```

### 7.4 只生成不执行 HarmonyRun

```powershell
python scripts\generate_harmonyrun_testcases.py --prd-path prd\one2many\rentandbuy.md --generate-only
```

### 7.5 只执行指定 case 或等级

```powershell
python scripts\generate_harmonyrun_testcases.py --prd-path prd\one2many\rentandbuy.md --case MUST-01
```

```powershell
python scripts\generate_harmonyrun_testcases.py --prd-path prd\one2many\rentandbuy.md --level L0
```

### 7.6 保留报告并忽略非零退出

```powershell
python scripts\generate_harmonyrun_testcases.py --prd-path prd\one2many\rentandbuy.md --ignore-test-failure
```

适合只想收集报告、不希望整体命令因 HarmonyRun 返回非 0 而中断的场景。

### 7.7 查看将发送给 OpenCode 的提示词

```powershell
python scripts\generate_harmonyrun_testcases.py --prd-path prd\one2many\rentandbuy.md --dry-run
```

## 8. 生成物说明

以 `prd/one2many/rentandbuy.md` 为例，输出目录为：

```text
test-cases/one2many/rentandbuy-test-suite/
```

其中：

- `rentandbuy-test-cases.json`：HarmonyRun 输入。
- `rentandbuy-test-cases.md`：人读版测试说明。
- `rentandbuy-app-card.md`：应用结构和测试边界说明。
- `rentandbuy-agent-prompt.md`：执行 agent 行为约束。
- `.harmonyrun-runtime-config.yaml`：运行时生成的 HarmonyRun 配置，会覆盖 `logging.trajectory_path` 指向本次结果目录。

执行结果示例：

```text
result/one2many/rentandbuy_multi_device_baseline_audit_suite/
└─ test_20260601_103746_50875ccc/
   ├─ report.json
   ├─ report.html
   └─ cases/
      └─ MUST-01_xxxxxxxx/
         ├─ trace.jsonl
         ├─ report.html
         ├─ screenshots/
         └─ device_state/
```

## 9. 一多适配测试策略补充

当前对“一句话 PRD：实现 XX 元服务的一多适配”的处理策略是：

- 不强行生成完整业务用例。
- 生成“可达页面适配基线巡检”。
- 重点验证当前可达页面是否存在明显适配问题。
- 结果只证明“本次实际到达页面未发现明显问题”，不证明所有页面都覆盖。

推荐断点：

```text
widthBp=sm: [320, 600) vp
widthBp=md: [600, 840) vp
widthBp=lg: [840, 1440) vp
widthBp=xl: [1440, +∞) vp

heightBp=sm/md/lg 按窗口高宽比区分
```

通用检查项：

- 首屏不空白、不黑屏、不持续加载。
- 无明显重叠、遮挡、错位、越界。
- 无非预期横向滚动。
- 关键文字可读，关键按钮可点击。
- 图片、卡片、图表比例正常。
- 固定导航、悬浮按钮、底部操作区不遮挡关键内容。

定制检查项：

- 断点下的列数、栅格、边距、间距、span、offset、order。
- 单栏/多栏/分栏/侧边栏/导航形态切换。
- 列表、瀑布流、轮播、网格、标签页、弹窗表单等组件响应式行为。

## 10. 测试结果

本节用于沉淀当前工程的代表性 HarmonyRun 执行结果，便于后续复盘用例质量、执行稳定性和一多适配评判口径。记录测试结果时，需要区分三类结论：

- **产品结论**：页面或功能是否真的不符合 PRD。
- **用例结论**：测试步骤、checkpoint、探索边界是否设计合理。
- **执行结论**：HarmonyRun/agent/设备环境是否稳定完成执行。

### 10.1 full-generation 与 requirement 执行结果汇总

本表汇总 `result/full-generation` 和 `result/requirement` 下当前已有 HarmonyRun 执行结果。

| 需求 PRD 名称 | 测试项 | 每项测试结果 | 备注 |
|---------------|--------|--------------|------|
| `full-generation/household.md` | 首页基础结构展示 | 通过 | 首页底部标签、城市/搜索入口、常用分类和精选服务卡片展示正常。 |
| `full-generation/household.md` | 搜索服务并查看搜索结果 | 失败 | final-chance 阶段只允许 `complete`，但 agent 仍输出 `type_at`；偏执行收尾/步骤边界问题。 |
| `full-generation/household.md` | 从首页查看服务详情页 | 失败 | final-chance 阶段只允许 `complete`，但 agent 仍输出 `click`；偏执行收尾/步骤边界问题。 |
| `full-generation/household.md` | 切换城市后首页城市名称更新 | 失败 | 多次点击城市入口未进入城市选择页，可能是入口不可点、页面入口识别不准或用例入口与实际 UI 不匹配。 |
| `full-generation/household.md` | 全部服务页面分类导航切换 | 通过 | 成功切到【全部服务】页，分类导航高亮和服务列表联动展示正常。 |
| `full-generation/household.md` | 未关联账号-点击我的订单引导账号关联 | 失败 | 当前环境账号已关联，无法建立“未关联账号”前置状态；应归为 fixture/账号状态不满足。 |
| `full-generation/scenic.md` | 首页TabBar与轮播展示 | 通过 | 首页底部 TabBar、轮播区和服务导航入口展示正常。 |
| `full-generation/scenic.md` | 门票Tab浏览门票列表 | 通过 | 成功切换到【门票】页，门票列表名称和价格展示正常。 |
| `full-generation/scenic.md` | 导览Tab地图景点展示 | 通过 | 成功切换到【导览】页，地图区域、景点标记和景点入口列表展示正常。 |
| `full-generation/scenic.md` | 我的Tab用户信息展示 | 通过 | 【我的】页用户信息卡、订单入口、旅客管理、地址管理入口展示正常。 |
| `full-generation/scenic.md` | 首页热门景点模块展示 | 通过 | 热门景点模块标题和景点卡片展示正常。 |
| `full-generation/scenic.md` | 首页攻略游记模块展示 | 通过 | 攻略游记模块标题和游记条目展示正常。 |
| `full-generation/scenic.md` | 门票列表点击查看详情 | 通过 | 可从门票列表进入门票详情页，详情页核心信息展示正常。 |
| `full-generation/scenic.md` | 未关联账号点击订单入口引导关联 | 失败 | 未关联状态下点击订单相关入口无响应，未出现预期账号关联引导；可能是功能逻辑/环境配置问题。 |
| `requirement/hotscenic.md` | 首页热门景点模块展示 | 通过 | 首页热门景点模块、【更多】入口和景点卡片展示正常。 |
| `requirement/hotscenic.md` | 点击热门景点卡片进入详情页 | 通过 | 可进入景点详情页，景点名称、头图、标签、地址、介绍等信息展示正常。 |
| `requirement/hotscenic.md` | 点击更多进入景点列表页 | 通过 | 可进入景点列表页，列表页标题、返回按钮和景点条目结构正常。 |
| `requirement/hotscenic.md` | 景点列表页点击景点进入详情页 | 通过 | 可从景点列表进入详情页，详情页核心信息展示正常。 |
| `requirement/hotscenic.md` | 详情页语音讲解功能 | 失败 | 点击语音讲解后未观察到播放中状态、底部音频提示或权限弹窗，无法确认功能启动。 |
| `requirement/hotscenic.md` | 详情页地图导航功能 | 通过 | 可从详情页触发地图导航相关交互，核心路径验证通过。 |
| `requirement/tourist.md` | 常用旅客列表页浏览 | 通过 | 可进入常用旅客列表页，列表结构和添加入口展示正常。 |
| `requirement/tourist.md` | 进入添加常用游客编辑页 | 通过 | 可进入添加常用旅客编辑页，姓名、手机号、证件类型、证件号和保存按钮展示正常。 |
| `requirement/tourist.md` | 添加常用游客 - 填写信息并保存 | 通过 | 填写游客信息后保存成功，流程验证通过。 |
| `requirement/tourist.md` | 必填字段为空校验 | 通过 | 证件号为空时点击保存，Toast 提示“请填写完整游客信息”，页面停留在编辑页。 |
| `requirement/tourist.md` | 游客手机号格式校验 | 通过 | 输入非法手机号后出现“请正确填写游客手机号码”提示，页面未跳转。 |
| `requirement/tourist.md` | 游客身份证号格式校验 | 通过 | 输入非法身份证号后出现“请正确填写游客身份证号”提示，页面未跳转。 |

## 11. 后续演进方向

- 明确组件模板用例的测试范围，mock场景测不测，比如mock支付，比如非mock支付还要输入密码等
- 批量执行评测平台用例，重点分析用例执行结果中与实际效果不符的用例，比如因输入法遮挡导致按钮不可点的失败问题，反向优化生成测试用例的skill
- 完善一多适配评价标准，现在只有响应式布局的最佳实践文档
- 当前harmonyrun还是不稳定版本，跟踪稳定版本发布进展，对比稳定版本测试用例执行效果
- 云手机测试对接进展，及时接入
