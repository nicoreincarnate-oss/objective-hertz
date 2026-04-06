"""Tests for Phase 27-02: Skills + hooks integration.

Verifies that skill frontmatter can declare hooks, that those hooks are
registered as session-scoped entries in the HookRegistry when a skill is
executed, and that they are properly cleaned up.
"""

import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SKILL_WITH_HOOKS = textwrap.dedent("""\
    ---
    name: email_compose
    description: Draft personalized outreach emails
    hooks:
      - point: pre_email_send
        type: function
        handler: validate_email_content
      - point: post_email_send
        type: function
        handler: log_email_sent
    ---

    # Email Compose Skill

    Draft emails for outreach campaigns.

    ```python
    def validate_email_content(context):
        subject = context.get("subject", "")
        if not subject:
            return False
        return True

    def log_email_sent(context):
        return True
    ```
""")

SKILL_WITHOUT_HOOKS = textwrap.dedent("""\
    ---
    name: simple_skill
    description: A skill with no hooks section
    tags: [simple, test]
    ---

    # Simple Skill

    Does something simple.
""")

SKILL_WITH_COMMAND_HOOK = textwrap.dedent("""\
    ---
    name: deploy_skill
    description: Deploy to production
    hooks:
      - point: pre_stage_execute
        type: command
        handler: echo "running tests"
    ---

    # Deploy Skill

    Deploys things.
""")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_hooks_registry():
    """Reset the hook registry singleton before each test."""
    from openjarvis.core.hooks import reset_registry
    reset_registry()
    yield
    reset_registry()


@pytest.fixture(autouse=True)
def _clear_frontmatter_cache():
    """Clear the frontmatter cache before each test.

    We must ensure an event loop exists before importing shared.skill_loader,
    because it transitively imports shared.llm_client which instantiates an
    asyncio.Lock at module level.  Python 3.9 requires a running loop for that.
    """
    import asyncio
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    from shared.skill_loader import _FRONTMATTER_CACHE
    _FRONTMATTER_CACHE.clear()
    yield
    _FRONTMATTER_CACHE.clear()


@pytest.fixture
def skill_dir(tmp_path):
    """Create a temporary skill directory with test skills."""
    email_dir = tmp_path / "email_compose"
    email_dir.mkdir()
    (email_dir / "SKILL.md").write_text(SKILL_WITH_HOOKS)

    simple_dir = tmp_path / "simple_skill"
    simple_dir.mkdir()
    (simple_dir / "SKILL.md").write_text(SKILL_WITHOUT_HOOKS)

    deploy_dir = tmp_path / "deploy_skill"
    deploy_dir.mkdir()
    (deploy_dir / "SKILL.md").write_text(SKILL_WITH_COMMAND_HOOK)

    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSkillFrontmatterHooksParsed:
    """test_skill_frontmatter_hooks_parsed: hooks section extracted from frontmatter."""

    def test_hooks_parsed_from_frontmatter(self, skill_dir):
        """Hooks section is correctly extracted from skill frontmatter."""
        os.environ["ANATOMY_LIFECYCLE_HOOKS"] = "true"
        try:
            from shared.skill_loader import _parse_frontmatter

            skill_path = skill_dir / "email_compose" / "SKILL.md"
            meta = _parse_frontmatter(skill_path)

            assert meta is not None
            assert "hooks" in meta
            assert len(meta["hooks"]) == 2

            hook1 = meta["hooks"][0]
            assert hook1["point"] == "pre_email_send"
            assert hook1["type"] == "function"
            assert hook1["handler"] == "validate_email_content"

            hook2 = meta["hooks"][1]
            assert hook2["point"] == "post_email_send"
            assert hook2["type"] == "function"
            assert hook2["handler"] == "log_email_sent"
        finally:
            os.environ.pop("ANATOMY_LIFECYCLE_HOOKS", None)

    def test_command_hook_parsed(self, skill_dir):
        """Command-type hooks are parsed correctly."""
        os.environ["ANATOMY_LIFECYCLE_HOOKS"] = "true"
        try:
            from shared.skill_loader import _parse_frontmatter

            skill_path = skill_dir / "deploy_skill" / "SKILL.md"
            meta = _parse_frontmatter(skill_path)

            assert meta is not None
            assert len(meta["hooks"]) == 1
            hook = meta["hooks"][0]
            assert hook["point"] == "pre_stage_execute"
            assert hook["type"] == "command"
            assert hook["handler"] == 'echo "running tests"'
        finally:
            os.environ.pop("ANATOMY_LIFECYCLE_HOOKS", None)


