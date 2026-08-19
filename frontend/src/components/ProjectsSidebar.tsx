"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import {
  Archive,
  BookOpen,
  ChevronRight,
  FlaskConical,
  Loader2,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Pin,
  Plus,
  Settings2,
  Trash2,
} from "lucide-react";

import { api, NoteThreadSummary, Project } from "@/lib/api";
import { useReuseCache } from "@/lib/use-note-settings";

const STORAGE_KEY = "omicsbase.projects-sidebar.open";

function projectHref(project: Project): string {
  return `/projects/${project.id}/workspace`;
}

export function useProjectsSidebar() {
  const [open, setOpen] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      // eslint-disable-next-line react-hooks/set-state-in-effect -- hydrate sidebar preference from local storage
      setOpen(stored === null ? false : stored === "1");
    } catch {
      setOpen(false);
    }
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    try {
      window.localStorage.setItem(STORAGE_KEY, open ? "1" : "0");
    } catch {
      // ignore storage failures
    }
  }, [open, ready]);

  return {
    open,
    ready,
    setOpen,
    toggle: () => setOpen((value) => !value),
  };
}

export function ProjectsSidebarToggle({
  open,
  onToggle,
}: {
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-border/80 bg-background/60 text-muted-foreground transition hover:bg-muted hover:text-foreground"
      title={open ? "Hide recent projects" : "Show recent projects"}
      aria-label={open ? "Hide recent projects" : "Show recent projects"}
      aria-pressed={open}
    >
      {open ? <PanelLeftClose className="h-4 w-4" /> : <PanelLeftOpen className="h-4 w-4" />}
    </button>
  );
}

