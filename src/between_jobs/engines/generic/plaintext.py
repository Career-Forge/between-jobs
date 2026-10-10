"""The profile as plain text, for the things that read a resume as text (interview practice).

A deterministic serializer of the validated profile: sections in a fixed order, entries in the
candidate's own order, every line the candidate's own words. No model, no judgement about what
matters. Contact details, date of birth, nationality, marital status, a photo and the
work-authorization text are left out: the text goes to an AI provider, and practice questions
are about the work, not the person's details.
"""

from __future__ import annotations

from between_jobs.api.profile import ResumeTemplate

from .formatting import date_range
from .text import clean_line


def _list(title: str, items: list[str]) -> list[str]:
    cleaned = [clean_line(item) for item in items if clean_line(item)]
    return [title, *(f"- {item}" for item in cleaned), ""] if cleaned else []


def resume_text(template: ResumeTemplate) -> str:
    lines: list[str] = []
    headline = clean_line(template.personal.headline)
    if headline:
        lines += [headline, ""]
    lines += _list("SUMMARY", template.summary_bullets)

    if template.experience:
        lines.append("EXPERIENCE")
        for job in template.experience:
            dates = date_range(job.start_date, job.end_date, current=job.is_current)
            lines.append(clean_line(f"{job.title}, {job.company} ({dates})"))
            lines += [f"- {clean_line(b)}" for b in job.bullets if clean_line(b)]
            if job.skills:
                lines.append("  Skills: " + ", ".join(clean_line(s) for s in job.skills))
            if job.metrics:
                lines.append("  Metrics: " + "; ".join(clean_line(m) for m in job.metrics))
        lines.append("")

    if template.projects:
        lines.append("PROJECTS")
        for project in template.projects:
            lines.append(clean_line(project.name))
            lines += [f"- {clean_line(b)}" for b in project.bullets if clean_line(b)]
            if project.tech:
                lines.append("  Technologies: " + ", ".join(clean_line(t) for t in project.tech))
        lines.append("")

    if template.education:
        lines.append("EDUCATION")
        for edu in template.education:
            degree = f"{edu.degree}, {edu.field}" if edu.field else edu.degree
            lines.append(clean_line(f"{degree}, {edu.institution}"))
        lines.append("")

    skills = template.skills
    groups = (
        ("Programming", skills.programming),
        ("AI and ML", skills.ai_ml),
        ("Data and MLOps", skills.data_mlops),
        ("Cloud and DevOps", skills.cloud_devops),
        ("Tools", skills.tools),
        ("Other", skills.other),
    )
    if any(items for _name, items in groups):
        lines.append("SKILLS")
        lines += [
            f"{name}: " + ", ".join(clean_line(item) for item in items)
            for name, items in groups
            if items
        ]
        lines.append("")

    lines += _list("CERTIFICATIONS", [c.name for c in template.certifications])
    lines += _list("PUBLICATIONS", [p.title for p in template.publications])
    lines += _list("PATENTS", [p.title for p in template.patents])
    lines += _list("ACHIEVEMENTS", template.achievements)
    for volunteer in template.volunteering:
        lines += _list(
            clean_line(f"VOLUNTEERING: {volunteer.role} {volunteer.organization}"),
            volunteer.bullets,
        )
    return "\n".join(lines).strip()
