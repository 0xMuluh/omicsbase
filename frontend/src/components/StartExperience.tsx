"use client";

import { motion } from "framer-motion";
import Link from "next/link";

import {
  ProjectsSidebar,
  ProjectsSidebarToggle,
  useProjectsSidebar,
} from "@/components/ProjectsSidebar";
import { StartComposer } from "@/components/StartComposer";
import { ThemeToggle } from "@/components/ThemeToggle";

export function StartExperience() {
  const sidebar = useProjectsSidebar();

  return (
    <div className="relative min-h-dvh overflow-hidden bg-[var(--start-hero-base)]">
      <section className="start-hero relative isolate min-h-dvh overflow-hidden">
        <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
          <div className="absolute inset-0 bg-[var(--start-hero-base)]" />
          <div className="start-hero-aurora absolute inset-[-20%]" />
          <div className="start-hero-ribbon start-hero-ribbon-a absolute -left-1/4 top-[8%] h-[55%] w-[90%]" />
          <div className="start-hero-ribbon start-hero-ribbon-b absolute -right-1/5 top-[18%] h-[50%] w-[80%]" />
          <div className="start-hero-vignette absolute inset-0" />
          <div className="start-hero-grain absolute inset-0" />
        </div>

        <div className="absolute inset-x-0 top-0 z-20 flex h-14 items-center justify-between px-4 sm:px-6">
          <div className="flex items-center gap-2.5">
            <ProjectsSidebarToggle open={sidebar.open} onToggle={sidebar.toggle} />
            <Link
              href="/"
              className="font-display text-[1.25rem] font-medium tracking-[-0.02em] text-foreground transition hover:opacity-80"
            >
              OmicsBase
            </Link>
          </div>
          <ThemeToggle />
        </div>

        <div className="relative mx-auto flex min-h-dvh w-full max-w-3xl flex-col items-center justify-center px-6 py-20">
          <motion.div
            className="mb-8 w-full text-center sm:mb-9"
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.65, ease: [0.22, 1, 0.36, 1] }}
          >
            <h1 className="font-display text-[clamp(2.35rem,5.5vw,3.6rem)] font-medium leading-[1.08] tracking-[-0.03em] text-foreground">
              See beyond the counts.
            </h1>
            <p className="mx-auto mt-4 max-w-lg text-[17px] leading-7 text-muted-foreground">
              Create downstream omics reports by chatting with AI.
            </p>
          </motion.div>

          <motion.div
            className="w-full"
            initial={{ opacity: 0, y: 22 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.1, ease: [0.22, 1, 0.36, 1] }}
          >
            <StartComposer variant="hero" />
          </motion.div>
        </div>
      </section>

      <ProjectsSidebar open={sidebar.open} onClose={() => sidebar.setOpen(false)} notesScope="standalone" />
    </div>
  );
}
