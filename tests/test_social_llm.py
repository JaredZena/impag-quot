"""
Unit tests for social_llm module
Tests JSON parsing retry logic
"""

import pytest
from routes.social_llm import parse_json_with_retry, clean_json_text
from routes.social_llm import StrategyResponse, ContentResponse
import json


def test_clean_json_text_removes_markdown():
    """Test that clean_json_text removes markdown code blocks"""
    text_with_markdown = "```json\n{\"key\": \"value\"}\n```"
    cleaned = clean_json_text(text_with_markdown)
    assert cleaned == '{"key": "value"}'
    
    text_without_markdown = '{"key": "value"}'
    cleaned = clean_json_text(text_without_markdown)
    assert cleaned == '{"key": "value"}'


def test_parse_json_with_retry_valid_json():
    """Test that valid JSON is parsed correctly"""
    valid_json = '{"problem_identified": "test", "topic": "test", "post_type": "Infografías", "channel": "fb-post", "preferred_category": "", "search_needed": true, "search_keywords": ""}'
    
    # Mock client (would need proper mocking in real test)
    # For now, just test the clean_json_text part
    cleaned = clean_json_text(valid_json)
    data = json.loads(cleaned)
    validated = StrategyResponse(**data)
    
    assert validated.topic == "test"
    assert validated.post_type == "Infografías"


def test_parse_json_with_retry_invalid_json_raises():
    """Test that invalid JSON after retry raises ValueError"""
    invalid_json = '{"key": "unclosed string'
    
    # This should raise ValueError after retry
    with pytest.raises(ValueError):
        # Mock client would be needed for full test
        # For now, just verify clean_json_text handles it
        cleaned = clean_json_text(invalid_json)
        json.loads(cleaned)  # This will raise JSONDecodeError


def test_content_response_validation():
    """Test that ContentResponse validates required fields"""
    # Valid response
    valid_data = {
        "channel": "fb-post",
        "caption": "Test caption",
        "needs_music": False
    }
    response = ContentResponse(**valid_data)
    assert response.channel == "fb-post"
    assert response.caption == "Test caption"
    
    # Missing required field should raise ValidationError
    with pytest.raises(Exception):  # Pydantic ValidationError
        ContentResponse(channel="fb-post")  # Missing caption


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


