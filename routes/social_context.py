"""
Social Media Context Module
Handles Durango sector context loading, summarization, and caching.
"""

from pathlib import Path
from typing import Dict
import logging

logger = logging.getLogger(__name__)

# Simple in-memory cache for context (key: month, value: context string)
_durango_context_cache: Dict[int, str] = {}

# Cache for summaries (key: month, value: summary string)
_durango_summary_cache: Dict[int, str] = {}


def load_durango_context(month: int, use_summary: bool = True) -> str:
    """
    Load Durango sector context (agricultura, forestal, ganadería, agroindustria) from markdown files.
    Returns formatted context string for AI prompts.
    
    Uses a simple cache to avoid re-reading files on every request.
    Cache is cleared on server restart (stateless).
    
    Args:
        month: Month number (1-12)
        use_summary: If True, returns a summarized version (default: True for token efficiency)
    
    Returns:
        Formatted context string
    """
    cache_key = f"{month}_{'summary' if use_summary else 'full'}"
    
    # Check cache first
    if use_summary and month in _durango_summary_cache:
        return _durango_summary_cache[month]
    elif not use_summary and month in _durango_context_cache:
        return _durango_context_cache[month]
    
    try:
        # Get the docs directory (docs is at impag-app/docs, we're in impag-quot/routes/social.py)
        # So we need to go up: impag-quot -> impag-app -> docs
        current_file = Path(__file__)  # impag-quot/routes/social_context.py
        project_root = current_file.parent.parent.parent  # impag-app
        docs_dir = project_root / "docs"
        
        context_parts = []
        
        # Load agricultura context
        agricultura_file = docs_dir / "durango-agricultura.md"
        if agricultura_file.exists():
            with open(agricultura_file, 'r', encoding='utf-8') as f:
                agricultura_content = f.read()
                if use_summary:
                    month_section = extract_month_section(agricultura_content, month)
                    key_stats = extract_key_stats(agricultura_content, "agricultura")
                    agricultura_context = month_section
                    if key_stats:
                        agricultura_context = f"{key_stats}\n\n{month_section}" if month_section else key_stats
                else:
                    agricultura_context = agricultura_content
                if agricultura_context.strip():
                    context_parts.append(f"AGRICULTURA DURANGO:\n{agricultura_context[:800]}...")  # Limit length
        
        # Load forestal context
        forestal_file = docs_dir / "durango-forestal.md"
        if forestal_file.exists():
            with open(forestal_file, 'r', encoding='utf-8') as f:
                forestal_content = f.read()
                if use_summary:
                    month_section = extract_month_section(forestal_content, month)
                    key_stats = extract_key_stats(forestal_content, "forestal")
                    forestal_context = month_section
                    if key_stats:
                        forestal_context = f"{key_stats}\n\n{month_section}" if month_section else key_stats
                else:
                    forestal_context = forestal_content
                if forestal_context.strip():
                    context_parts.append(f"FORESTAL DURANGO:\n{forestal_context[:800]}...")  # Limit length
        
        # Load ganadería context
        ganaderia_file = docs_dir / "durango-ganaderia.md"
        if ganaderia_file.exists():
            with open(ganaderia_file, 'r', encoding='utf-8') as f:
                ganaderia_content = f.read()
                if use_summary:
                    month_section = extract_month_section(ganaderia_content, month)
                    key_stats = extract_key_stats(ganaderia_content, "ganaderia")
                    ganaderia_context = month_section
                    if key_stats:
                        ganaderia_context = f"{key_stats}\n\n{month_section}" if month_section else key_stats
                else:
                    ganaderia_context = ganaderia_content
                if ganaderia_context.strip():
                    context_parts.append(f"GANADERÍA DURANGO:\n{ganaderia_context[:800]}...")  # Limit length
        
        # Load agroindustria context
        agroindustria_file = docs_dir / "durango-agroindustria.md"
        if agroindustria_file.exists():
            with open(agroindustria_file, 'r', encoding='utf-8') as f:
                agroindustria_content = f.read()
                if use_summary:
                    month_section = extract_month_section(agroindustria_content, month)
                    if month_section:
                        context_parts.append(f"AGROINDUSTRIA DURANGO:\n{month_section[:800]}...")
                    else:
                        summary = extract_agroindustria_summary(agroindustria_content)
                        if summary:
                            context_parts.append(f"AGROINDUSTRIA DURANGO:\n{summary[:800]}...")
                else:
                    context_parts.append(f"AGROINDUSTRIA DURANGO:\n{agroindustria_content[:800]}...")
        
        if context_parts:
            result = "\n\n".join(context_parts)
            # Cache the result (limit cache size to avoid memory issues)
            if use_summary:
                if len(_durango_summary_cache) < 12:  # Only cache up to 12 months
                    _durango_summary_cache[month] = result
            else:
                if len(_durango_context_cache) < 12:  # Only cache up to 12 months
                    _durango_context_cache[month] = result
            return result
        else:
            # Fallback to hardcoded if files don't exist
            return get_fallback_durango_context(month)
    except Exception as e:
        logger.error(f"Error loading Durango context: {e}", exc_info=True)
        return get_fallback_durango_context(month)


