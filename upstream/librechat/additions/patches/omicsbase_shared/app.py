"""Compose OmicsBase routes before OpenHands mounts its frontend catch-all."""
import os

os.environ.setdefault(
    'OPENHANDS_CONVERSATION_VALIDATOR_CLS',
    'omicsbase_shared.validator.OmicsBaseConversationValidator',
)

from openhands.server.app import app as base_app
from omicsbase_shared import assets, editor, launch, reports
from omicsbase_shared.frontend import install_frontend

for router in (launch.router, editor.router, reports.router, assets.router):
    base_app.include_router(router)
install_frontend()

from openhands.server.listen import app
from openhands.server.shared import sio
from omicsbase_shared.sockets import install_socket_sessions
install_socket_sessions(sio)
