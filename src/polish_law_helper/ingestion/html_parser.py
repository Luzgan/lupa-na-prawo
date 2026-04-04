"""Parse ELI API HTML into structured LegalUnit trees."""

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag


@dataclass
class LegalUnit:
    unit_type: str  # "part", "title", "section", "chapter", "article", "paragraph", "point"
    number: str
    name: str | None = None
    text: str = ""
    children: list["LegalUnit"] = field(default_factory=list)

    def all_articles(self) -> list["LegalUnit"]:
        """Recursively collect all article-level units."""
        if self.unit_type == "article":
            return [self]
        result = []
        for child in self.children:
            result.extend(child.all_articles())
        return result


# Maps CSS classes to unit types
UNIT_CLASS_MAP = {
    "unit_part": "part",
    "unit_ttl": "title",
    "unit_sect": "section",
    "unit_chpt": "chapter",
    "unit_arti": "article",
    "unit_pass": "paragraph",
    "unit_pint": "point",
}

# Hierarchy order for building context paths
HIERARCHY_ORDER = ["part", "title", "section", "chapter"]

# Regex patterns for extracting numbers from headings
ART_PATTERN = re.compile(r"Art\.\s*(\d+[a-z]*)", re.IGNORECASE)
UNIT_NUM_PATTERN = re.compile(
    r"(?:Część|Księga|Tytuł|Dział|Rozdział|Oddział|§)\s*([IVXLCDM\d]+[a-z]*\.?)",
    re.IGNORECASE,
)
PARAGRAPH_PATTERN = re.compile(r"§\s*(\d+[a-z]*)")
USTEP_PATTERN = re.compile(r"(\d+[a-z]*)\.")
POINT_PATTERN = re.compile(r"(\d+[a-z]*)\)")


def _get_unit_type(element: Tag) -> str | None:
    """Determine unit type from CSS classes."""
    classes = element.get("class", [])
    for cls in classes:
        if cls in UNIT_CLASS_MAP:
            return UNIT_CLASS_MAP[cls]
    return None


def _extract_number(element: Tag, unit_type: str) -> str:
    """Extract the unit number from a heading element."""
    heading = element.find(["h1", "h2", "h3", "h4", "h5"])
    if not heading:
        return ""

    text = heading.get_text(strip=True)

    if unit_type == "article":
        m = ART_PATTERN.search(text)
        return m.group(1) if m else text[:20]

    m = UNIT_NUM_PATTERN.search(text)
    if m:
        return m.group(1).rstrip(".")

    return text[:30]


def _extract_name(element: Tag) -> str | None:
    """Extract title/name text from a unit heading."""
    heading = element.find(["h1", "h2", "h3", "h4", "h5"])
    if not heading:
        return None

    text = heading.get_text(strip=True)

    # For articles, there's usually no separate name
    # For chapters etc., the name follows the number
    parts = re.split(r"\n", text, maxsplit=1)
    if len(parts) > 1:
        return parts[1].strip()

    # Try to find name after the unit identifier
    title_span = element.find("span", class_="pro-title-unit")
    if title_span:
        return title_span.get_text(strip=True)

    return None


def _extract_text(element: Tag) -> str:
    """Extract clean text content from an element."""
    # Look for pro-text divs which contain actual legal text
    text_divs = element.find_all("div", class_="pro-text")
    if text_divs:
        texts = [div.get_text(strip=True) for div in text_divs]
        return " ".join(texts)

    # Fallback: get all text, excluding headings
    parts = []
    for child in element.children:
        if isinstance(child, Tag):
            if child.name not in ("h1", "h2", "h3", "h4", "h5"):
                # Skip nested unit divs
                if "unit" not in " ".join(child.get("class", [])):
                    parts.append(child.get_text(strip=True))
        elif isinstance(child, str) and child.strip():
            parts.append(child.strip())
    return " ".join(parts)


