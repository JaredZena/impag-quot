import os

os.environ.setdefault("ALEMBIC_RUNNING", "1")

from rag_system_moved.rag_system import _query_keywords


def test_keeps_brand_model_and_dimension_tokens():
    kw = _query_keywords("UN SISTEMA DE RIEGO PARA 20X20 CON XCEL WOBBLER PARA ALFALFA")
    assert "xcel" in kw and "wobbler" in kw and "20x20" in kw
    assert "sistema" not in kw and "para" not in kw


def test_keeps_short_pipe_terms_and_inch_sizes():
    kw = _query_keywords('tubo pvc 2" y codos de 1 1/2”')
    assert "pvc" in kw and '2"' in kw and "codos" in kw


def test_job_words_expand_to_catalog_part_words():
    kw = _query_keywords("SISTEMA DE CONDUCCION PRINCIPAL LINEAS REGANTES")
    assert "pvc" in kw and "tuberia" in kw and "manguera" in kw
