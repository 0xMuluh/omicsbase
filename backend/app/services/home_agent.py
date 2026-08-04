"""Project title generation (surviving piece of the retired home chat)."""

from __future__ import annotations

from app.services.llm import call_llm


async def generate_project_title(prompt: str) -> str:
    """Generate a clean 2-4 word scientific topic title for a project using few-shot LLM prompt."""
    system_prompt = (
        "You are a scientific topic title generator. Given a user query or research question, "
        "summarize the core scientific topic into a concise 2 to 4 word Title Case topic name.\n"
        "Examples:\n"
        "- 'can you tell me about the differences in kruskal and fisher exact' -> 'Kruskal vs Fisher Exact Test'\n"
        "- 'what downstream analysis can i do on RNA-seq' -> 'Downstream RNA-seq Workflow'\n"
        "- 'what is ecological alpha diversity' -> 'Alpha Diversity'\n"
        "- 'how to run permanova on phyloseq data' -> 'PERMANOVA Analysis'\n"
        "Return ONLY the 2-4 word title. Do not add quotes, explanation, or punctuation."
    )

    from app.services.llm import resolve_target

    title_provider, title_model = resolve_target("title")
    raw = await call_llm(
        system_prompt=system_prompt,
        user_prompt=f'Summarize this query into a title: "{prompt[:300]}"',
        max_tokens=20,
        model_override=title_model,
        provider_override=title_provider,
    )
    cleaned = raw.strip().strip('"').strip("'").strip(".").strip()
    return cleaned
