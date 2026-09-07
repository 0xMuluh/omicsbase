from openhands.server.config.server_config import ServerConfig


class OmicsBaseConfig(ServerConfig):
    user_auth_class = 'omicsbase_shared.auth.OmicsBaseAuth'
    settings_store_class = 'omicsbase_shared.stores.UserSettings'
    secret_store_class = 'omicsbase_shared.stores.UserSecrets'
    conversation_store_class = 'omicsbase_shared.stores.UserConversations'

    def verify_config(self):
        pass