def extract_key_stats(content: str, sector: str) -> str:
    """
    Extract key statistics and rankings from markdown content.
    Looks for sections like "## Posicionamiento Nacional" or "## Estadísticas"
    """
    lines = content.split('\n')
    stats_lines = []
    in_stats_section = False
    
    # Look for key sections that contain important stats
    key_sections = [
        "posicionamiento nacional",
        "estadísticas",
        "ranking",
        "producción total",
        "valor de producción"
    ]
    
    for i, line in enumerate(lines):
        line_lower = line.lower()
        # Check if this is a relevant stats section
        if any(key in line_lower for key in key_sections) and ('##' in line or '###' in line):
            in_stats_section = True
            stats_lines.append(line)
        elif in_stats_section:
            # Stop at next major section (##) that's not a sub-section
            if line.startswith('##') and not line.startswith('###'):
                break
            # Include subsections and content
            if line.strip() and (line.startswith('#') or line.startswith('-') or ':' in line):
                stats_lines.append(line)
            # Limit to avoid too much context
            if len(stats_lines) > 15:
                break
    
    return '\n'.join(stats_lines[:15]) if stats_lines else ""


def extract_agroindustria_summary(content: str) -> str:
    """
    Extract summary sections from agroindustria markdown (Resumen, Contexto, Oportunidades).
    """
    lines = content.split('\n')
    summary_lines = []
    sections_to_include = [
        "## Resumen General",
        "## Contexto del Sector",
        "## Oportunidades"
    ]
    in_target_section = False
    current_section = None
    
    for line in lines:
        # Check if entering a target section
        if any(section in line for section in sections_to_include):
            in_target_section = True
            current_section = line
            summary_lines.append(line)
        elif in_target_section:
            # Stop at next major section (##) that's not a sub-section
            if line.startswith('##') and not line.startswith('###'):
                # Check if this is another target section
                if any(section in line for section in sections_to_include):
                    current_section = line
                    summary_lines.append(line)
                else:
                    break
            else:
                # Include content (limit length)
                if line.strip():
                    summary_lines.append(line)
                    if len(summary_lines) > 60:  # Limit total lines
                        break
    
    return '\n'.join(summary_lines) if summary_lines else ""


