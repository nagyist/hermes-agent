"""Tests for the QQBot platform adapter."""
import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform, PlatformConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides):
    """Create a PlatformConfig suitable for QQBot."""
    defaults = {
        "enabled": True,
        "extra": {"appid": "test_appid"},
        "token": "test_secret",
    }
    defaults.update(overrides)
    return PlatformConfig(**defaults)


def _make_adapter(monkeypatch):
    """Create a QQBotAdapter with mocked botpy."""
    monkeypatch.setenv("QQBOT_APPID", "test_appid")
    monkeypatch.setenv("QQBOT_SECRET", "test_secret")
    # Mock botpy so we don't need the real SDK
    mock_botpy = MagicMock()
    mock_botpy.Intents.return_value = MagicMock()
    mock_botpy.Client = MagicMock
    monkeypatch.setitem(
        __import__("sys").modules, "botpy", mock_botpy
    )
    monkeypatch.setitem(
        __import__("sys").modules, "botpy.message", MagicMock()
    )

    import importlib
    import gateway.platforms.qqbot as qqmod
    # Ensure QQBOTPY_AVAILABLE is True
    monkeypatch.setattr(qqmod, "QQBOTPY_AVAILABLE", True)
    monkeypatch.setattr(qqmod, "botpy", mock_botpy)

    cfg = _make_config()
    adapter = qqmod.QQBotAdapter(cfg)
    return adapter


# ---------------------------------------------------------------------------
# Platform enum & config
# ---------------------------------------------------------------------------

class TestQQBotPlatformEnum:
    def test_qqbot_enum_exists(self):
        assert Platform.QQBOT.value == "qqbot"


class TestQQBotConfigLoading:
    def test_apply_env_overrides(self, monkeypatch):
        monkeypatch.setenv("QQBOT_APPID", "12345")
        monkeypatch.setenv("QQBOT_SECRET", "my_secret")
        from gateway.config import GatewayConfig, _apply_env_overrides

        config = GatewayConfig()
        _apply_env_overrides(config)
        assert Platform.QQBOT in config.platforms
        qc = config.platforms[Platform.QQBOT]
        assert qc.enabled is True
        assert qc.extra["appid"] == "12345"
        assert qc.token == "my_secret"

    def test_env_overrides_with_legacy_token(self, monkeypatch):
        """QQBOT_TOKEN is a legacy fallback for QQBOT_SECRET."""
        monkeypatch.setenv("QQBOT_APPID", "12345")
        monkeypatch.delenv("QQBOT_SECRET", raising=False)
        monkeypatch.setenv("QQBOT_TOKEN", "legacy_token")
        from gateway.config import GatewayConfig, _apply_env_overrides

        config = GatewayConfig()
        _apply_env_overrides(config)
        assert Platform.QQBOT in config.platforms
        assert config.platforms[Platform.QQBOT].token == "legacy_token"

    def test_connected_platforms_includes_qqbot(self, monkeypatch):
        monkeypatch.setenv("QQBOT_APPID", "12345")
        monkeypatch.setenv("QQBOT_SECRET", "my_secret")
        from gateway.config import GatewayConfig, _apply_env_overrides

        config = GatewayConfig()
        _apply_env_overrides(config)
        assert Platform.QQBOT in config.get_connected_platforms()

    def test_not_connected_without_secret(self, monkeypatch):
        monkeypatch.setenv("QQBOT_APPID", "12345")
        monkeypatch.delenv("QQBOT_SECRET", raising=False)
        monkeypatch.delenv("QQBOT_TOKEN", raising=False)
        from gateway.config import GatewayConfig, _apply_env_overrides

        config = GatewayConfig()
        _apply_env_overrides(config)
        assert Platform.QQBOT not in config.get_connected_platforms()

    def test_not_connected_without_appid(self, monkeypatch):
        monkeypatch.delenv("QQBOT_APPID", raising=False)
        monkeypatch.setenv("QQBOT_SECRET", "my_secret")
        from gateway.config import GatewayConfig, _apply_env_overrides

        config = GatewayConfig()
        _apply_env_overrides(config)
        assert Platform.QQBOT not in config.get_connected_platforms()

    def test_home_channel_set_from_env(self, monkeypatch):
        monkeypatch.setenv("QQBOT_APPID", "12345")
        monkeypatch.setenv("QQBOT_SECRET", "my_secret")
        monkeypatch.setenv("QQBOT_HOME_CHANNEL", "chan_123")
        from gateway.config import GatewayConfig, _apply_env_overrides

        config = GatewayConfig()
        _apply_env_overrides(config)
        hc = config.platforms[Platform.QQBOT].home_channel
        assert hc is not None
        assert hc.chat_id == "chan_123"