export function ProjectsSidebarContent({
  onClose,
  notesScope,
  activeThreadId,
}: {
  onClose?: () => void;
  notesScope?: string | "recent";
  activeThreadId?: string | null;
}) {
  const { data: projects, isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: api.listProjects,
  });

  const scope = notesScope === "recent" ? "standalone" : notesScope || "standalone";
  const queryClient = useQueryClient();
  const router = useRouter();
  const [reuseCache, setReuseCache] = useReuseCache();
  const [menuThreadId, setMenuThreadId] = useState<string | null>(null);
  const [settingsThreadId, setSettingsThreadId] = useState<string | null>(null);
  const notesQuery = useQuery({
    queryKey: ["note-threads", scope],
    queryFn: () =>
      scope === "standalone"
        ? api.listStandaloneNoteThreads()
        : api.listNoteThreads(scope),
    enabled: Boolean(scope),
  });
  const notes = (notesQuery.data || [])
    .filter((note) => note.status === "active")
    .sort((a, b) => Number(Boolean(b.metadata?.pinned)) - Number(Boolean(a.metadata?.pinned)));
  const activeProjects = (projects || []).filter((project) => project.status !== "archived");
  const noteHref = (note: NoteThreadSummary) =>
    note.project_id
      ? `/projects/${note.project_id}/notes?thread=${note.id}`
      : `/notes?thread=${note.id}`;

  const updateNote = (note: NoteThreadSummary, data: { title?: string; status?: "active" | "archived"; metadata?: Record<string, unknown> | null }) =>
    scope === "standalone"
      ? api.updateStandaloneNoteThread(note.id, data)
      : api.updateNoteThread(scope, note.id, data);

  const deleteNote = (note: NoteThreadSummary) =>
    scope === "standalone"
      ? api.deleteStandaloneNoteThread(note.id)
      : api.deleteNoteThread(scope, note.id);

  const togglePin = async (note: NoteThreadSummary) => {
    const pinned = Boolean(note.metadata?.pinned);
    try {
      await updateNote(note, { metadata: { ...(note.metadata || {}), pinned: !pinned } });
      queryClient.invalidateQueries({ queryKey: ["note-threads", scope] });
    } catch {
      // ignore transient failures
    }
  };

  const renameNote = async (note: NoteThreadSummary) => {
    const title = window.prompt("Rename note", note.title || "");
    if (title == null) return;
    const clean = title.trim();
    if (!clean) return;
    try {
      await updateNote(note, { title: clean });
      queryClient.invalidateQueries({ queryKey: ["note-threads", scope] });
    } catch {
      // ignore transient failures
    }
  };

  const archiveNote = async (note: NoteThreadSummary) => {
    try {
      await updateNote(note, { status: "archived" });
      queryClient.invalidateQueries({ queryKey: ["note-threads", scope] });
      if (activeThreadId === note.id) router.push(scope === "standalone" ? "/notes" : `/projects/${scope}/notes`);
    } catch {
      // ignore transient failures
    }
  };

  const deleteNotePermanently = async (note: NoteThreadSummary) => {
    if (!window.confirm(`Delete "${note.title || "Untitled note"}" permanently? This removes the note and all its files.`)) return;
    try {
      await deleteNote(note);
      queryClient.invalidateQueries({ queryKey: ["note-threads", scope] });
      if (activeThreadId === note.id) router.push(scope === "standalone" ? "/notes" : `/projects/${scope}/notes`);
    } catch {
      // ignore transient failures
    }
  };

  const createNewNote = async () => {
    try {
      const thread = await api.createStandaloneNoteThread({ title: "Untitled note" });
      queryClient.invalidateQueries({ queryKey: ["note-threads", "standalone"] });
      router.push(`/notes?thread=${thread.id}`);
    } catch {
      // ignore transient failures
    }
  };

  return (
    <div className="flex h-full w-full flex-col border-r border-border bg-sidebar text-sidebar-foreground">
      {/* Sidebar Header with OmicsBase Logo */}
      <div className="flex h-14 items-center justify-between gap-3 border-b border-border/40 px-4">
        <Link
          href="/"
          className="flex items-center gap-2 text-sm font-semibold hover:opacity-80 transition"
        >
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-teal-600 text-white shadow-sm">
            <FlaskConical className="h-4 w-4" />
          </div>
          <span className="font-display text-base font-semibold tracking-tight text-foreground">
            OmicsBase
          </span>
        </Link>

        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition hover:bg-muted hover:text-foreground"
            title="Close sidebar"
          >
            <PanelLeftClose className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* New Project Action Buttons */}
      <div className="border-b border-border/40 px-3 py-3">
        <Link
          href="/"
          className="inline-flex h-9 w-full items-center justify-center gap-2 rounded-lg border border-teal-600/20 bg-teal-600/10 text-sm font-medium text-teal-800 transition hover:bg-teal-600/15 dark:border-teal-400/25 dark:bg-teal-400/10 dark:text-teal-100 dark:hover:bg-teal-400/15"
        >
          <Plus className="h-4 w-4" />
          New project
        </Link>
        <button
          type="button"
          onClick={() => void createNewNote()}
          className="mt-2 inline-flex h-9 w-full items-center justify-center gap-2 rounded-lg border border-border bg-muted/40 text-sm font-medium text-foreground transition hover:bg-muted"
        >
          <Plus className="h-4 w-4" />
          New note
        </button>
      </div>

      {/* Notes + Recent Projects List (two independent scroll areas) */}
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto border-b border-border/40 px-2 py-3">
        <div className="px-3 pb-2 text-xs font-medium text-muted-foreground">Notes</div>
        {notesQuery.isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : notes.length ? (
          <ul className="space-y-1">
            {notes.map((note) => {
              const isActive = activeThreadId === note.id;
              const pinned = Boolean(note.metadata?.pinned);
              return (
                <li key={note.id} className="group relative">
                  <div
                    className={`flex items-center rounded-xl pr-1 transition ${
                      isActive ? "bg-teal-500/15" : "hover:bg-muted"
                    }`}
                  >
                    <Link
                      href={noteHref(note)}
                      className={`flex min-w-0 flex-1 items-center gap-2 px-3 py-2 text-sm transition ${
                        isActive
                          ? "font-medium text-teal-800 dark:text-teal-200"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                      title={note.title}
                    >
                      <BookOpen className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70" />
                      <span className="truncate">{note.title || "Untitled note"}</span>
                    </Link>

                    <div
                      className={`flex shrink-0 items-center gap-0.5 transition-opacity ${
                        pinned ? "opacity-100" : "opacity-0 group-hover:opacity-100"
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() => void togglePin(note)}
                        className="rounded-md p-1.5 text-muted-foreground transition hover:bg-background hover:text-foreground"
                        title={pinned ? "Unpin note" : "Pin note"}
                        aria-label={pinned ? "Unpin note" : "Pin note"}
                      >
                        <Pin className={`h-3.5 w-3.5 ${pinned ? "text-teal-600 dark:text-teal-300" : ""}`} fill={pinned ? "currentColor" : "none"} />
                      </button>

                      <div className="relative">
                        <button
                          type="button"
                          onClick={() => {
                            setSettingsThreadId(settingsThreadId === note.id ? null : note.id);
                            setMenuThreadId(null);
                          }}
                          className="rounded-md p-1.5 text-muted-foreground transition hover:bg-background hover:text-foreground"
                          title="Run settings"
                          aria-label="Run settings"
                        >
                          <Settings2 className="h-3.5 w-3.5" />
                        </button>
                        {settingsThreadId === note.id ? (
                          <>
                            <div className="fixed inset-0 z-20" onClick={() => setSettingsThreadId(null)} />
                            <div className="absolute right-0 top-full z-30 mt-1 w-60 rounded-xl border border-border bg-popover p-2.5 text-xs text-popover-foreground shadow-xl">
                              <label className="flex cursor-pointer items-start gap-2">
                                <input
                                  type="checkbox"
                                  checked={reuseCache}
                                  onChange={(event) => setReuseCache(event.target.checked)}
                                  className="mt-0.5 accent-teal-500"
                                />
                                <span>
                                  <span className="block font-medium">Reuse validated results</span>
                                  <span className="mt-0.5 block leading-4 text-muted-foreground">
                                    Cells share one notebook R workspace; cached runs are only reused for identical code.
                                  </span>
                                </span>
                              </label>
                            </div>
                          </>
                        ) : null}
                      </div>

                      <div className="relative">
                        <button
                          type="button"
                          onClick={() => {
                            setMenuThreadId(menuThreadId === note.id ? null : note.id);
                            setSettingsThreadId(null);
                          }}
                          className="rounded-md p-1.5 text-muted-foreground transition hover:bg-background hover:text-foreground"
                          title="Note options"
                          aria-label="Note options"
                        >
                          <MoreHorizontal className="h-3.5 w-3.5" />
                        </button>
                        {menuThreadId === note.id ? (
                          <>
                            <div className="fixed inset-0 z-20" onClick={() => setMenuThreadId(null)} />
                            <div className="absolute right-0 top-full z-30 mt-1 w-40 rounded-xl border border-border bg-popover p-1 text-xs shadow-xl">
                  <button
                    type="button"
                    onClick={() => {
                      setMenuThreadId(null);
                      void renameNote(note);
                    }}
                    className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground transition hover:bg-muted"
                  >
                    <Pencil className="h-3.5 w-3.5" /> Rename
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setMenuThreadId(null);
                      void archiveNote(note);
                    }}
                    className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground transition hover:bg-muted"
                  >
                    <Archive className="h-3.5 w-3.5" /> Archive
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setMenuThreadId(null);
                      void deleteNotePermanently(note);
                    }}
                    className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-red-600 transition hover:bg-red-500/10 dark:text-red-400"
                  >
                    <Trash2 className="h-3.5 w-3.5" /> Delete
                  </button>
                            </div>
                          </>
                        ) : null}
                      </div>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <div className="px-3 py-6 text-center">
            <BookOpen className="mx-auto mb-2 h-7 w-7 text-muted-foreground/50" />
            <p className="text-xs leading-5 text-muted-foreground">
              No notes yet.
            </p>
          </div>
        )}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-2 py-3">
        <div className="px-3 pb-2 pt-4 text-xs font-medium text-muted-foreground">
          Recent projects
        </div>
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : !activeProjects.length ? (
          <div className="px-3 py-10 text-center">
            <FlaskConical className="mx-auto mb-3 h-8 w-8 text-muted-foreground/50" />
            <p className="text-sm text-foreground">No projects yet</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              Describe a study in the composer to create one.
            </p>
          </div>
        ) : (
          <ul className="space-y-1">
            {activeProjects.map((project) => (
              <SidebarProjectItem key={project.id} project={project} />
            ))}
          </ul>
        )}
        </div>
      </div>
    </div>
  );
}

