from datetime import datetime, timedelta
from models import Task


def auto_archive_completed_tasks(db):
    """
    Move tasks that have been 'done' for more than 3 days to 'archived'.
    Called lazily on task list requests.
    """
    cutoff = datetime.utcnow() - timedelta(days=3)

    tasks_to_archive = db.query(Task).filter(
        Task.status == "done",
        Task.completed_at != None,
        Task.completed_at <= cutoff
    ).all()

    for task in tasks_to_archive:
        task.status = "archived"
        task.archived_at = datetime.utcnow()
        task.task_number = None

    if tasks_to_archive:
        db.commit()

    return len(tasks_to_archive)