# ---------------------------------------------------------------------------
# Adapter unit tests (mocked botpy)
# ---------------------------------------------------------------------------

class TestQQBotAdapter:
    def test_adapter_init(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        assert adapter._appid == "test_appid"
        assert adapter._secret == "test_secret"
        assert adapter.MAX_MESSAGE_LENGTH == 2000

    def test_adapter_init_from_env(self, monkeypatch):
        """Adapter should fall back to env vars when config is empty."""
        monkeypatch.setenv("QQBOT_APPID", "env_appid")
        monkeypatch.setenv("QQBOT_SECRET", "env_secret")

        import gateway.platforms.qqbot as qqmod
        monkeypatch.setattr(qqmod, "QQBOTPY_AVAILABLE", True)
        monkeypatch.setattr(qqmod, "botpy", MagicMock())

        cfg = PlatformConfig(enabled=True, extra={}, token="")
        adapter = qqmod.QQBotAdapter(cfg)
        assert adapter._appid == "env_appid"
        assert adapter._secret == "env_secret"

    def test_clean_text(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        assert adapter._clean_text("<@!12345> hello world") == "hello world"
        assert adapter._clean_text("<@bot123> test") == "test"
        assert adapter._clean_text("no mentions") == "no mentions"
        assert adapter._clean_text("") == ""
        assert adapter._clean_text(None) == ""

    def test_is_duplicate(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        assert adapter._is_duplicate("msg1") is False
        assert adapter._is_duplicate("msg1") is True
        assert adapter._is_duplicate("msg2") is False

    def test_next_msg_seq(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter._msg_seq = 0
        seq1 = adapter._next_msg_seq()
        seq2 = adapter._next_msg_seq()
        assert seq1 == 1
        assert seq2 == 2

    def test_msg_seq_wraps(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter._msg_seq = 1_000_000
        seq = adapter._next_msg_seq()
        assert seq == 1  # Wrapped around

    def test_get_sender_id_from_author(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        msg = MagicMock()
        msg.author = MagicMock()
        msg.author.id = "user_123"
        assert adapter._get_sender_id(msg) == "user_123"

    def test_get_sender_id_string_attr(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        msg = MagicMock(spec=[])  # No auto-attributes
        msg.author = None
        msg.src_guild_id = None
        msg.group_openid = "group_abc"
        msg.openid = None
        assert adapter._get_sender_id(msg) == "group_abc"

    def test_get_sender_id_unknown(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        msg = MagicMock(spec=[])
        msg.author = None
        msg.src_guild_id = None
        msg.group_openid = None
        msg.openid = None
        assert adapter._get_sender_id(msg) == "unknown"

    def test_extract_attachments(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        att = MagicMock()
        att.content_type = "image/png"
        att.filename = "photo.png"
        att.url = "https://example.com/photo.png"
        att.width = 800
        att.height = 600
        att.size = 12345

        msg = MagicMock()
        msg.attachments = [att]
        result = adapter._extract_attachments(msg)
        assert len(result) == 1
        assert result[0]["content_type"] == "image/png"
        assert result[0]["url"] == "https://example.com/photo.png"

    def test_extract_attachments_empty(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        msg = MagicMock()
        msg.attachments = None
        assert adapter._extract_attachments(msg) == []


class TestQQBotResponderCache:
    def test_cache_and_retrieve(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        mock_responder = AsyncMock()
        adapter._responder_cache["chat1"] = ("group_at", mock_responder, time.time())
        result = adapter._get_cached_responder("chat1")
        assert result is not None
        assert result[0] == "group_at"

    def test_cache_expires(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        mock_responder = AsyncMock()
        # Set timestamp 20 minutes ago (past TTL)
        adapter._responder_cache["chat1"] = ("c2c", mock_responder, time.time() - 1200)
        result = adapter._get_cached_responder("chat1")
        assert result is None
        assert "chat1" not in adapter._responder_cache

    def test_cache_miss(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        assert adapter._get_cached_responder("nonexistent") is None

    def test_evict_stale(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        now = time.time()
        adapter._responder_cache["stale"] = ("c2c", AsyncMock(), now - 1200)
        adapter._responder_cache["fresh"] = ("group_at", AsyncMock(), now)
        adapter._evict_stale_responders()
        assert "stale" not in adapter._responder_cache
        assert "fresh" in adapter._responder_cache

    def test_evict_over_max(self, monkeypatch):
        import gateway.platforms.qqbot as qqmod
        adapter = _make_adapter(monkeypatch)
        now = time.time()
        # Fill beyond max
        for i in range(510):
            adapter._responder_cache[f"chat_{i}"] = ("c2c", AsyncMock(), now - i)
        adapter._evict_stale_responders()
        assert len(adapter._responder_cache) <= qqmod._RESPONDER_CACHE_MAX


class TestQQBotConnect:
    @pytest.mark.asyncio
    async def test_connect_missing_sdk(self, monkeypatch):
        monkeypatch.setenv("QQBOT_APPID", "test")
        monkeypatch.setenv("QQBOT_SECRET", "test")
        import gateway.platforms.qqbot as qqmod
        monkeypatch.setattr(qqmod, "QQBOTPY_AVAILABLE", False)

        cfg = _make_config()
        adapter = qqmod.QQBotAdapter(cfg)
        result = await adapter.connect()
        assert result is False

    @pytest.mark.asyncio
    async def test_connect_missing_credentials(self, monkeypatch):
        monkeypatch.delenv("QQBOT_APPID", raising=False)
        monkeypatch.delenv("QQBOT_SECRET", raising=False)
        import gateway.platforms.qqbot as qqmod
        monkeypatch.setattr(qqmod, "QQBOTPY_AVAILABLE", True)
        monkeypatch.setattr(qqmod, "botpy", MagicMock())

        cfg = PlatformConfig(enabled=True, extra={}, token="")
        adapter = qqmod.QQBotAdapter(cfg)
        result = await adapter.connect()
        assert result is False

    @pytest.mark.asyncio
    async def test_send_when_not_connected(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter._bot_client = None
        result = await adapter.send("chan_123", "hello")
        assert result.success is False
        assert "not connected" in result.error.lower()


class TestQQBotSend:
    @pytest.mark.asyncio
    async def test_send_truncates_long_messages(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        mock_api = MagicMock()
        mock_api.post_message = AsyncMock(return_value={"id": "msg_1"})
        adapter._bot_client = MagicMock()
        adapter._bot_client.api = mock_api

        long_msg = "x" * 3000
        result = await adapter.send("chan_123", long_msg)
        assert result.success is True
        call_args = mock_api.post_message.call_args
        sent_content = call_args[1]["content"]
        assert len(sent_content) <= 2000
        assert sent_content.endswith("...")

    @pytest.mark.asyncio
    async def test_send_to_dm(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter._dm_guild_ids.add("dm_guild_123")
        mock_api = MagicMock()
        mock_api.post_dms = AsyncMock(return_value={"id": "dm_1"})
        adapter._bot_client = MagicMock()
        adapter._bot_client.api = mock_api

        result = await adapter.send("dm_guild_123", "hello dm")
        assert result.success is True
        mock_api.post_dms.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_to_channel(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        mock_api = MagicMock()
        mock_api.post_message = AsyncMock(return_value={"id": "ch_1"})
        adapter._bot_client = MagicMock()
        adapter._bot_client.api = mock_api

        result = await adapter.send("channel_456", "hello channel")
        assert result.success is True
        mock_api.post_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_via_cached_responder(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        mock_responder = AsyncMock()
        adapter._responder_cache["grp_123"] = ("group_at", mock_responder, time.time())
        adapter._bot_client = MagicMock()
        adapter._bot_client.api = MagicMock()

        result = await adapter.send("grp_123", "hi group")
        assert result.success is True
        mock_responder.assert_called_once()
        kwargs = mock_responder.call_args[1]
        assert kwargs["content"] == "hi group"
        assert "msg_seq" in kwargs

    @pytest.mark.asyncio
    async def test_send_image_not_connected(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter._bot_client = None
        result = await adapter.send_image("chan", "http://img.png")
        assert result.success is False


# ---------------------------------------------------------------------------
# Integration checks (toolset, prompt hints, cron, etc.)
# ---------------------------------------------------------------------------

class TestQQBotIntegration:
    def test_toolset_exists(self):
        from toolsets import TOOLSETS
        assert "hermes-qqbot" in TOOLSETS

    def test_toolset_in_gateway(self):
        from toolsets import TOOLSETS
        assert "hermes-qqbot" in TOOLSETS["hermes-gateway"]["includes"]

    def test_platform_hint_exists(self):
        from agent.prompt_builder import PLATFORM_HINTS
        assert "qqbot" in PLATFORM_HINTS
        assert "QQ" in PLATFORM_HINTS["qqbot"]

    def test_cron_known_delivery_platforms(self):
        from cron.scheduler import _KNOWN_DELIVERY_PLATFORMS
        assert "qqbot" in _KNOWN_DELIVERY_PLATFORMS

    def test_send_message_platform_map(self):
        # Verify qqbot is in the send_message platform_map
        import tools.send_message_tool as smt
        # The platform_map is built inside _handle_send; verify via import check
        assert hasattr(smt, "_send_qqbot")

    def test_platform_info_registered(self):
        from hermes_cli.platforms import PLATFORMS
        assert "qqbot" in PLATFORMS
        assert PLATFORMS["qqbot"].default_toolset == "hermes-qqbot"

    def test_status_check_registered(self):
        # hermes_cli/status.py has QQBot in its platform detection dict
        import hermes_cli.status as status_mod
        source = open(status_mod.__file__).read()
        assert "QQBot" in source
        assert "QQBOT_APPID" in source

    def test_dump_platform_detection(self):
        import hermes_cli.dump as dump_mod
        source = open(dump_mod.__file__).read()
        assert '"qqbot"' in source
        assert "QQBOT_APPID" in source


class TestQQBotCheckRequirements:
    def test_check_requirements_ok(self, monkeypatch):
        monkeypatch.setenv("QQBOT_APPID", "12345")
        monkeypatch.setenv("QQBOT_SECRET", "secret")
        import gateway.platforms.qqbot as qqmod
        monkeypatch.setattr(qqmod, "QQBOTPY_AVAILABLE", True)
        assert qqmod.check_qqbot_requirements() is True

    def test_check_requirements_no_sdk(self, monkeypatch):
        monkeypatch.setenv("QQBOT_APPID", "12345")
        monkeypatch.setenv("QQBOT_SECRET", "secret")
        import gateway.platforms.qqbot as qqmod
        monkeypatch.setattr(qqmod, "QQBOTPY_AVAILABLE", False)
        assert qqmod.check_qqbot_requirements() is False

    def test_check_requirements_no_env(self, monkeypatch):
        monkeypatch.delenv("QQBOT_APPID", raising=False)
        monkeypatch.delenv("QQBOT_SECRET", raising=False)
        import gateway.platforms.qqbot as qqmod
        monkeypatch.setattr(qqmod, "QQBOTPY_AVAILABLE", True)
        assert qqmod.check_qqbot_requirements() is False
