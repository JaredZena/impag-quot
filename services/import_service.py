import re
import json
import anthropic
from config import claude_api_key
from models import Task, get_next_task_number
from datetime import datetime, date as date_type


DATE_PATTERN = re.compile(
    r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})$"
    r"|^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})$"
)


def _parse_date(s: str) -> date_type | None:
    s = s.strip()
    m = DATE_PATTERN.match(s)
    if not m:
        return None
    try:
        if m.group(1):
            return date_type(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        else:
            return date_type(int(m.group(4)), int(m.group(5)), int(m.group(6)))
    except ValueError:
        return None


def parse_import_text(text: str) -> list[dict]:
    lines = text.strip().split("\n")
    parsed = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        tab_parts = line.split("\t")

        due_date = None
        if len(tab_parts) >= 2:
            maybe_date = _parse_date(tab_parts[-1])
            if maybe_date:
                due_date = maybe_date
                tab_parts = tab_parts[:-1]

        rejoined = "\t".join(tab_parts)
        parts = re.split(r"\t|\s{2,}", rejoined, maxsplit=1)
        if len(parts) < 2:
            match = re.match(r"^(\d+)\s+(.+)$", rejoined)
            if match:
                parts = [match.group(1), match.group(2)]
            else:
                parts = [None, rejoined]

        raw_number = parts[0]
        title_part = parts[1] if len(parts) > 1 else parts[0]

        task_number = None
        if raw_number and raw_number.strip().isdigit():
            task_number = int(raw_number.strip())

        priority = "medium"
        urgente_match = re.search(r"\(URGENTE\)", title_part, re.IGNORECASE)
        if urgente_match:
            priority = "urgent"
            title_part = title_part[:urgente_match.start()].strip()

        title_part = title_part.strip()
        if not title_part:
            continue

        parsed.append({
            "task_number": task_number,
            "title": title_part,
            "priority": priority,
            "due_date": due_date,
        })

    return parsed


def detect_duplicates_with_ai(incoming_tasks: list[dict], existing_tasks: list[dict]) -> list[dict]:
    if not claude_api_key:
        return [
            {**t, "is_duplicate": False, "matched_existing_id": None, "match_reason": None}
            for t in incoming_tasks
        ]

    if not existing_tasks:
        return [
            {**t, "is_duplicate": False, "matched_existing_id": None, "match_reason": None}
            for t in incoming_tasks
        ]

    existing_list = "\n".join(
        f"  ID={t['id']}, #{t['task_number'] or '?'}: {t['title']}"
        for t in existing_tasks
    )

    incoming_list = "\n".join(
        f"  INDEX={i}, #{t['task_number'] or '?'}: {t['title']}"
        for i, t in enumerate(incoming_tasks)
    )

    prompt = f"""You are a task deduplication assistant for a business task management system.

EXISTING TASKS (already in the system):
{existing_list}

INCOMING TASKS (being imported):
{incoming_list}

For each INCOMING task, determine if it is a duplicate of an existing task. A task is a duplicate if:
- It describes the same work/action, even if worded slightly differently
- It refers to the same subject (e.g. same client, same product, same shipment)
- Minor differences in wording, capitalization, or extra details do NOT make it different

Be strict: if the core action and subject are the same, it's a duplicate.
If the tasks are about different subjects or different actions, they are NOT duplicates.

Return a JSON array with one object per incoming task (in the same order), each with:
- "index": the INDEX number of the incoming task
- "is_duplicate": true/false
- "matched_existing_id": the ID of the matching existing task if duplicate, null otherwise
- "reason": short explanation (in Spanish) of why it's a duplicate or why it's new

Return ONLY the JSON array, no other text."""

    client = anthropic.Anthropic(api_key=claude_api_key)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = response.content[0].text.strip()
    if response_text.startswith("```"):
        response_text = re.sub(r"^```(?:json)?\n?", "", response_text)
        response_text = re.sub(r"\n?```$", "", response_text)

    try:
        results = json.loads(response_text)
    except json.JSONDecodeError:
        return [
            {**t, "is_duplicate": False, "matched_existing_id": None, "match_reason": "Error al analizar duplicados"}
            for t in incoming_tasks
        ]

    enriched = []
    for i, task in enumerate(incoming_tasks):
        ai_result = next((r for r in results if r.get("index") == i), None)
        if ai_result:
            enriched.append({
                **task,
                "is_duplicate": ai_result.get("is_duplicate", False),
                "matched_existing_id": ai_result.get("matched_existing_id"),
                "match_reason": ai_result.get("reason"),
            })
        else:
            enriched.append({
                **task,
                "is_duplicate": False,
                "matched_existing_id": None,
                "match_reason": None,
            })

    return enriched


def create_imported_tasks(db, tasks_to_create: list[dict], assigned_to: int, created_by: int) -> list[Task]:
    incoming_numbers = {t["task_number"] for t in tasks_to_create if t.get("task_number")}

    if incoming_numbers:
        conflicting = db.query(Task).filter(
            Task.task_number.in_(incoming_numbers),
            Task.status != "archived",
        ).all()

        if conflicting:
            used = {r[0] for r in db.query(Task.task_number).filter(Task.task_number.isnot(None)).all()}
            reserved = used | incoming_numbers

            for task in conflicting:
                n = 1
                while n in reserved:
                    n += 1
                task.task_number = n
                reserved.add(n)

            db.flush()

    created = []
    for t in tasks_to_create:
        if t.get("task_number"):
            task_number = t["task_number"]
        else:
            task_number = get_next_task_number(db)

        due_date = t.get("due_date")
        task = Task(
            title=t["title"],
            priority=t.get("priority", "medium"),
            assigned_to=assigned_to,
            created_by=created_by,
            task_number=task_number,
            status="pending",
        )
        if due_date:
            task.due_date = due_date
        db.add(task)
        db.flush()
        created.append(task)

    return created