export function ProjectsSidebar({
  open,
  onClose,
  notesScope,
}: {
  open: boolean;
  onClose: () => void;
  notesScope?: string | "recent";
}) {
  return (
    <>
      <AnimatePresence>
        {open ? (
          <motion.button
            type="button"
            aria-label="Close projects sidebar"
            className="fixed inset-0 z-40 bg-black/30 backdrop-blur-[2px] md:hidden dark:bg-black/45"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />
        ) : null}
      </AnimatePresence>
      <motion.aside
        initial={false}
        animate={{
          width: open ? 320 : 0,
          opacity: open ? 1 : 0,
          x: open ? 0 : -24,
          borderRightWidth: open ? "1px" : "0px",
        }}
        transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
        className={`fixed inset-y-0 left-0 z-50 flex h-full flex-col overflow-hidden bg-sidebar shadow-[20px_0_60px_rgba(15,23,42,0.12)] backdrop-blur-xl dark:shadow-[20px_0_60px_rgba(0,0,0,0.35)] md:static md:z-auto md:h-screen md:shrink-0 md:shadow-none md:backdrop-blur-none ${
          open ? "" : "pointer-events-none"
        }`}
        inert={!open}
        aria-hidden={!open}
      >
        <ProjectsSidebarContent onClose={onClose} notesScope={notesScope} />
      </motion.aside>
    </>
  );
}

