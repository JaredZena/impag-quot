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


def test_keeps_tee_and_adds_unaccented_variants():
    kw = _query_keywords('Válvula esfera 1.25" y tee pvc')
    assert "tee" in kw and "valvula" in kw and "válvula" in kw


def test_bom_components_reads_the_quantities_list():
    from rag_system_moved.rag_system import _bom_components
    report = (
        "--- REPORTE DE CÁLCULO ---\n"
        "RECOMENDACIÓN DE CANTIDADES:\n"
        "- **Tee PVC 1.25\"**: 20 pzas\n"
        "- Válvula esfera 1.25\": 9 pzas\n"
        "- Tee PVC 1.25\": 4 pzas\n"
        "\n"
        "--- CONDICIONES COMERCIALES SUGERIDAS ---\n"
        "- Vigencia: 15 días\n"
    )
    assert _bom_components(report) == ['Tee PVC 1.25"', 'Válvula esfera 1.25"']


def test_bom_components_without_list_is_empty():
    from rag_system_moved.rag_system import _bom_components
    assert _bom_components("TIPO DE SOLICITUD: SOLICITUD DIRECTA") == []
    assert _bom_components(None) == []


def test_job_requests_never_take_the_simple_path():
    from rag_system_moved.rag_system import classify_quotation_tier

    class SP:
        cost = 10

    priced = [SP()] * 5
    assert classify_quotation_tier("UN SISTEMA DE RIEGO PARA 20X20 CON XCEL WOBBLER", priced) == 'mediana'
    assert classify_quotation_tier("riego para 3 ha de nogal", priced) == 'mediana'
    assert classify_quotation_tier("10 rollos de mallasombra 50%", priced) == 'sencilla'


def test_bom_components_reads_a_markdown_table():
    from rag_system_moved.rag_system import _bom_components
    report = (
        "## RECOMENDACIÓN DE CANTIDADES\n\n"
        "| # | Producto | Diámetro | Cantidad | Unidad |\n"
        "|---|----------|----------|----------|--------|\n"
        "| 1 | Tee PVC hidráulica | 1.25\" | 3 | Pieza |\n"
        "| 2 | Manguera agrícola RD17 ⚠️ | 1.25\" | 60 | Metro |\n"
        "| 3 | Teflón | — | 3 | Pieza |\n"
        "\n---\n"
    )
    assert _bom_components(report) == ['Tee PVC hidráulica 1.25"', 'Manguera agrícola RD17 1.25"', 'Teflón']


def test_same_part_needs_the_part_noun_and_diameter():
    from rag_system_moved.rag_system import _names_same_part
    assert _names_same_part('Tee PVC hidráulica 1.25"', 'Tee pvc 1.25" (1 1/4")')
    assert _names_same_part('Reducción bushing 1.25"', 'REDUCCION BUCHING 1.25"X3/4"')
    assert _names_same_part('Tubería PVC RD26 1.25"', 'TUBERIA PVC 32 MM RD 26') is True
    assert not _names_same_part('Válvula de esfera PVC 1.25"', 'Tee pvc 1.25" (1 1/4")')
    assert not _names_same_part('Tubería PVC RD26 1.25"', 'TUBERIA 2" PVC RD26 C/A IPS')


def test_same_part_rejects_other_valve_kinds_and_bigger_sizes():
    from rag_system_moved.rag_system import _names_same_part
    assert not _names_same_part('Válvula de aire 1/2"', 'Válvula de pie Pichancha malla 1 1/2"')
    assert _names_same_part('Válvula de aire 2"', 'VALVULA DE AIRE CINETICA EMEK 2" AV-010')
    assert not _names_same_part('Adaptador macho 1"', 'ADAPTADOR HEMBRA DE 1" PVC')
    assert _names_same_part('Tubería PVC RD26 1.25"', 'TUBERIA PVC 32 MM RD 26')
    assert not _names_same_part('Abrazadera 1.25"', 'NIPLE CUELLO DE BOTELLA 1.25" CON ABRAZADERA')


def test_bom_components_reads_sectioned_tables_until_conditions():
    from rag_system_moved.rag_system import _bom_components
    report = (
        "## RECOMENDACIÓN DE CANTIDADES\n\n### ASPERSORES\n\n"
        "| Producto | Cantidad | Unidad |\n|---|---|---|\n"
        "| Aspersor Xcel Wobbler 1/2\" | 16 | Pieza |\n\n---\n\n### ACCESORIOS\n\n"
        "| Producto | Cantidad | Unidad |\n|---|---|---|\n"
        "| Tee PVC 1.25\" | 4 | Pieza |\n\n---\n\n"
        "# --- CONDICIONES COMERCIALES SUGERIDAS ---\n- Vigencia 15 días\n"
    )
    assert _bom_components(report) == ['Aspersor Xcel Wobbler 1/2"', 'Tee PVC 1.25"']
