"""Offline startup contract for a coherent source integration, without starting an agent."""


def test_agent_tts_and_tui_share_current_import_graph():
    from run_agent import AIAgent
    from tools import tts_streaming, tts_tool
    from tools.tts_tool_plugins import _plugin_pcm_streaming_provider
    from tui_gateway import server

    assert callable(AIAgent)
    assert callable(server.handle_request)
    assert tts_streaming._plugin_pcm_streaming_provider is _plugin_pcm_streaming_provider
    assert callable(tts_tool.text_to_speech_tool)
