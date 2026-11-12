# Flake8 配置漂移诊断：根目录与子目录运行结果不一致

日期：2026-11-12 · 环境：本仓库 `.venv` 中的 flake8 7.5.0（即 `src/flake8` 源码）

## 结论（TL;DR）

- Flake8 的项目配置**从当前工作目录（cwd）向上逐级查找**，每层按 `setup.cfg` → `tox.ini` → `.flake8` 顺序取**第一份**含 `[flake8]` 或 `[flake8:local-plugins]` 段的文件，命中即停，**绝不合并多层配置**（`src/flake8/options/config.py:22`）。
- 从子目录启动时，若该子目录（或其与仓库根之间的某层）存在另一份含 `[flake8]` 段的配置，它会**屏蔽**仓库根的 `tox.ini`：安全插件的本地注册（`[flake8:local-plugins]`）、`extend-select`、`extend-ignore` 等全部丢失 → 告警"少了一批"。
- 相对路径的依附点分两类：
  - `exclude` / `extend-exclude` / `[flake8:local-plugins] paths` → **配置文件所在目录**（`cfg_dir`）；
  - `per-file-ignores` → **启动目录（cwd）**，不随配置文件走。
- `--append-config` 读入主配置之后，同名选项**覆盖**（不是合并），且其中相对路径仍依附**主配置目录**，不是追加文件所在目录；`--isolated` 会让 `--append-config` 静默失效。
- 不改代码的修复：在 CI / pre-commit / tox 调用处用 `--config=<仓库根>/tox.ini` 显式钉住配置。

## 机制详解

### 1. 配置查找（`_find_config_file`，config.py:22-53）

- 起点是 `os.path.abspath(".")`，即**启动目录**，与被检查的文件路径无关（config.py:69）。
- 每个目录内候选顺序：`setup.cfg`、`tox.ini`、`.flake8`（config.py:32）；文件必须含 `[flake8]` 或 `[flake8:local-plugins]` 段才算数（config.py:41）——本仓库根的 `setup.cfg` 没有该段，所以被跳过，根的 `tox.ini:122` 才是生效配置。
- 向上递归，到达文件系统根或**用户主目录**即停（config.py:46）；主目录本身的配置不会被当作项目配置读取。
- 第一份命中即返回，父目录中的其他配置不再读取——子目录配置对根配置是**整体屏蔽**，不是逐选项覆盖。

### 2. 追加配置（`load_config`，config.py:56-95）

- `--config` 指定后**完全绕过**上述查找（config.py:74-75）。
- `--append-config` 的文件在主配置之后 `cfg.read()` 进**同一个** RawConfigParser（config.py:89）：configparser 语义下同段同名选项**后者覆盖前者**，列表型选项（如 `extend-exclude`）不会拼接；追加文件没碰的选项（如 `per-file-ignores`）保留主配置的值。
- `cfg_dir` 只由主配置决定（`os.path.dirname(config)`，config.py:83）；所有选项经 `option.normalize(value, cfg_dir)` 归一化（config.py:126）。**追加文件里的相对路径同样锚在主配置目录**。
- 陷阱：`--isolated` 在 config.py:71 提前返回，其后的 append 循环不会执行——`--isolated --append-config=...` 中追加配置被**静默忽略**。

### 3. 相对路径依附在哪里

| 配置项 | 依附点 | 依据 |
| --- | --- | --- |
| `exclude` / `extend-exclude` | 配置文件所在目录 `cfg_dir` | `normalize_paths=True`（main/options.py:171,182）→ `utils.normalize_path(path, cfg_dir)`（utils.py:164）；**仅当模式含路径分隔符时**才绝对化，裸名字（如 `generated`）按 basename 在任意深度匹配（utils.py:232 先比 basename） |
| `[flake8:local-plugins] paths` | 配置文件所在目录 `cfg_dir` | finder.py:135-137 `normalize_paths(paths, cfg_dir)` |
| `per-file-ignores` | **启动目录 cwd** | 模式在 StyleGuide 初始化时按 `os.curdir` 归一化（style_guide.py:316），被检文件名也按 cwd 取绝对路径后 fnmatch（utils.py:254-258） |

由此产生两类典型漂移：

- 子目录有自己的 `[flake8]` 配置 → 根配置整体失效（本地插件未注册、select/ignore 丢失）。
- 子目录无配置、向上找到根配置 → `exclude` 仍正确（锚在配置目录），但 `per-file-ignores` 里的 `pkg/app.py: E501` 会按 cwd 拼成 `pkg/pkg/app.py` 而**失配**，原本被抑制的告警冒出来。

## 复现（临时目录 + 假检查编号）