class TestHooksRegisteredOnSkillExecute:
    """test_hooks_registered_on_skill_execute: executing skill registers hooks in HookRegistry."""

    @pytest.mark.asyncio
    async def test_hooks_registered_on_execute(self, skill_dir):
        """Hooks from skill frontmatter are registered in HookRegistry on execute."""
        os.environ["ANATOMY_LIFECYCLE_HOOKS"] = "true"
        os.environ["SKILL_LOADER_ALLOW_UNSIGNED"] = "true"
        os.environ["ENVIRONMENT"] = "development"
        try:
            from openjarvis.core.hooks import HookPoint, get_hook_registry
            from shared.skill_loader import SKILL_DIRS, execute_skill

            # Point SKILL_DIRS to our temp directory
            original_dirs = SKILL_DIRS[:]
            SKILL_DIRS.clear()
            SKILL_DIRS.append(skill_dir)

            try:
                with patch("shared.skill_loader.llm") as mock_llm:
                    mock_llm.generate = AsyncMock(return_value="Generated email")

                    result = await execute_skill("email_compose", "Write an email")

                    registry = get_hook_registry()
                    pre_hooks = registry._hooks[HookPoint.PRE_EMAIL_SEND]
                    post_hooks = registry._hooks[HookPoint.POST_EMAIL_SEND]

                    assert len(pre_hooks) == 1
                    assert len(post_hooks) == 1
                    assert pre_hooks[0].name == "email_compose:pre_email_send:validate_email_content"
                    assert post_hooks[0].name == "email_compose:post_email_send:log_email_sent"
                    assert result == "Generated email"
            finally:
                SKILL_DIRS.clear()
                SKILL_DIRS.extend(original_dirs)
        finally:
            os.environ.pop("ANATOMY_LIFECYCLE_HOOKS", None)
            os.environ.pop("SKILL_LOADER_ALLOW_UNSIGNED", None)
            os.environ.pop("ENVIRONMENT", None)

    @pytest.mark.asyncio
    async def test_command_hook_registered(self, skill_dir):
        """Command-type hooks are registered with the command string as handler."""
        os.environ["ANATOMY_LIFECYCLE_HOOKS"] = "true"
        os.environ["SKILL_LOADER_ALLOW_UNSIGNED"] = "true"
        os.environ["ENVIRONMENT"] = "development"
        try:
            from openjarvis.core.hooks import HookPoint, HookType, get_hook_registry
            from shared.skill_loader import SKILL_DIRS, execute_skill

            original_dirs = SKILL_DIRS[:]
            SKILL_DIRS.clear()
            SKILL_DIRS.append(skill_dir)

            try:
                with patch("shared.skill_loader.llm") as mock_llm:
                    mock_llm.generate = AsyncMock(return_value="Deployed")

                    await execute_skill("deploy_skill", "Deploy now")

                    registry = get_hook_registry()
                    pre_hooks = registry._hooks[HookPoint.PRE_STAGE_EXECUTE]

                    assert len(pre_hooks) == 1
                    assert pre_hooks[0].type == HookType.COMMAND
                    assert pre_hooks[0].handler == 'echo "running tests"'
            finally:
                SKILL_DIRS.clear()
                SKILL_DIRS.extend(original_dirs)
        finally:
            os.environ.pop("ANATOMY_LIFECYCLE_HOOKS", None)
            os.environ.pop("SKILL_LOADER_ALLOW_UNSIGNED", None)
            os.environ.pop("ENVIRONMENT", None)


