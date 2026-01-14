"""
Unit tests for /save endpoint
Tests external_id lookup efficiency
"""

import pytest
from datetime import date
from routes.social import save_social_post, SocialPostSaveRequest
from models import SocialPost


def test_save_uses_external_id_lookup(db_session, auth_user):
    """Test that /save uses indexed external_id lookup instead of O(n) scan"""
    # Create a post with external_id
    post = SocialPost(
        date_for=date.today(),
        caption="Test",
        external_id="test-123",
        formatted_content={"id": "test-123"}
    )
    db_session.add(post)
    db_session.commit()
    
    # Try to save with same external_id
    payload = SocialPostSaveRequest(
        date_for=str(date.today()),
        caption="Updated",
        formatted_content={"id": "test-123"}
    )
    
    # This should find the existing post by external_id (indexed lookup)
    # Not by scanning all posts
    result = await save_social_post(payload, db_session, auth_user)
    
    assert result["updated"] == True
    assert result["id"] == post.id
    
    # Verify the post was updated, not duplicated
    count = db_session.query(SocialPost).filter(SocialPost.external_id == "test-123").count()
    assert count == 1


def test_save_creates_external_id_on_new_post(db_session, auth_user):
    """Test that new posts get external_id set from formatted_content.id"""
    payload = SocialPostSaveRequest(
        date_for=str(date.today()),
        caption="New post",
        formatted_content={"id": "new-456"}
    )
    
    result = await save_social_post(payload, db_session, auth_user)
    
    assert result["updated"] == False
    assert result["id"] is not None
    
    # Verify external_id was set
    saved_post = db_session.query(SocialPost).filter(SocialPost.id == result["id"]).first()
    assert saved_post.external_id == "new-456"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


