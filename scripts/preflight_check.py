#!/usr/bin/env python3
"""ADK startup preflight checks for this project.

Goals:
1) Find common runtime blockers before `adk run` / `adk web`.
2) Especially catch proxy misconfigurations that break model calls.
3) Provide actionable fixes in Chinese.
4) Guard ADK extension sync: new skill/tool must update routing and exports.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _ok(msg: str) -> None:
    print(f"[OK] {msg}")


def _warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}")


def _mask(text: str) -> str:
    if not text:
        return ""
    if len(text) <= 10:
        return "***"
    return f"{text[:6]}***{text[-4:]}"


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out

    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _get_env(key: str, env_file_vars: dict[str, str]) -> str:
    return (os.getenv(key, "") or env_file_vars.get(key, "")).strip()


def _check_local_proxy(url: str, timeout: float = 0.8) -> tuple[bool, str]:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port
    if not host or port is None:
        return (True, "代理格式无法解析，跳过连通性检测")

    is_local = host in {"127.0.0.1", "localhost", "::1"}
    if not is_local:
        return (True, "非本地代理，跳过本地端口探测")

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (True, f"本地代理可达: {host}:{port}")
    except OSError as exc:
        return (False, f"本地代理不可达: {host}:{port} ({exc})")


def _check_llm_endpoint_connectivity(endpoint_url: str, timeout: float = 1.5) -> tuple[bool, str]:
    parsed = urlparse(endpoint_url)
    host = parsed.hostname or ""
    if not host:
        return (False, f"LLM 端点格式非法: {endpoint_url}")

    port = parsed.port
    if port is None:
        port = 443 if (parsed.scheme or "https").lower() == "https" else 80

    try:
        # 先 DNS，便于给出更明确错误。
        socket.getaddrinfo(host, port)
    except OSError as exc:
        return (False, f"LLM 端点 DNS 解析失败: {host}:{port} ({exc})")

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (True, f"LLM 端点可达: {host}:{port}")
    except OSError as exc:
        return (False, f"LLM 端点不可达: {host}:{port} ({exc})")


def _load_skill_names_from_skill_dir(root_dir: Path) -> set[str]:
    """从 SKILL/*/SKILL.md 读取 skill 名称。"""
    skill_root = root_dir / "SKILL"
    if not skill_root.exists() or not skill_root.is_dir():
        return set()

    skill_names: set[str] = set()
    for child in skill_root.iterdir():
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.exists():
            continue

        text = skill_md.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        parsed_name = ""
        if lines and lines[0].strip() == "---":
            for line in lines[1:]:
                if line.strip() == "---":
                    break
                match = re.match(r"^\s*name\s*:\s*([A-Za-z0-9_-]+)\s*$", line)
                if match:
                    parsed_name = match.group(1).strip()
                    break
        skill_names.add(parsed_name or child.name)
    return skill_names


def _load_tools_shim_all(root_dir: Path) -> list[str]:
    """通过文件路径加载 tools.py 兼容层，读取 __all__。"""
    shim_path = root_dir / "tools.py"
    if not shim_path.exists():
        return []

    spec = importlib.util.spec_from_file_location("_codex_tools_shim_check", shim_path)
    if not spec or not spec.loader:
        return []

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[assignment]
    return list(getattr(module, "__all__", []) or [])


def _check_adk_extension_sync(root_dir: Path) -> tuple[list[str], list[str]]:
    """检查 Skill/Tool 扩展是否同步到 ADK 关键接入点。"""
    errors: list[str] = []
    warnings: list[str] = []

    try:
        from tools import __all__ as tools_package_all  # type: ignore
        from tools.skill_router import SKILL_DEFINITIONS  # type: ignore
    except Exception as exc:
        errors.append(f"扩展同步检查失败（导入 tools/skill_router 异常）: {exc}")
        return errors, warnings

    router_skills = set(SKILL_DEFINITIONS.keys())
    declared_skills = _load_skill_names_from_skill_dir(root_dir)

    missing_in_router = sorted(declared_skills - router_skills)
    if missing_in_router:
        errors.append(
            "以下 SKILL 已声明但未注册到 tools/skill_router.SKILL_DEFINITIONS: "
            + ", ".join(missing_in_router)
        )

    missing_in_skill_dir = sorted(router_skills - declared_skills)
    if missing_in_skill_dir:
        errors.append(
            "以下 skill 已在 tools/skill_router 注册，但 SKILL 目录缺少对应 SKILL.md: "
            + ", ".join(missing_in_skill_dir)
        )

    prompt_path = root_dir / "prompt.py"
    prompt_text = prompt_path.read_text(encoding="utf-8", errors="ignore") if prompt_path.exists() else ""
    missing_in_prompt = sorted([skill for skill in router_skills if skill not in prompt_text])
    if missing_in_prompt:
        errors.append(
            "以下 skill 未在 prompt.py 显式出现（需同步更新路由说明）: "
            + ", ".join(missing_in_prompt)
        )

    package_exports = set(str(x).strip() for x in (tools_package_all or []) if str(x).strip())
    shim_exports = set(str(x).strip() for x in _load_tools_shim_all(root_dir) if str(x).strip())
    only_in_package = sorted(package_exports - shim_exports)
    only_in_shim = sorted(shim_exports - package_exports)
    if only_in_package or only_in_shim:
        parts: list[str] = []
        if only_in_package:
            parts.append("仅 tools/__init__.py 导出: " + ", ".join(only_in_package))
        if only_in_shim:
            parts.append("仅 tools.py 导出: " + ", ".join(only_in_shim))
        errors.append("tools 导出不一致（新增 tool 后需同步兼容层）: " + "；".join(parts))

    if not declared_skills:
        warnings.append("未在 SKILL/ 目录发现可用 skill 声明。")

    return errors, warnings


def run_checks(args: argparse.Namespace) -> int:
    print("== ADK 启动前自检 ==")
    print(f"项目目录: {ROOT_DIR}")

    failed = 0
    warned = 0

    env_file = ROOT_DIR / ".env"
    env_file_vars = _read_env_file(env_file)
    if env_file.exists():
        _ok(f".env 存在: {env_file}")
    else:
        _warn(f".env 不存在: {env_file}（可选）")
        warned += 1

    # 1) Python/ADK imports
    try:
        import google.adk  # noqa: F401

        _ok("google-adk 可导入")
    except Exception as exc:  # pragma: no cover
        _fail(f"google-adk 导入失败: {exc}")
        failed += 1

    # 2) Agent/Tools wiring
    try:
        from agent import root_agent  # type: ignore

        sub_agent_names = [a.name for a in root_agent.sub_agents]
        _ok(f"root_agent 加载成功，sub_agents={sub_agent_names}")
    except Exception as exc:
        _fail(f"agent/root_agent 加载失败: {exc}")
        failed += 1

    try:
        from tools import list_skills, route_by_skill  # type: ignore

        skill_names = [x.get("skill_name", "") for x in list_skills().get("skills", [])]
        _ok(f"工具可用: list_skills/route_by_skill，skills={skill_names}")
    except Exception as exc:
        _fail(f"tools.list_skills/route_by_skill 加载失败: {exc}")
        failed += 1

    # 2.1) 扩展同步守卫（新增 skill/tool 时必须同步 ADK 接入层）
    sync_errors, sync_warnings = _check_adk_extension_sync(ROOT_DIR)
    if sync_errors:
        for msg in sync_errors:
            _fail(msg)
            failed += 1
    else:
        _ok("扩展同步检查通过（SKILL 声明、skill_router、prompt、tools 导出已对齐）")
    for msg in sync_warnings:
        _warn(msg)
        warned += 1

    # 3) Env essentials
    # DOUBAO_* 优先，其次回退 OPENAI_*（兼容旧配置）。
    llm_model = _get_env("DOUBAO_MODEL", env_file_vars) or _get_env("OPENAI_MODEL", env_file_vars)
    llm_base_url = _get_env("DOUBAO_BASE_URL", env_file_vars) or _get_env("OPENAI_BASE_URL", env_file_vars)
    llm_api_key = _get_env("DOUBAO_API_KEY", env_file_vars) or _get_env("OPENAI_API_KEY", env_file_vars)

    if llm_model:
        _ok(f"LLM_MODEL={llm_model}")
    else:
        _warn("LLM_MODEL 未配置（建议设置 DOUBAO_MODEL 或 OPENAI_MODEL）")
        warned += 1

    if llm_base_url:
        _ok(f"LLM_BASE_URL={llm_base_url}")
    else:
        _warn("LLM_BASE_URL 未配置（仅在 OpenAI 兼容网关场景必需）")
        warned += 1

    if llm_api_key:
        _ok(f"LLM_API_KEY={_mask(llm_api_key)}")
    else:
        _fail("LLM_API_KEY 未配置（请设置 DOUBAO_API_KEY 或 OPENAI_API_KEY）")
        failed += 1

    # 4) Proxy checks (common blocker)
    proxy_keys = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]
    proxy_values = {k: os.getenv(k, "").strip() for k in proxy_keys if os.getenv(k, "").strip()}
    proxy_has_fail = False
    if proxy_values:
        _warn(f"检测到代理变量: {proxy_values}")
        warned += 1
        for key, value in proxy_values.items():
            ok, reason = _check_local_proxy(value)
            if ok:
                _ok(f"{key}: {reason}")
            else:
                _fail(f"{key}: {reason}")
                failed += 1
                proxy_has_fail = True
    else:
        _ok("未检测到代理变量")

    # 4.1) LLM 网关连通性（adk run 的硬前置条件）
    # 若配置了代理且代理端口探测通过，则网络最终经代理转发；此处不做直连探测，避免误报。
    llm_endpoint = llm_base_url or "https://api.openai.com/v1"
    if proxy_values and not proxy_has_fail:
        _warn("检测到代理变量，跳过 LLM 端点直连探测（请求将经代理转发）。")
        warned += 1
    else:
        ok, reason = _check_llm_endpoint_connectivity(llm_endpoint)
        if ok:
            _ok(reason)
        else:
            _fail(reason)
            failed += 1

    # 5) Path checks
    if args.log_path:
        p = (ROOT_DIR / args.log_path).resolve() if not Path(args.log_path).is_absolute() else Path(args.log_path)
        if p.exists() and p.is_file():
            _ok(f"log_path 可用: {p}")
        else:
            _fail(f"log_path 不存在: {p}")
            failed += 1

    source_root = (ROOT_DIR / args.source_root).resolve() if not Path(args.source_root).is_absolute() else Path(args.source_root)
    if source_root.exists() and source_root.is_dir():
        _ok(f"source_root 可用: {source_root}")
    else:
        _warn(f"source_root 不存在或非目录: {source_root}")
        warned += 1

    rule_path = (ROOT_DIR / args.rule_path).resolve() if not Path(args.rule_path).is_absolute() else Path(args.rule_path)
    if rule_path.exists() and rule_path.is_file():
        _ok(f"rule_path 可用: {rule_path}")
    else:
        _warn(f"rule_path 不存在: {rule_path}")
        warned += 1

    output_dir = (ROOT_DIR / args.output_dir).resolve() if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        test_path = output_dir / ".preflight_write_test"
        test_path.write_text("ok", encoding="utf-8")
        test_path.unlink(missing_ok=True)
        _ok(f"output_dir 可写: {output_dir}")
    except Exception as exc:
        _fail(f"output_dir 不可写: {output_dir} ({exc})")
        failed += 1

    # 6) Optional local route smoke test (no LLM call)
    if args.route_smoke:
        if not args.log_path:
            _warn("route_smoke 已开启但未提供 --log-path，跳过")
            warned += 1
        else:
            try:
                from tools import route_by_skill  # type: ignore

                res: dict[str, Any] = route_by_skill(
                    skill_name=args.skill_name,
                    log_path=args.log_path,
                    source_root=args.source_root,
                    rule_path=args.rule_path,
                    max_output_lines=args.max_output_lines,
                    title=args.title,
                    output_dir=args.output_dir,
                )
                if "error" in res:
                    _fail(f"route_by_skill 本地冒烟失败: {res['error']}")
                    failed += 1
                else:
                    _ok(
                        "route_by_skill 本地冒烟通过: "
                        f"normalized={res.get('normalized_skill_name', '')}, "
                        f"report_path={res.get('report_path', '')}"
                    )
            except Exception as exc:
                _fail(f"route_by_skill 本地冒烟异常: {exc}")
                failed += 1

    print("\n== 结果汇总 ==")
    print(f"FAIL={failed}, WARN={warned}")
    if failed > 0:
        print("建议先处理 FAIL 项，再执行 adk run / adk web。")
        if proxy_values:
            print("常用修复示例: unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy")
        return 2
    print("自检通过，可以启动 ADK。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ADK 启动前自检（代理/环境/路径/工具路由/扩展同步）")
    parser.add_argument("--log-path", default="", help="可选：日志文件路径，用于路径检查与路由冒烟")
    parser.add_argument("--source-root", default="source/GZCheSuPaiApp", help="源码根目录")
    parser.add_argument("--rule-path", default="source/log_rule.md", help="规则文件路径")
    parser.add_argument("--output-dir", default="output", help="输出目录")
    parser.add_argument("--skill-name", default="crisp-l-report-assistant", help="route_by_skill 冒烟 skill")
    parser.add_argument("--max-output-lines", type=int, default=200, help="route_by_skill 冒烟参数")
    parser.add_argument("--title", default="日志分析报告", help="route_by_skill 冒烟参数")
    parser.add_argument(
        "--route-smoke",
        action="store_true",
        help="可选：执行 route_by_skill 本地冒烟（不经过 LLM）",
    )
    args = parser.parse_args()
    return run_checks(args)


if __name__ == "__main__":
    raise SystemExit(main())