def _parse_unit(element: Tag) -> LegalUnit | None:
    """Parse a single unit div into a LegalUnit."""
    unit_type = _get_unit_type(element)
    if not unit_type:
        return None

    number = _extract_number(element, unit_type)
    name = _extract_name(element)

    # For articles and below, extract text directly
    if unit_type in ("article", "paragraph", "point"):
        text = _extract_text(element)
    else:
        text = ""

    # Parse children
    children = []
    for child in element.find_all("div", class_="unit", recursive=False):
        child_unit = _parse_unit(child)
        if child_unit:
            children.append(child_unit)

    # For paragraphs and points within articles
    if unit_type == "article" and not children:
        # Check for paragraphs (ustepy) within this article
        for para_div in element.find_all("div", class_="unit_pass", recursive=False):
            para_unit = _parse_unit(para_div)
            if para_unit:
                children.append(para_unit)

        # Check for points within this article (without paragraph grouping)
        for point_div in element.find_all("div", class_="unit_pint", recursive=False):
            point_unit = _parse_unit(point_div)
            if point_unit:
                children.append(point_unit)

    return LegalUnit(
        unit_type=unit_type,
        number=number,
        name=name,
        text=text,
        children=children,
    )


def parse_act_html(html: str) -> list[LegalUnit]:
    """Parse act HTML into a list of top-level LegalUnit trees.

    Returns the top-level structural units (parts, titles, chapters)
    containing articles as children.
    """
    soup = BeautifulSoup(html, "lxml")

    # Find the main content area
    body = soup.find("body") or soup

    # Try structured parsing first - find top-level unit divs
    top_units = []
    for div in body.find_all("div", class_="unit", recursive=False):
        unit = _parse_unit(div)
        if unit:
            top_units.append(unit)

    if top_units:
        return top_units

    # If no top-level units found, try finding all articles directly
    # (simpler acts without hierarchical structure)
    articles = []
    for div in body.find_all("div", class_="unit_arti"):
        unit = _parse_unit(div)
        if unit:
            articles.append(unit)

    if articles:
        return articles

    # Last resort: regex-based extraction from plain text
    return _fallback_parse(body.get_text())


def _fallback_parse(text: str) -> list[LegalUnit]:
    """Fallback parser for acts without structured HTML classes."""
    articles = []
    # Split on "Art. N." patterns
    splits = re.split(r"(Art\.\s*\d+[a-z]*\.)", text)

    for i in range(1, len(splits), 2):
        art_header = splits[i]
        art_text = splits[i + 1] if i + 1 < len(splits) else ""

        m = ART_PATTERN.search(art_header)
        number = m.group(1) if m else str(i // 2 + 1)

        articles.append(
            LegalUnit(
                unit_type="article",
                number=number,
                text=art_text.strip(),
            )
        )

    return articles


def extract_plain_text(html: str) -> str:
    """Fallback: extract all readable text from HTML when structured parsing fails."""
    soup = BeautifulSoup(html, "lxml")
    # Remove script, style, and navigational elements
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Clean up: collapse multiple newlines, strip whitespace per line
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def collect_articles_with_context(
    units: list[LegalUnit],
    parent_context: dict[str, tuple[str, str | None]] | None = None,
) -> list[tuple[dict[str, tuple[str, str | None]], LegalUnit]]:
    """Collect all articles with their hierarchical context.

    Returns list of (context_dict, article) tuples where context_dict maps
    unit_type -> (number, name) for all parent structural units.
    """
    if parent_context is None:
        parent_context = {}

    result = []
    for unit in units:
        current_context = dict(parent_context)

        if unit.unit_type in HIERARCHY_ORDER:
            current_context[unit.unit_type] = (unit.number, unit.name)

        if unit.unit_type == "article":
            result.append((current_context, unit))
        else:
            result.extend(
                collect_articles_with_context(unit.children, current_context)
            )

    return result