class TestHooksAreSessionScoped:
    """test_hooks_are_session_scoped: hooks have session_scoped=True."""

    @pytest.mark.asyncio
    async def test_session_scoped_flag(self, skill_dir):
        """All hooks registered from skill frontmatter have session_scoped=True."""
        os.environ["ANATOMY_LIFECYCLE_HOOKS"] = "true"
        os.environ["SKILL_LOADER_ALLOW_UNSIGNED"] = "true"
        os.environ["ENVIRONMENT"] = "development"
        try:
            from openjarvis.core.hooks import HookPoint, get_hook_registry
            from shared.skill_loader import SKILL_DIRS, execute_skill

            original_dirs = SKILL_DIRS[:]
            SKILL_DIRS.clear()
            SKILL_DIRS.append(skill_dir)

            try:
                with patch("shared.skill_loader.llm") as mock_llm:
                    mock_llm.generate = AsyncMock(return_value="done")

                    await execute_skill("email_compose", "Write")

                    registry = get_hook_registry()
                    for point in HookPoint:
                        for hook in registry._hooks[point]:
                            assert hook.session_scoped is True, (
                                f"Hook {hook.name} should be session_scoped"
                            )
            finally:
                SKILL_DIRS.clear()
                SKILL_DIRS.extend(original_dirs)
        finally:
            os.environ.pop("ANATOMY_LIFECYCLE_HOOKS", None)
            os.environ.pop("SKILL_LOADER_ALLOW_UNSIGNED", None)
            os.environ.pop("ENVIRONMENT", None)


class TestSessionCleanupRemovesHooks:
    """test_session_cleanup_removes_hooks: clear_session_hooks removes skill hooks."""

    @pytest.mark.asyncio
    async def test_clear_session_hooks(self, skill_dir):
        """Calling clear_session_hooks() removes all skill-registered hooks."""
        os.environ["ANATOMY_LIFECYCLE_HOOKS"] = "true"
        os.environ["SKILL_LOADER_ALLOW_UNSIGNED"] = "true"
        os.environ["ENVIRONMENT"] = "development"
        try:
            from openjarvis.core.hooks import HookPoint, get_hook_registry
            from shared.skill_loader import SKILL_DIRS, execute_skill

            original_dirs = SKILL_DIRS[:]
            SKILL_DIRS.clear()
            SKILL_DIRS.append(skill_dir)

            try:
                with patch("shared.skill_loader.llm") as mock_llm:
                    mock_llm.generate = AsyncMock(return_value="done")

                    await execute_skill("email_compose", "Write")

                    registry = get_hook_registry()
                    # Verify hooks exist
                    assert len(registry._hooks[HookPoint.PRE_EMAIL_SEND]) == 1
                    assert len(registry._hooks[HookPoint.POST_EMAIL_SEND]) == 1

                    # Clear session hooks
                    registry.clear_session_hooks()

                    # Verify hooks are gone
                    assert len(registry._hooks[HookPoint.PRE_EMAIL_SEND]) == 0
                    assert len(registry._hooks[HookPoint.POST_EMAIL_SEND]) == 0
            finally:
                SKILL_DIRS.clear()
                SKILL_DIRS.extend(original_dirs)
        finally:
            os.environ.pop("ANATOMY_LIFECYCLE_HOOKS", None)
            os.environ.pop("SKILL_LOADER_ALLOW_UNSIGNED", None)
            os.environ.pop("ENVIRONMENT", None)


class TestNoHooksSectionNoRegistration:
    """test_no_hooks_section_no_registration: skill without hooks section registers nothing."""

    @pytest.mark.asyncio
    async def test_no_hooks_no_registration(self, skill_dir):
        """A skill with no hooks section registers zero hooks."""
        os.environ["ANATOMY_LIFECYCLE_HOOKS"] = "true"
        os.environ["SKILL_LOADER_ALLOW_UNSIGNED"] = "true"
        os.environ["ENVIRONMENT"] = "development"
        try:
            from openjarvis.core.hooks import get_hook_registry
            from shared.skill_loader import SKILL_DIRS, execute_skill

            original_dirs = SKILL_DIRS[:]
            SKILL_DIRS.clear()
            SKILL_DIRS.append(skill_dir)

            try:
                with patch("shared.skill_loader.llm") as mock_llm:
                    mock_llm.generate = AsyncMock(return_value="done")

                    await execute_skill("simple_skill", "Do something")

                    registry = get_hook_registry()
                    total = sum(len(h) for h in registry._hooks.values())
                    assert total == 0, f"Expected 0 hooks, found {total}"
            finally:
                SKILL_DIRS.clear()
                SKILL_DIRS.extend(original_dirs)
        finally:
            os.environ.pop("ANATOMY_LIFECYCLE_HOOKS", None)
            os.environ.pop("SKILL_LOADER_ALLOW_UNSIGNED", None)
            os.environ.pop("ENVIRONMENT", None)


