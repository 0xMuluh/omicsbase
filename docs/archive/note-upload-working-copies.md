# Note upload working copies

LibreChat retains the original upload. Before NoteCells executes R, attachments linked
to the conversation or its messages are copied to `projects/<conversationId>/data/<filename>`.
The lookup is restricted to the conversation owner. Existing working files are preserved
on subsequent executions. Use a different filename for a new dataset version: uploading
a different file with the same name does not replace the working copy.
Copy failures stop execution instead of producing missing-file R errors.

## Permissions and deployment

Run `scripts/prepare_runtime_dirs.sh` with `GID_VALUE` matching LibreChat's group
(normally 1000). It assigns the project group without changing project file owners,
grants group write, and sets setgid on directories. The engine uses umask 002 so new
files remain group writable. This is service-level sharing, not tenant filesystem isolation.
Restart the engine after deploying its startup change; existing R kernels retain their
old umask until restarted.

Deploy both the compiled API package and the updated mounted NoteCells service/routes
and engine source. Restarting an old image alone does not update compiled API code.
Restore from the committed snapshot before building, preserving VM-only edits first.

After deployment and permissions preparation, upload a uniquely named RDS in a new
conversation. Execute `readRDS("data/<filename>")` without manual copying. Confirm the
file appears in the engine's conversation directory. Repeat with an existing conversation
created by the engine. Rerunning cells should preserve edits to the working copy.
