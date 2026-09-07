"""OpenCode adapter for the backend-neutral coding-agent contract."""

from __future__ import annotations

from collections.abc import AsyncIterator

from .contract import AgentEvent, AgentTurnRequest


class OpenCodeBackend:
    """Adapt the existing OpenCode relay without changing its event contract."""

    name = "opencode"

    async def _stream(self, request: AgentTurnRequest) -> AsyncIterator[AgentEvent]:
        # Import lazily so tests and alternative deployments can replace the relay.
        from app.services.opencode.transport import stream_opencode

        session_id = request.session.external_id if request.session else None
        async for event in stream_opencode(
            project_dir=request.project_dir,
            instruction=request.instruction,
            provider=request.provider,
            model=request.model,
            session_id=session_id,
            fresh_session=request.fresh_session,
            chat_mode=request.chat_mode,
            cancel_check=request.cancel_check,
            question=request.question,
            plan=request.plan,
            notes=request.notes,
            existing_sources=request.existing_sources,
            attachments=request.attachments,
            selected_file=request.selected_file,
            selected_content=request.selected_content,
            selected_content_dirty=request.selected_content_dirty,
            preview_path=request.preview_path,
            file_attachments=request.file_attachments,
            api_key=request.options.get("api_key"),
        ):
            yield event

    def stream_turn(self, request: AgentTurnRequest) -> AsyncIterator[AgentEvent]:
        return self._stream(request)