class TestFlagOffNoRegistration:
    """test_flag_off_no_registration: hooks not registered when flag off."""

    @pytest.mark.asyncio
    async def test_flag_off_skips_registration(self, skill_dir):
        """When ANATOMY_LIFECYCLE_HOOKS is not set, no hooks are registered."""
        os.environ.pop("ANATOMY_LIFECYCLE_HOOKS", None)
        os.environ["SKILL_LOADER_ALLOW_UNSIGNED"] = "true"
        os.environ["ENVIRONMENT"] = "development"
        try:
            from openjarvis.core.hooks import get_hook_registry
            from shared.skill_loader import SKILL_DIRS, execute_skill

            original_dirs = SKILL_DIRS[:]
            SKILL_DIRS.clear()
            SKILL_DIRS.append(skill_dir)

            try:
                with patch("shared.skill_loader.llm") as mock_llm:
                    mock_llm.generate = AsyncMock(return_value="done")

                    await execute_skill("email_compose", "Write an email")

                    registry = get_hook_registry()
                    total = sum(len(h) for h in registry._hooks.values())
                    assert total == 0, f"Expected 0 hooks when flag is off, found {total}"
            finally:
                SKILL_DIRS.clear()
                SKILL_DIRS.extend(original_dirs)
        finally:
            os.environ.pop("SKILL_LOADER_ALLOW_UNSIGNED", None)
            os.environ.pop("ENVIRONMENT", None)

    def test_frontmatter_hooks_empty_when_flag_off(self, skill_dir):
        """When flag is off, frontmatter parsing returns empty hooks list."""
        os.environ.pop("ANATOMY_LIFECYCLE_HOOKS", None)
        from shared.skill_loader import _parse_frontmatter

        skill_path = skill_dir / "email_compose" / "SKILL.md"
        meta = _parse_frontmatter(skill_path)

        assert meta is not None
        assert meta["hooks"] == []


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------

class TestParseHooksYaml:
    """Unit tests for _parse_hooks_yaml."""

    def test_basic_parsing(self):
        from shared.skill_loader import _parse_hooks_yaml

        yaml_block = textwrap.dedent("""\
            name: test_skill
            hooks:
              - point: pre_email_send
                type: function
                handler: validate_email
              - point: post_email_send
                type: command
                handler: echo done
            version: 1
        """)

        hooks = _parse_hooks_yaml(yaml_block)
        assert len(hooks) == 2
        assert hooks[0] == {"point": "pre_email_send", "type": "function", "handler": "validate_email"}
        assert hooks[1] == {"point": "post_email_send", "type": "command", "handler": "echo done"}

    def test_no_hooks_section(self):
        from shared.skill_loader import _parse_hooks_yaml

        yaml_block = "name: test\ndescription: no hooks here\n"
        hooks = _parse_hooks_yaml(yaml_block)
        assert hooks == []

    def test_empty_hooks_section(self):
        from shared.skill_loader import _parse_hooks_yaml

        yaml_block = "name: test\nhooks:\nversion: 1\n"
        hooks = _parse_hooks_yaml(yaml_block)
        assert hooks == []


class TestResolveFunctionFromSkill:
    """Unit tests for _resolve_function_from_skill."""

    def test_resolves_function(self):
        from shared.skill_loader import _resolve_function_from_skill

        content = textwrap.dedent("""\
            # My Skill

            ```python
            def my_handler(context):
                return True
            ```
        """)

        fn = _resolve_function_from_skill("my_handler", content, _signature_verified=True)
        assert fn is not None
        assert callable(fn)
        assert fn({}) is True

    def test_missing_function(self):
        from shared.skill_loader import _resolve_function_from_skill

        content = "# No code blocks here"
        fn = _resolve_function_from_skill("nonexistent", content)
        assert fn is None

    def test_syntax_error_in_block(self):
        from shared.skill_loader import _resolve_function_from_skill

        content = textwrap.dedent("""\
            ```python
            def broken(
                return oops
            ```
        """)

        fn = _resolve_function_from_skill("broken", content)
        assert fn is None
