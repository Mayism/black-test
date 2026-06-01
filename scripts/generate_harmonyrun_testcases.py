#!/usr/bin/env python3
"""Generate HarmonyRun test cases from a PRD through the OpenCode HTTP API.

This is the cross-platform entrypoint. It intentionally uses only Python's
standard library so it can run in Windows, Linux, macOS, and cloud containers.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def short_text(value: Any, max_length: int = 1200) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").strip()
    if len(text) <= max_length:
        return text
    return text[:max_length] + "\n... <truncated>"


def request_json(method: str, url: str, body: Any | None = None, timeout: int = 60) -> Any:
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))


def strip_yaml_comment(line: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_double:
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "#" and not in_single and not in_double:
            return line[:index].rstrip()
    return line.rstrip()


def parse_yaml_scalar(value: str) -> Any:
    text = value.strip()
    lower = text.lower()
    if lower in {"null", "none", "~", ""}:
        return None
    if lower == "true":
        return True
    if lower == "false":
        return False
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if text.startswith("[") and text.endswith("]"):
        return json.loads(text)
    if text.startswith('"') and text.endswith('"'):
        return json.loads(text)
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1].replace("''", "'")
    return text


def load_project_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}

    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for line_no, raw_line in enumerate(config_path.read_text(encoding="utf-8").splitlines(), 1):
        line = strip_yaml_comment(raw_line)
        if not line.strip():
            continue
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise RuntimeError(f"Tabs are not supported in project config indentation: {config_path}:{line_no}")

        indent = len(line) - len(line.lstrip(" "))
        text = line.strip()
        if text.startswith("- "):
            raise RuntimeError(
                f"Dash-style YAML lists are not supported in project config. "
                f"Use inline lists instead, for example devices: [SERIAL]. Location: {config_path}:{line_no}"
            )
        if ":" not in text:
            raise RuntimeError(f"Invalid project config line: {config_path}:{line_no}: {raw_line}")

        key, value = text.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise RuntimeError(f"Invalid empty config key: {config_path}:{line_no}")

        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise RuntimeError(f"Invalid indentation in project config: {config_path}:{line_no}")

        parent = stack[-1][1]
        if value == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = parse_yaml_scalar(value)

    return root


def config_get(config: dict[str, Any], path: str, default: Any = None) -> Any:
    cursor: Any = config
    for part in path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return default
        cursor = cursor[part]
    return cursor


def choose(cli_value: Any, config: dict[str, Any], path: str, default: Any = None) -> Any:
    if cli_value is not None:
        return cli_value
    return config_get(config, path, default)


def yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def default_harmonyrun_config_path() -> Path | None:
    env_path = os.environ.get("HARMONYRUN_CONFIG")
    if env_path and Path(env_path).exists():
        return Path(env_path)

    user_config = Path.home() / ".config" / "harmonyrun" / "config.yaml"
    if user_config.exists():
        return user_config

    return None


def set_yaml_logging_trajectory_path(content: str, trajectory_path: Path) -> str:
    lines = content.splitlines()
    new_line = f"  trajectory_path: {yaml_string(str(trajectory_path))}"

    logging_start = None
    for index, line in enumerate(lines):
        if re.match(r"^logging\s*:\s*(#.*)?$", line):
            logging_start = index
            break

    if logging_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["logging:", new_line])
        return "\n".join(lines) + "\n"

    block_end = len(lines)
    for index in range(logging_start + 1, len(lines)):
        line = lines[index]
        if line.strip() and not line.startswith((" ", "\t")) and not line.lstrip().startswith("#"):
            block_end = index
            break

    for index in range(logging_start + 1, block_end):
        if re.match(r"^\s+trajectory_path\s*:", lines[index]):
            lines[index] = new_line
            return "\n".join(lines) + "\n"

    lines.insert(logging_start + 1, new_line)
    return "\n".join(lines) + "\n"


def write_harmonyrun_config(base_config: Path | None, target_config: Path, trajectory_path: Path) -> Path:
    if base_config and base_config.exists():
        content = base_config.read_text(encoding="utf-8")
    else:
        content = "logging:\n  save_trajectory: none\n"

    target_config.parent.mkdir(parents=True, exist_ok=True)
    target_config.write_text(
        set_yaml_logging_trajectory_path(content, trajectory_path),
        encoding="utf-8",
    )
    return target_config


def stream_process(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
    print("", flush=True)
    print("[harmonyrun:command]", flush=True)
    print(" ".join(command), flush=True)
    print("", flush=True)

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        creationflags=creationflags,
    )

    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)

    return process.wait()


def find_report_dirs(root: Path) -> set[Path]:
    if not root.exists():
        return set()
    return {path.parent.resolve() for path in root.rglob("report.json")}


def is_healthy(base_url: str) -> bool:
    try:
        health = request_json("GET", f"{base_url}/global/health", timeout=3)
        return bool(health and health.get("healthy"))
    except Exception:
        return False


def start_opencode_server(workspace: Path, host: str, port: int, base_url: str) -> subprocess.Popen[Any] | None:
    if is_healthy(base_url):
        print(f"OpenCode server already available at {base_url}", flush=True)
        return None

    command = shutil.which("opencode.cmd") or shutil.which("opencode")
    if command is None:
        raise RuntimeError("Cannot find opencode command in PATH")

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    process = subprocess.Popen(
        [command, "serve", "--hostname", host, "--port", str(port)],
        cwd=str(workspace),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )

    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(0.5)
        if is_healthy(base_url):
            print(f"Started OpenCode server at {base_url}", flush=True)
            return process

    raise RuntimeError(f"OpenCode server did not become healthy within 30 seconds. Process ID: {process.pid}")


def stop_opencode_server(process: subprocess.Popen[Any] | None) -> None:
    if process is None:
        return
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def resolve_input_path(path_text: str, workspace: Path) -> Path:
    expanded = Path(os.path.expandvars(path_text.strip().strip('"')))
    if expanded.is_absolute():
        return expanded.resolve(strict=True)

    from_current = (Path.cwd() / expanded).resolve()
    if from_current.exists():
        return from_current

    return (workspace / expanded).resolve(strict=True)


def relative_parent_under_prd(prd_file: Path, workspace: Path) -> Path:
    prd_root = (workspace / "prd").resolve()
    prd_parent = prd_file.parent.resolve()
    try:
        return prd_parent.relative_to(prd_root)
    except ValueError:
        return Path()


def prd_category_aliases() -> dict[str, str]:
    return {
        "requirement": "requirement",
        "requirements": "requirement",
        "需求": "requirement",
        "bug-fix": "bug-fix",
        "bugfix": "bug-fix",
        "bug": "bug-fix",
        "问题": "bug-fix",
        "修复": "bug-fix",
        "full-generation": "full-generation",
        "full": "full-generation",
        "generation": "full-generation",
        "全新生成": "full-generation",
        "全新": "full-generation",
    }


def resolve_prd_targets(input_text: str, workspace: Path) -> list[Path]:
    text = input_text.strip().strip('"')
    if not text:
        raise RuntimeError("Input cannot be empty")

    lowered = text.lower()
    prd_root = workspace / "prd"

    if lowered in {"all", "全部", "全量", "*"}:
        targets = sorted(prd_root.rglob("*.md"))
        if not targets:
            raise RuntimeError(f"No PRD markdown files found under {prd_root}")
        return targets

    aliases = prd_category_aliases()
    if lowered in aliases or text in aliases:
        category = aliases.get(lowered) or aliases[text]
        category_dir = prd_root / category
        targets = sorted(category_dir.rglob("*.md"))
        if not targets:
            raise RuntimeError(f"No PRD markdown files found under {category_dir}")
        return targets

    candidate = Path(os.path.expandvars(text))
    possible_paths = []
    if candidate.is_absolute():
        possible_paths.append(candidate)
    else:
        possible_paths.append((Path.cwd() / candidate))
        possible_paths.append((workspace / candidate))
        possible_paths.append((prd_root / candidate))

    for possible in possible_paths:
        if possible.exists():
            resolved = possible.resolve(strict=True)
            if resolved.is_file():
                if resolved.suffix.lower() != ".md":
                    raise RuntimeError(f"PRD file must be a markdown file: {resolved}")
                return [resolved]
            if resolved.is_dir():
                targets = sorted(resolved.rglob("*.md"))
                if not targets:
                    raise RuntimeError(f"No PRD markdown files found under {resolved}")
                return targets

    if not any(sep in text for sep in ("\\", "/")) and not text.lower().endswith(".md"):
        stem_matches = sorted(prd_root.rglob(f"{text}.md"))
        if len(stem_matches) == 1:
            return [stem_matches[0].resolve(strict=True)]
        if len(stem_matches) > 1:
            choices = ", ".join(str(path) for path in stem_matches)
            raise RuntimeError(f"Multiple PRD files match scene name {text!r}: {choices}")

    raise RuntimeError(
        "Input must be a PRD markdown file path, a PRD category "
        "(requirement/需求, bug-fix/问题, full-generation/全新生成), or all/全部."
    )


def build_prompt(
    prd_file: Path,
    scene: str,
    app_package: str,
    output_dir: Path,
    json_file: Path,
    markdown_file: Path,
    app_card_file: Path,
    agent_prompt_file: Path,
) -> str:
    return f"""Use the local opencode skill harmonyrun-testcase-gen to generate HarmonyRun black-box UI test cases from this PRD.