复现目录：`$TMPDIR/flake8-cfg-repro`（本次为 `/tmp/claude-501/flake8-cfg-repro`），假安全插件 `sec_plugin.py` 对 `eval(...)` 报 `SEC100`，通过根 `tox.ini` 的 `[flake8:local-plugins]` 注册。

```
flake8-cfg-repro/
├── tox.ini                 # [flake8] max-line-length=88, extend-exclude=pkg/generated,
│                           #          per-file-ignores=pkg/app.py: E501
│                           # [flake8:local-plugins] paths=., SEC = sec_plugin:SecurityChecker
├── sec_plugin.py           # 假安全插件，emit SEC100
├── ci/
│   ├── overlay.ini         # 追加配置：max-line-length=100, extend-exclude=./generated
│   └── generated/bad.py    # eval( → SEC100
├── generated/bad.py        # eval( → SEC100
└── pkg/
    ├── app.py              # eval( → SEC100；93 字符长行 → E501（被 per-file-ignores 抑制）
    ├── generated/gen.py    # eval( → SEC100（被根配置 exclude）
    └── legacy/
        ├── setup.cfg       # [flake8] max-line-length=120  ← 屏蔽根配置的"另一份配置"
        └── check.py        # eval( → SEC100
```

实测结果（命令前缀均为仓库 `.venv/bin/flake8`）：

| # | 目录 / 命令 | 结果 | 说明 |
| --- | --- | --- | --- |
| A | 根：`flake8 pkg ci generated` | 4 条 SEC100（app、legacy/check、ci/generated、generated）；gen.py 被排除；无 E501 | 基线：根 `tox.ini` 生效 |
| B | `pkg/legacy`：`flake8 check.py` | **0 条，exit=0** | `legacy/setup.cfg` 命中即停，根配置被屏蔽，SEC 插件未注册 → 整批告警丢失（即维护者看到的现象） |
| C | `pkg`：`flake8 .` | SEC100 仍在，但**多出 E501**；`generated/` 仍被排除 | 向上找到根配置：`exclude` 锚在配置目录仍正确；`per-file-ignores` 锚在 cwd 失配 |
| D | 根：`flake8 --append-config ci/overlay.ini pkg ci generated` | `pkg/generated/gen.py` 的 SEC100 **复活**；`generated/bad.py` 被排除；`ci/generated/bad.py` **未被排除**；E501 因 100 字限制消失 | 追加配置**覆盖** `extend-exclude`/`max-line-length`；`./generated` 锚在**主配置目录**（根），不是 `ci/` |
| E | `pkg/legacy`：`flake8 --config=../../tox.ini check.py` | SEC100 恢复，exit=1 | 钉住配置即修复 |
| F | `pkg/legacy`：`flake8 --isolated --append-config=../../tox.ini check.py` | 0 条，exit=0 | `--isolated` 静默吞掉 `--append-config`，不能用作修复手段 |

## 修复建议（不改代码）

1. **钉住有效配置**：所有 CI 入口显式传 `--config`：
   - tox：`commands = flake8 --config={toxinidir}/tox.ini src/flake8/ tests/ setup.py`
   - pre-commit（`.pre-commit-config.yaml` 的 flake8 hook）：`args: [--config=tox.ini]`
   - GitHub Actions：避免在子目录设 `working-directory` 直接跑 flake8；必须的话配合 `--config` 指回仓库根。
2. **不要用 `--isolated --append-config` 组合**——追加配置会被静默忽略（场景 F）。
3. 写 `--append-config` 覆盖文件时记住：相对路径锚在**主配置目录**；需要锚在覆盖文件所在目录时只能写绝对路径或从主配置目录推算的相对路径。
4. `per-file-ignores` 的模式按**启动目录**书写并固定启动目录（配合第 1 条，始终在仓库根运行）。
5. 长期治理（属于配置整理而非代码改动）：子项目（如 `example-plugin/`）若新增含 `[flake8]` 段的 `setup.cfg`/`tox.ini`，需意识到它会屏蔽根配置；要么合并进根配置，要么接受该子目录独立 lint。

## 关键源码位置

- `src/flake8/options/config.py:22` `_find_config_file`（向上查找、命中即停、主目录截止）
- `src/flake8/options/config.py:56` `load_config`（`--config` 绕过查找、`cfg_dir` 来源、append 流程、isolated 提前返回）
- `src/flake8/options/config.py:126` `option.normalize(value, cfg_dir)`
- `src/flake8/utils.py:164` `normalize_path`（含分隔符才锚定父目录）
- `src/flake8/utils.py:232` `matches_filename`（basename + 按 cwd 的 abspath）
- `src/flake8/style_guide.py:316`（`per-file-ignores` 模式按 cwd 归一化）
- `src/flake8/plugins/finder.py:135`（local-plugins `paths` 按 `cfg_dir` 归一化）
- `tox.ini:122`（本仓库唯一生效的 `[flake8]` 配置）
