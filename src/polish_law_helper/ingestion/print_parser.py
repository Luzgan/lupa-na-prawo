"""Extract text from Sejm print HTML attachments."""

import httpx
from bs4 import BeautifulSoup
from rich.console import Console

from polish_law_helper.ingestion.sejm_client import SejmClient

console = Console()

# File extensions we can handle
_HTML_EXTENSIONS = {".htm", ".html", ".txt", ".xhtml"}
_PDF_EXTENSIONS = {".pdf"}


async def extract_print_text(
    sejm_client: SejmClient,
    term: int,
    print_number: str,
) -> str | None:
    """Fetch a Sejm print and extract text from its first HTML attachment.

    Returns the extracted plain text, or None if no suitable attachment found.
    """
    try:
        print_data = await sejm_client.get_print(print_number)
    except Exception as e:
        console.print(f"  [red]Failed to fetch print {print_number}: {e}[/red]")
        return None

    attachments = print_data.get("attachments", [])
    if not attachments:
        console.print(f"  [dim]No attachments for print {print_number}[/dim]")
        return None

    # Categorize attachments
    att_names = [a if isinstance(a, str) else a.get("name", "") for a in attachments]
    html_att = next((n for n in att_names if any(n.lower().endswith(e) for e in _HTML_EXTENSIONS)), None)
    pdf_att = next((n for n in att_names if any(n.lower().endswith(e) for e in _PDF_EXTENSIONS)), None)

    client = await sejm_client._get_client()
    base = f"{sejm_client.base_url}/term{term}/prints/{print_number}"

    # Try HTML first
    if html_att:
        try:
            resp = await client.get(f"{base}/{html_att}")
            resp.raise_for_status()
            text = _extract_text_from_html(resp.text)
            if text and len(text.strip()) >= 50:
                return text.strip()
        except Exception as e:
            console.print(f"  [yellow]Failed to fetch HTML {html_att}: {e}[/yellow]")

    # Fallback to PDF
    if pdf_att:
        try:
            resp = await client.get(f"{base}/{pdf_att}", timeout=120.0)
            resp.raise_for_status()
            text = _extract_text_from_pdf(resp.content)
            if text and len(text.strip()) >= 50:
                return text.strip()
        except Exception as e:
            console.print(f"  [yellow]Failed to fetch PDF {pdf_att}: {e}[/yellow]")

    console.print(
        f"  [dim]Failed to extract text from print {print_number} "
        f"(attachments: {att_names[:3]})[/dim]"
    )
    return None


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract plain text from PDF bytes using PyMuPDF."""
    try:
        import fitz

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        return "\n".join(text_parts)
    except Exception:
        return ""


def _extract_text_from_html(html: str) -> str:
    """Extract plain text from HTML content using BeautifulSoup."""
    soup = BeautifulSoup(html, "lxml")

    # Remove script and style elements
    for tag in soup(["script", "style", "head", "meta", "link"]):
        tag.decompose()

    # Get text with newlines between block elements
    text = soup.get_text(separator="\n", strip=True)

    # Clean up excessive whitespace while preserving paragraph breaks
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            lines.append(stripped)

    return "\n".join(lines)