Input PRD:
{prd_file}

Output directory:
{output_dir}

Required output files:
- {json_file}
- {markdown_file}
- {app_card_file}
- {agent_prompt_file}

Generation requirements:
1. Read the PRD from the input path and generate test cases for scene "{scene}".
2. Write all generated files into the output directory only.
3. The JSON file must be the HarmonyRun executable test suite input.
4. In suite.app_package use exactly "{app_package}". Do not infer or invent another package name from the PRD.
5. In suite.app_card use "file:./{app_card_file.name}".
6. In suite.agent_prompt use "file:./{agent_prompt_file.name}".
7. If the output directory already exists, overwrite only the four required files for this scene.
8. Do not edit the PRD source file and do not write generated files outside the output directory.
9. Preserve Chinese text as UTF-8. If using PowerShell to read or write files, use -Encoding UTF8.
10. The JSON must be strict valid JSON: escape any double quotes inside string values, do not leave unterminated strings, and do not write mojibake text.
11. Before finishing, validate that the JSON is parseable and that suite plus test_cases exist. If validation fails, fix the file before completing.
"""


def write_opencode_part(part: dict[str, Any]) -> None:
    part_type = part.get("type")

    if part_type == "reasoning":
        text = short_text(part.get("text"))
        if text:
            print("\n[opencode:thinking]", flush=True)
            print(text, flush=True)
        return

    if part_type == "text":
        text = short_text(part.get("text"), 2000)
        if text:
            print("\n[opencode:message]", flush=True)
            print(text, flush=True)
        return

    if part_type == "tool":
        state = part.get("state") or {}
        tool_name = part.get("tool") or "tool"
        status = state.get("status") or ""
        state_input = state.get("input") or {}
        title = state.get("title") or state_input.get("description") or state_input.get("command") or "(no title)"
        print(f"\n[opencode:tool][{tool_name}][{status}] {title}", flush=True)

        command = state_input.get("command")
        if command:
            print("command:", flush=True)
            print(short_text(command, 600), flush=True)

        output = state.get("output")
        if status == "completed" and output:
            print("output:", flush=True)
            print(short_text(output, 1200), flush=True)
        return

    if part_type == "file":
        path = part.get("path")
        if path:
            print(f"\n[opencode:file] {path}", flush=True)
        return

    if part_type == "patch":
        print("\n[opencode:patch]", flush=True)
        for file_name in part.get("files") or []:
            print(f"  {file_name}", flush=True)
        return

    if part_type == "step-start":
        print("\n[opencode:step-start]", flush=True)
        return

    if part_type == "step-finish":
        print(f"\n[opencode:step-finish] {part.get('reason') or ''}", flush=True)
        return

    if part_type:
        print(f"\n[opencode:{part_type}]", flush=True)


def get_session_status_type(base_url: str, directory_query: str, session_id: str) -> str:
    try:
        status = request_json("GET", f"{base_url}/session/status?directory={directory_query}", timeout=10)
        if not isinstance(status, dict):
            return "idle"
        session_status = status.get(session_id)
        if not session_status:
            return "idle"
        return str(session_status.get("type") or "unknown")
    except Exception:
        return "unknown"


def watch_session(base_url: str, directory_query: str, session_id: str, timeout_sec: int) -> None:
    printed: set[str] = set()
    deadline = time.time() + timeout_sec
    idle_seen = False

    while time.time() < deadline:
        messages = request_json(
            "GET",
            f"{base_url}/session/{session_id}/message?directory={directory_query}&limit=200",
            timeout=30,
        )
        if not isinstance(messages, list):
            messages = []

        def created_at(message: dict[str, Any]) -> int:
            return int(((message.get("info") or {}).get("time") or {}).get("created") or 0)

        for message in sorted(messages, key=created_at):
            info = message.get("info") or {}
            if info.get("role") != "assistant":
                continue
            for part in message.get("parts") or []:
                part_id = part.get("id")
                if not part_id:
                    continue
                status = ""
                if part.get("type") == "tool":
                    status = str(((part.get("state") or {}).get("status")) or "")
                key = f"{part_id}:{status}"
                if key in printed:
                    continue
                write_opencode_part(part)
                printed.add(key)

        status_type = get_session_status_type(base_url, directory_query, session_id)
        if status_type != "busy":
            if idle_seen:
                return
            idle_seen = True
        else:
            idle_seen = False

        time.sleep(2)

    raise TimeoutError(f"Timed out while waiting for OpenCode generation after {timeout_sec} seconds")


def run_harmonyrun_test(
    *,
    workspace: Path,
    json_file: Path,
    result_dir: Path,
    output_dir: Path,
    harmonyrun_config: Path | None,
    case_id: str | None,
    level: str | None,
    devices: list[str],
    save_trajectory: str,
    ignore_test_failure: bool,
) -> int:
    command = shutil.which("harmonyrun")
    if command is None:
        command = shutil.which("harmonyrun.exe")
    if command is None:
        raise RuntimeError("Cannot find harmonyrun command in PATH")

    result_dir.mkdir(parents=True, exist_ok=True)
    runtime_config = output_dir / ".harmonyrun-runtime-config.yaml"
    base_config = harmonyrun_config or default_harmonyrun_config_path()
    write_harmonyrun_config(base_config, runtime_config, result_dir)

    before_reports = find_report_dirs(result_dir)

    args = [
        command,
        "test",
        "-c",
        str(runtime_config),
        "--save-trajectory",
        save_trajectory,
    ]
    if case_id:
        args.extend(["--case", case_id])
    if level:
        args.extend(["--level", level])
    for device in devices:
        args.extend(["-d", device])
    args.append(str(json_file))

    print("", flush=True)
    print(f"HarmonyRun result root: {result_dir}", flush=True)
    print("Starting HarmonyRun test. Live log follows.", flush=True)
    process_env = os.environ.copy()
    process_env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "NO_COLOR": "1",
            "CLICOLOR": "0",
            "TERM": "dumb",
            "TTY_COMPATIBLE": "0",
            "TTY_INTERACTIVE": "0",
        }
    )
    exit_code = stream_process(args, workspace, env=process_env)

    after_reports = find_report_dirs(result_dir)
    new_reports = sorted(after_reports - before_reports)

    print("", flush=True)
    print("HarmonyRun execution completed.", flush=True)
    if new_reports:
        print("New result report directories:", flush=True)
        for report_dir in new_reports:
            print(f"  {report_dir}", flush=True)
            html = report_dir / "report.html"
            if html.exists():
                print(f"  {html}", flush=True)
    else:
        print(f"No new report.json was found under {result_dir}", flush=True)

    if exit_code != 0 and not ignore_test_failure:
        raise RuntimeError(f"HarmonyRun test failed with exit code {exit_code}")

    return exit_code


def generate_and_run_prd(
    *,
    prd_file: Path,
    workspace: Path,
    project_config_path: Path,
    output_root: str,
    result_root: str,
    app_package: str,
    base_url: str | None,
    directory_query: str | None,
    model: Any,
    agent: Any,
    timeout_minutes: int,
    generate_only: bool,
    harmonyrun_config_text: Any,
    case_id: Any,
    level: Any,
    devices: list[Any],
    save_trajectory: str,
    ignore_test_failure: bool,
    dry_run: bool,
) -> None:
    scene = prd_file.stem
    relative_parent = relative_parent_under_prd(prd_file, workspace)
    suite_dir_name = f"{scene}-test-suite"
    output_dir = workspace / output_root / relative_parent / suite_dir_name

    json_file = output_dir / f"{scene}-test-cases.json"
    markdown_file = output_dir / f"{scene}-test-cases.md"
    app_card_file = output_dir / f"{scene}-app-card.md"
    agent_prompt_file = output_dir / f"{scene}-agent-prompt.md"
    result_dir = workspace / result_root / relative_parent

    prompt = build_prompt(
        prd_file,
        scene,
        app_package,
        output_dir,
        json_file,
        markdown_file,
        app_card_file,
        agent_prompt_file,
    )

    print("", flush=True)
    print("=" * 72, flush=True)
    print(f"PRD: {prd_file}", flush=True)
    print(f"Scene: {scene}", flush=True)
    print(f"App package: {app_package}", flush=True)
    print(f"Output: {output_dir}", flush=True)
    print(f"Result: {result_dir}", flush=True)
    print("=" * 72, flush=True)

    if dry_run:
        print("\nDry run only. Prompt that would be sent to OpenCode:", flush=True)
        print("------------------------------------------------------", flush=True)
        print(prompt, flush=True)
        return

    if base_url is None or directory_query is None:
        raise RuntimeError("OpenCode server is not initialized")

    output_dir.mkdir(parents=True, exist_ok=True)

    session = request_json(
        "POST",
        f"{base_url}/session?directory={directory_query}",
        {"title": f"Generate HarmonyRun test cases for {scene}"},
        timeout=60,
    )
    session_id = session["id"]
    print(f"OpenCode session: {session_id}", flush=True)

    message_body: dict[str, Any] = {"parts": [{"type": "text", "text": prompt}]}
    if model:
        model_parts = str(model).split("/", 1)
        if len(model_parts) != 2:
            raise RuntimeError("Model must use provider/model format, for example: bailian-coding-plan/qwen3-coder-plus")
        message_body["model"] = {"providerID": model_parts[0], "modelID": model_parts[1]}
    if agent:
        message_body["agent"] = agent

    timeout_sec = max(60, timeout_minutes * 60)
    print("Sending testcase generation prompt to OpenCode...", flush=True)
    request_json(
        "POST",
        f"{base_url}/session/{session_id}/prompt_async?directory={directory_query}",
        message_body,
        timeout=60,
    )
    print("OpenCode is generating files. Live log follows; this can take several minutes.", flush=True)
    watch_session(base_url, directory_query, session_id, timeout_sec)
    print("OpenCode generation request completed. Validating output files...", flush=True)

    if not json_file.exists():
        raise RuntimeError(f"Generation finished but expected JSON was not found: {json_file}")

    try:
        data = json.loads(json_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Generated JSON is not parseable: {json_file}. Reason: {exc}") from exc

    if "suite" not in data or "test_cases" not in data:
        raise RuntimeError("Generated JSON is missing required fields: suite/test_cases")
    if not isinstance(data["suite"], dict):
        raise RuntimeError("Generated JSON field suite must be an object")

    if data["suite"].get("app_package") != app_package:
        old_package = data["suite"].get("app_package")
        data["suite"]["app_package"] = app_package
        json_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"Corrected suite.app_package: {old_package!r} -> {app_package!r}",
            flush=True,
        )

    print("Generated HarmonyRun test suite:", flush=True)
    print(f"  {json_file}", flush=True)
    print(f"  {markdown_file}", flush=True)
    print(f"  {app_card_file}", flush=True)
    print(f"  {agent_prompt_file}", flush=True)

    if not generate_only:
        run_harmonyrun_test(
            workspace=workspace,
            json_file=json_file,
            result_dir=result_dir,
            output_dir=output_dir,
            harmonyrun_config=resolve_input_path(str(harmonyrun_config_text), workspace) if harmonyrun_config_text else None,
            case_id=case_id,
            level=level,
            devices=[str(device) for device in devices],
            save_trajectory=save_trajectory,
            ignore_test_failure=ignore_test_failure,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate HarmonyRun test cases from a PRD through OpenCode.")
    parser.add_argument(
        "prd_path_arg",
        nargs="?",
        help="PRD markdown file path, category alias, or all/全部",
    )
    parser.add_argument("--project-config", help="Project config YAML path; defaults to config/config.yaml")
    parser.add_argument("--prd-path", help="PRD markdown file path, category alias, or all/全部")
    parser.add_argument("--output-root", help="Output root directory relative to workspace")
    parser.add_argument("--result-root", help="HarmonyRun result root directory relative to workspace")
    parser.add_argument("--bundlename", help="HarmonyOS app bundle name to write into suite.app_package")
    parser.add_argument("--host", help="OpenCode server host")
    parser.add_argument("--port", type=int, help="OpenCode server port")
    parser.add_argument("--model", help="OpenCode model in provider/model format")
    parser.add_argument("--agent", help="OpenCode agent name")
    parser.add_argument("--timeout-minutes", type=int, help="Generation timeout")
    parser.add_argument("--keep-server", action=argparse.BooleanOptionalAction, default=None, help="Keep server process started by this script")
    parser.add_argument("--generate-only", action=argparse.BooleanOptionalAction, default=None, help="Only generate test cases; do not run harmonyrun test")
    parser.add_argument("--harmonyrun-config", help="Base HarmonyRun config file to copy and override for this run")
    parser.add_argument("--case", help="Run only the generated test case with this ID")
    parser.add_argument("--level", choices=["L0", "L1", "L2"], help="Run generated cases from L0 through this level")
    parser.add_argument("--device", action="append", default=None, help="HarmonyOS device serial/IP; can be repeated")
    parser.add_argument("--save-trajectory", choices=["none", "step", "action"], help="HarmonyRun trajectory saving level")
    parser.add_argument("--ignore-test-failure", action=argparse.BooleanOptionalAction, default=None, help="Return success even if harmonyrun test exits non-zero")
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None, help="Print prompt without calling OpenCode")
    return parser.parse_args()


def main() -> int:
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    args = parse_args()
    workspace = Path(__file__).resolve().parents[1]
    os.chdir(workspace)

    project_config_path = (
        resolve_input_path(args.project_config, workspace)
        if args.project_config
        else workspace / "config" / "config.yaml"
    )
    project_config = load_project_config(project_config_path)

    output_root = str(choose(args.output_root, project_config, "paths.output_root", "test-cases"))
    result_root = str(choose(args.result_root, project_config, "paths.result_root", "result"))
    app_package = choose(args.bundlename, project_config, "app.bundlename", None)
    if app_package is None:
        app_package = config_get(project_config, "bundlename", None)
    if app_package is None:
        app_package = config_get(project_config, "paths.bundlename", None)
    if app_package is None or str(app_package).strip() == "":
        raise RuntimeError(
            "config/config.yaml must define app.bundlename, or pass --bundlename."
        )
    app_package = str(app_package).strip()
    host = str(choose(args.host, project_config, "opencode.host", "127.0.0.1"))
    port = int(choose(args.port, project_config, "opencode.port", 4096))
    model = choose(args.model, project_config, "opencode.model", None)
    agent = choose(args.agent, project_config, "opencode.agent", None)
    timeout_minutes = int(choose(args.timeout_minutes, project_config, "opencode.timeout_minutes", 90))
    keep_server = bool(choose(args.keep_server, project_config, "opencode.keep_server", False))
    generate_only = bool(
        choose(
            args.generate_only,
            project_config,
            "workflow.generate_only",
            not bool(config_get(project_config, "harmonyrun.enabled", True)),
        )
    )
    harmonyrun_config_text = choose(args.harmonyrun_config, project_config, "harmonyrun.config", None)
    case_id = choose(args.case, project_config, "harmonyrun.case", None)
    level = choose(args.level, project_config, "harmonyrun.level", None)
    devices = args.device if args.device is not None else config_get(project_config, "harmonyrun.devices", [])
    save_trajectory = str(choose(args.save_trajectory, project_config, "harmonyrun.save_trajectory", "step"))
    ignore_test_failure = bool(
        choose(args.ignore_test_failure, project_config, "harmonyrun.ignore_test_failure", False)
    )
    dry_run = bool(choose(args.dry_run, project_config, "workflow.dry_run", False))

    if level not in (None, "L0", "L1", "L2"):
        raise RuntimeError("harmonyrun.level must be one of L0, L1, L2")
    if save_trajectory not in {"none", "step", "action"}:
        raise RuntimeError("harmonyrun.save_trajectory must be one of none, step, action")
    if devices is None:
        devices = []
    if not isinstance(devices, list):
        raise RuntimeError("harmonyrun.devices must be an inline list, for example: devices: [SERIAL]")

    print(f"Workspace: {workspace}", flush=True)
    print(f"Config: {project_config_path}", flush=True)
    print(f"App package: {app_package}", flush=True)
    print(f"Output root: {workspace / output_root}", flush=True)
    print(f"Result root: {workspace / result_root}", flush=True)

    prd_input = args.prd_path or args.prd_path_arg
    if not prd_input:
        prd_input = input(
            "Please input PRD file path, category (requirement), or all: "
        )

    targets = resolve_prd_targets(prd_input, workspace)
    print(f"Resolved PRD target(s): {len(targets)}", flush=True)
    for index, target in enumerate(targets, 1):
        print(f"  {index}. {target}", flush=True)

    base_url = f"http://{host}:{port}"
    server_process = None
    directory_query = None

    try:
        if not dry_run:
            server_process = start_opencode_server(workspace, host, port, base_url)
            directory_query = urllib.parse.quote(str(workspace), safe="")

        failures: list[tuple[Path, str]] = []
        for index, prd_file in enumerate(targets, 1):
            print("", flush=True)
            print(f"Batch progress: {index}/{len(targets)}", flush=True)
            try:
                generate_and_run_prd(
                    prd_file=prd_file,
                    workspace=workspace,
                    project_config_path=project_config_path,
                    output_root=output_root,
                    result_root=result_root,
                    app_package=app_package,
                    base_url=base_url if not dry_run else None,
                    directory_query=directory_query,
                    model=model,
                    agent=agent,
                    timeout_minutes=timeout_minutes,
                    generate_only=generate_only,
                    harmonyrun_config_text=harmonyrun_config_text,
                    case_id=case_id,
                    level=level,
                    devices=devices,
                    save_trajectory=save_trajectory,
                    ignore_test_failure=ignore_test_failure,
                    dry_run=dry_run,
                )
            except Exception as exc:
                failures.append((prd_file, str(exc)))
                print(f"ERROR while processing {prd_file}: {exc}", file=sys.stderr, flush=True)

        print("", flush=True)
        print("Batch summary:", flush=True)
        print(f"  total: {len(targets)}", flush=True)
        print(f"  passed: {len(targets) - len(failures)}", flush=True)
        print(f"  failed: {len(failures)}", flush=True)
        if failures:
            for prd_file, reason in failures:
                print(f"  - {prd_file}: {reason}", flush=True)
            return 1

        return 0
    finally:
        if server_process is not None and not keep_server:
            pid = server_process.pid
            stop_opencode_server(server_process)
            print(f"Stopped OpenCode server process {pid}", flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
    except (RuntimeError, TimeoutError, urllib.error.URLError) as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