def extract_month_section(content: str, month: int) -> str:
    """
    Extract the section relevant to the given month from markdown content.
    Looks for sections like "### Enero-Febrero" or "## Ciclos por Mes"
    """
    month_names = {
        1: ["enero", "febrero"], 2: ["enero", "febrero"],
        3: ["marzo", "abril"], 4: ["marzo", "abril"],
        5: ["mayo", "junio", "julio"], 6: ["mayo", "junio", "julio"], 7: ["mayo", "junio", "julio"],
        8: ["agosto", "septiembre"], 9: ["agosto", "septiembre"],
        10: ["octubre", "noviembre"], 11: ["octubre", "noviembre"],
        12: ["diciembre"]
    }
    
    target_months = month_names.get(month, [])
    if not target_months:
        return ""
    
    lines = content.split('\n')
    in_relevant_section = False
    current_section = []
    
    for line in lines:
        # Check if this is a month header
        line_lower = line.lower()
        if any(month_name in line_lower for month_name in target_months) and ('###' in line or '##' in line):
            in_relevant_section = True
            current_section = [line]
        elif in_relevant_section:
            # Stop at next major section (## or ###)
            if line.startswith('##') and not any(month_name in line.lower() for month_name in target_months):
                break
            current_section.append(line)
    
    if current_section:
        return '\n'.join(current_section)
    
    # If no specific month section found, return summary from "Ciclos por Mes" section
    return extract_general_cycles(content)


def extract_general_cycles(content: str) -> str:
    """Extract general cycle information if month-specific not found."""
    lines = content.split('\n')
    in_cycles_section = False
    cycles_lines = []
    
    for line in lines:
        if 'ciclos' in line.lower() and ('##' in line or '###' in line):
            in_cycles_section = True
        elif in_cycles_section:
            if line.startswith('##') and 'ciclos' not in line.lower():
                break
            if line.strip():
                cycles_lines.append(line)
    
    return '\n'.join(cycles_lines[:20]) if cycles_lines else "Información general del sector disponible."


def get_fallback_durango_context(month: int) -> str:
    """Fallback context if markdown files are not available."""
    if month in [1, 2]:
        return "Ciclos Durango: Preparación siembra maíz/frijol (feb-mar), mantenimiento avena/alfalfa/trigo (cultivos de frío otoño-invierno). Forestal: Protección árboles jóvenes contra heladas, mantenimiento viveros forestales. Ganadero: Alimentación suplementaria, protección ganado contra frío, mantenimiento cercas y corrales."
    elif month in [3, 4]:
        return "Ciclos Durango: Siembra maíz/frijol activa, crecimiento avena/alfalfa/trigo, inicio manzana. Forestal: Siembra/reforestación activa, trasplante árboles, preparación viveros. Ganadero: Pastoreo primaveral, reparación cercas post-invierno, preparación agostaderos."
    elif month in [5, 6, 7]:
        return "Ciclos Durango: Crecimiento maíz/frijol, cosecha avena/alfalfa, desarrollo manzana, inicio chile. Forestal: Crecimiento activo árboles, mantenimiento reforestaciones, control plagas forestales. Ganadero: Pastoreo intensivo, construcción/reparación cercas, protección sombra para ganado, preparación henificación."
    elif month in [8, 9]:
        return "Ciclos Durango: Cosecha manzana (ago-sep), desarrollo chile, preparación siembra otoño-invierno (avena, trigo, cultivos de frío). Forestal: Mantenimiento reforestaciones, preparación viveros otoño-invierno, protección contra incendios. Ganadero: Cosecha forraje, henificación, preparación alimentación invernal, mantenimiento infraestructura ganadera."
    elif month in [10, 11]:
        return "Ciclos Durango: Cosecha frijol (oct-nov), cosecha chile (oct-nov), siembra activa avena/trigo (cultivos de frío otoño-invierno), preparación protección frío. Forestal: Siembra otoño-invierno especies forestales, protección árboles contra heladas tempranas, mantenimiento viveros. Ganadero: Almacenamiento forraje, preparación protección ganado frío, reparación cercas y corrales, alimentación suplementaria inicio."
    elif month == 12:
        return "Ciclos Durango: Protección heladas crítica, mantenimiento invernal cultivos de frío (avena/trigo), preparación nuevo ciclo. Forestal: Protección árboles contra heladas, mantenimiento viveros invernal, planificación reforestación siguiente año. Ganadero: Protección ganado heladas crítica, alimentación suplementaria intensiva, mantenimiento cercas y refugios, preparación próximo ciclo."
    return ""


