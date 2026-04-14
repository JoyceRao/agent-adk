from google.adk.agents.llm_agent import Agent
from google.adk.models.lite_llm import LiteLlm
import os

try:
    from .prompt import (
        ANALYSIS_AGENT_INSTRUCTION,
        FILTER_AGENT_INSTRUCTION,
        REPORT_AGENT_INSTRUCTION,
        ROOT_AGENT_INSTRUCTION,
    )
    from .tools import (
        analyze_and_generate_report,
        analyze_log_with_source,
        build_timeline,
        filter_logs,
        generate_markdown_report,
        list_skills,
        route_by_skill,
        scan_patterns_full,
    )
except ImportError:
    from prompt import (
        ANALYSIS_AGENT_INSTRUCTION,
        FILTER_AGENT_INSTRUCTION,
        REPORT_AGENT_INSTRUCTION,
        ROOT_AGENT_INSTRUCTION,
    )
    from tools import (
        analyze_and_generate_report,
        analyze_log_with_source,
        build_timeline,
        filter_logs,
        generate_markdown_report,
        list_skills,
        route_by_skill,
        scan_patterns_full,
    )


# 兼容两套环境变量：
# 1) DOUBAO_*（推荐，豆包场景）
# 2) OPENAI_*（向后兼容）
# model_name = (os.getenv("DOUBAO_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-5.3-codex").strip()
# openai_base_url = (os.getenv("DOUBAO_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").strip()
# openai_api_key = (os.getenv("DOUBAO_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()

model_name = os.getenv("DOUBAO_MODEL") 
openai_base_url = os.getenv("DOUBAO_BASE_URL") 
openai_api_key = os.getenv("DOUBAO_API_KEY")

normalized_model_name = model_name if "/" in model_name else f"openai/{model_name}"

# 对自定义 OpenAI 兼容网关（如豆包 ARK）：
# 使用 openai provider 路径，避免 openai_like + endpoint-id 触发
# “Unmapped LLM provider for this endpoint”。
use_openai_like = bool(openai_base_url) and "api.openai.com" not in openai_base_url
agent_model = normalized_model_name
if use_openai_like:
    # openai provider 对这类 endpoint id 兼容性更稳定。
    gateway_model_name = model_name if "/" in model_name else f"openai/{model_name}"
    agent_model = LiteLlm(
        model=gateway_model_name,
        custom_llm_provider="openai",
        api_base=openai_base_url,
        api_key=openai_api_key or None,
        drop_params=True,
    )

# 子 Agent 1：仅做筛选
filter_agent = Agent(
    model=agent_model,
    name="filter_agent",
    description="Filter logs by time/type/level/keywords and return compact previews.",
    instruction=FILTER_AGENT_INSTRUCTION,
    tools=[filter_logs],
)

# 子 Agent 2：仅做日志+源码联合分析
analysis_agent = Agent(
    model=agent_model,
    name="analysis_agent",
    description="Analyze filtered logs with source code correlation and statistical indicators.",
    instruction=ANALYSIS_AGENT_INSTRUCTION,
    tools=[scan_patterns_full, build_timeline, analyze_log_with_source],
)

# 子 Agent 3：仅做 CRISP-L 报告生成/落盘
report_agent = Agent(
    model=agent_model,
    name="report_agent",
    description="Generate CRISP-L markdown report and write report file when requested.",
    instruction=REPORT_AGENT_INSTRUCTION,
    tools=[generate_markdown_report, analyze_and_generate_report],
)

root_agent = Agent(
    model=agent_model,
    name="root_agent",
    description="Root orchestrator for a 3-agent log analysis workflow.",
    instruction=ROOT_AGENT_INSTRUCTION,
    # 让 root 以编排为主，能力通过子 Agent 提供。
    # 同时暴露 analyze_and_generate_report，兼容用户“直接点名工具调用”的输入习惯。
    tools=[
        list_skills,
        route_by_skill,
        analyze_and_generate_report,
    ],
    sub_agents=[
        filter_agent,
        analysis_agent,
        report_agent,
    ],
)
