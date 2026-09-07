from openhands.storage.conversation.file_conversation_store import FileConversationStore
from openhands.storage.settings.file_settings_store import FileSettingsStore
from openhands.storage.secrets.file_secrets_store import FileSecretsStore
from omicsbase_shared.identity import identifier


class UserConversations(FileConversationStore):
    @classmethod
    async def get_instance(cls, config, user_id):
        base = await super().get_instance(config, user_id)
        store = cls(base.file_store)
        store.user_id = identifier(user_id) if user_id else None
        return store

    def get_conversation_metadata_dir(self):
        if self.user_id:
            return f'users/{self.user_id}/conversations'
        return 'conversations'

    def get_conversation_metadata_filename(self, conversation_id):
        return f'{self.get_conversation_metadata_dir()}/{identifier(conversation_id)}/metadata.json'


class UserSettings(FileSettingsStore):
    @classmethod
    async def get_instance(cls, config, user_id):
        base = await super().get_instance(config, user_id)
        if not user_id:
            return base
        return cls(base.file_store, f'users/{identifier(user_id)}/settings.json')

    async def load(self):
        settings = await super().load()
        if settings is None:
            # Seed server-managed LLM defaults once; future changes remain user-scoped.
            settings = await FileSettingsStore(self.file_store).load()
            if settings is not None:
                await self.store(settings)
        return settings


class UserSecrets(FileSecretsStore):
    @classmethod
    async def get_instance(cls, config, user_id):
        base = await super().get_instance(config, user_id)
        if not user_id:
            return base
        return cls(base.file_store, f'users/{identifier(user_id)}/secrets.json')