function SidebarProjectItem({ project }: { project: Project }) {
  const params = useParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const activeProjectId = params?.id;
  const isActive = activeProjectId === project.id;
  const href = projectHref(project);
  const [menuOpen, setMenuOpen] = useState(false);
  const [notesOpen, setNotesOpen] = useState(false);
  const [projectThreads, setProjectThreads] = useState<NoteThreadSummary[] | null>(null);
  const notesCloseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const openProjectNotes = async () => {
    if (notesCloseTimerRef.current) clearTimeout(notesCloseTimerRef.current);
    setNotesOpen(true);
    if (projectThreads === null) {
      try {
        const threads = await api.listNoteThreads(project.id);
        setProjectThreads(threads.filter((thread) => thread.status === "active"));
      } catch {
        setProjectThreads([]);
      }
    }
  };

  const scheduleCloseNotes = () => {
    if (notesCloseTimerRef.current) clearTimeout(notesCloseTimerRef.current);
    notesCloseTimerRef.current = setTimeout(() => setNotesOpen(false), 180);
  };

  const renameProject = async () => {
    const name = window.prompt("Rename project", project.name || "");
    if (name == null) return;
    const clean = name.trim();
    if (!clean) return;
    try {
      const updatedProject = await api.updateProject(project.id, { name: clean });
      queryClient.setQueryData<Project>(["project", project.id], updatedProject);
      queryClient.setQueryData<Project[]>(["projects"], (projects) =>
        projects?.map((item) => (item.id === project.id ? updatedProject : item))
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["project", project.id] }),
        queryClient.invalidateQueries({ queryKey: ["projects"] }),
      ]);
    } catch {
      // ignore transient failures
    }
  };

  const addNote = async () => {
    try {
      const thread = await api.createNoteThread(project.id, { title: "Untitled note" });
      queryClient.invalidateQueries({ queryKey: ["note-threads", project.id] });
      router.push(`/projects/${project.id}/notes?thread=${thread.id}`);
    } catch {
      // ignore transient failures
    }
  };

  const archiveProject = async () => {
    try {
      await api.updateProject(project.id, { status: "archived" });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      if (isActive) router.push("/");
    } catch {
      // ignore transient failures
    }
  };

  const deleteProject = async () => {
    if (!window.confirm(`Delete "${project.name || "Untitled Chat"}"? This permanently removes the project and its files.`)) return;
    try {
      await api.deleteProject(project.id);
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      if (isActive) router.push("/");
    } catch {
      // ignore transient failures
    }
  };

  return (
    <li className="group relative">
      <div
        className={`flex items-center rounded-xl pr-1 transition ${
          isActive ? "bg-teal-500/15" : "hover:bg-muted"
        }`}
      >
        <Link
          href={href}
          className={`flex min-w-0 flex-1 items-center rounded-xl px-3 py-2 text-sm transition ${
            isActive
              ? "font-medium text-teal-800 dark:text-teal-200"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          <span className="truncate">{project.name || "Untitled Chat"}</span>
        </Link>

        <div className="flex shrink-0 items-center opacity-0 transition-opacity group-hover:opacity-100">
          <div className="relative">
            <button
              type="button"
              onClick={() => setMenuOpen((value) => !value)}
              className="rounded-md p-1.5 text-muted-foreground transition hover:bg-background hover:text-foreground"
              title="Project options"
              aria-label="Project options"
            >
              <MoreHorizontal className="h-3.5 w-3.5" />
            </button>
            {menuOpen ? (
              <>
                <div className="fixed inset-0 z-20" onClick={() => setMenuOpen(false)} />
                <div className="absolute right-0 top-full z-30 mt-1 w-40 rounded-xl border border-border bg-popover p-1 text-xs shadow-xl">
                  <button
                    type="button"
                    onClick={() => {
                      void openProjectNotes();
                    }}
                    onMouseEnter={() => {
                      // Only expand an already-open flyout; never open on a
                      // passing pointer aiming for the rows below.
                      if (notesOpen) void openProjectNotes();
                    }}
                    className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground transition hover:bg-muted"
                  >
                    <BookOpen className="h-3.5 w-3.5" /> Notes
                    <ChevronRight className="ml-auto h-3.5 w-3.5 text-muted-foreground" />
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setMenuOpen(false);
                      void addNote();
                    }}
                    className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground transition hover:bg-muted"
                  >
                    <BookOpen className="h-3.5 w-3.5" /> Add note
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setMenuOpen(false);
                      void renameProject();
                    }}
                    className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground transition hover:bg-muted"
                  >
                    <Pencil className="h-3.5 w-3.5" /> Rename
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setMenuOpen(false);
                      void archiveProject();
                    }}
                    className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground transition hover:bg-muted"
                  >
                    <Archive className="h-3.5 w-3.5" /> Archive
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setMenuOpen(false);
                      void deleteProject();
                    }}
                    className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-red-600 transition hover:bg-red-500/10 dark:text-red-400"
                  >
                    <Trash2 className="h-3.5 w-3.5" /> Delete
                  </button>
                </div>
              </>
            ) : null}
          </div>
        </div>

        {notesOpen ? (
          <div
            onMouseEnter={openProjectNotes}
            onMouseLeave={scheduleCloseNotes}
            className="absolute right-full top-0 z-40 mr-1 w-64 max-h-80 overflow-y-auto rounded-2xl border border-border bg-popover p-1.5 text-xs text-popover-foreground shadow-2xl"
          >
            <p className="px-2.5 py-1.5 font-medium text-muted-foreground">Notes</p>
            {projectThreads === null ? (
              <p className="flex items-center gap-2 px-2.5 py-2 text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" /> Loading...
              </p>
            ) : projectThreads.length === 0 ? (
              <p className="px-2.5 py-2 text-muted-foreground">No notes in this project yet.</p>
            ) : (
              projectThreads.map((thread) => (
                <button
                  key={thread.id}
                  type="button"
                  onClick={() => router.push(`/projects/${project.id}/notes?thread=${thread.id}`)}
                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground transition hover:bg-muted"
                >
                  <BookOpen className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70" />
                  <span className="truncate">{thread.title || "Untitled note"}</span>
                </button>
              ))
            )}
          </div>
        ) : null}
      </div>
    </li>
  );
}
