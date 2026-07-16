"""Tools primitive — tool system with ABC interface and built-in tools."""

from __future__ import annotations

from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec

# Import built-in tools to trigger @ToolRegistry.register() decorators.
# Each is wrapped in try/except so the package loads even before the
# individual tool modules are created.
try:
    import openjarvis.tools.calculator  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.think  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.retrieval  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.llm_tool  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.file_read  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.web_search  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.code_interpreter  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.code_interpreter_docker  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.repl  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.storage_tools  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.mcp_adapter  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.channel_tools  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.http_request  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.docker_shell_exec  # noqa: F401
    import openjarvis.tools.shell_exec  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.memory_manage  # noqa: F401
except ImportError:
    pass
try:
    import openjarvis.tools.user_profile_manage  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.skill_manage  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.file_write  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.apply_patch  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.git_tool  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.db_query  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.pdf_tool  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.image_tool  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.audio_tool  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.knowledge_tools  # noqa: F401
except ImportError:
    pass

# Connector-backed knowledge tools (knowledge_search, knowledge_sql). These
# were only imported on the managed-agent routes, so the main chat agent
# never got them even when config listed them.
try:
    import openjarvis.tools.knowledge_search  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.knowledge_sql  # noqa: F401
except ImportError:
    pass

# Slim project-context surface for external MCP clients (Cursor, OpenCode):
# project_list + project_context with dual-layer (graph + code) routing.
try:
    import openjarvis.tools.project_context  # noqa: F401
except ImportError:
    pass

# Deterministic memory routing: remember(content, kind) picks the right
# store (memory.db / MEMORY.md / USER.md / Obsidian vault) server-side.
try:
    import openjarvis.tools.remember  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.text_to_speech  # noqa: F401
except ImportError:
    pass

# Self-management tools: platform config, encrypted credential vault, and
# OAuth connector onboarding from chat.
try:
    import openjarvis.tools.config_manage  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.credential_manage  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.connector_manage  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.mcp_manage  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.digest_collect  # noqa: F401
except ImportError:
    pass

# Scheduler tools (schedule_task, list_scheduled_tasks, ...). These live under
# openjarvis.scheduler.tools and were not imported anywhere on the serve path,
# so the orchestrator never actually got them even though config lists them.
try:
    import openjarvis.scheduler.tools  # noqa: F401
except ImportError:
    pass

# Playwright browser automation (browser_navigate, browser_click, ...).
# Imported here so the orchestrator's configured browser_* tools register
# at `jarvis serve` startup — without this they only registered on the
# managed-agent routes, so the main chat agent could never browse.
try:
    import openjarvis.tools.browser  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.browser_axtree  # noqa: F401
except ImportError:
    pass

__all__ = ["BaseTool", "ToolExecutor", "ToolSpec"]
