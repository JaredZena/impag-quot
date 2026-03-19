from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from models import get_db, Notification
from auth import verify_google_token

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
    user=Depends(verify_google_token),
):
    """List notifications for the current engineer."""
    email = user.get("email", "")
    query = db.query(Notification).filter(Notification.recipient_email == email)

    if unread_only:
        query = query.filter(Notification.is_read == False)

    notifications = query.order_by(desc(Notification.created_at)).limit(limit).all()

    return {
        "success": True,
        "data": [
            {
                "id": n.id,
                "quote_id": n.quote_id,
                "event_type": n.event_type,
                "message": n.message,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notifications
        ],
    }


@router.get("/count")
def unread_count(db: Session = Depends(get_db), user=Depends(verify_google_token)):
    """Get unread notification count for badge display."""
    email = user.get("email", "")
    count = (
        db.query(Notification)
        .filter(Notification.recipient_email == email, Notification.is_read == False)
        .count()
    )
    return {"success": True, "data": {"unread_count": count}}


@router.post("/{notification_id}/read")
def mark_read(notification_id: int, db: Session = Depends(get_db), user=Depends(verify_google_token)):
    """Mark a notification as read."""
    n = db.query(Notification).filter(Notification.id == notification_id).first()
    if n:
        n.is_read = True
        db.commit()
    return {"success": True}


@router.post("/read-all")
def mark_all_read(db: Session = Depends(get_db), user=Depends(verify_google_token)):
    """Mark all notifications as read for current user."""
    email = user.get("email", "")
    db.query(Notification).filter(
        Notification.recipient_email == email, Notification.is_read == False
    ).update({"is_read": True})
    db.commit()
    return {"success": True}
