"""Register all ORM model modules.

Importing any ``app.models`` submodule (e.g. ``from app.models.project
import Project``) executes this package initializer first, so every model
class is defined before SQLAlchemy configures mappers. This prevents
unresolvable string relationships such as ``Project.note_threads -> "NoteThread"``
when a model module is imported in isolation.
"""

from app.models import knowledge, notes, project, runs
