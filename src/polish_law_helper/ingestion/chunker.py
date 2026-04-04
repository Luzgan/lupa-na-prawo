"""Convert parsed LegalUnit trees into embedding-ready chunks."""

from dataclasses import dataclass

from polish_law_helper.ingestion.html_parser import (
    LegalUnit,
    collect_articles_with_context,
)

# Thresholds for splitting
ARTICLE_SPLIT_THRESHOLD = 1500  # chars - split article into paragraphs
PARAGRAPH_SPLIT_THRESHOLD = 2000  # chars - split paragraph into points


@dataclass
class ChunkData:
    """Ready-to-store chunk with hierarchy metadata."""

    part_num: str | None = None
    part_title: str | None = None
    title_num: str | None = None
    title_name: str | None = None
    section_num: str | None = None
    section_title: str | None = None
    chapter_num: str | None = None
    chapter_title: str | None = None
    article_num: str = ""
    paragraph_num: str | None = None
    point_num: str | None = None
    text_content: str = ""
    text_for_embedding: str = ""
    char_count: int = 0


def _build_hierarchy_prefix(
    act_title: str,
    context: dict[str, tuple[str, str | None]],
    article_num: str,
    paragraph_num: str | None = None,
    point_num: str | None = None,
) -> str:
    """Build hierarchy path string for embedding context."""
    parts = [act_title]

    type_labels = {
        "part": "Część",
        "title": "Tytuł",
        "section": "Dział",
        "chapter": "Rozdział",
    }

    for unit_type in ["part", "title", "section", "chapter"]:
        if unit_type in context:
            num, name = context[unit_type]
            label = type_labels[unit_type]
            entry = f"{label} {num}"
            if name:
                entry += f". {name}"
            parts.append(entry)

    art_label = f"Art. {article_num}"
    if paragraph_num:
        art_label += f" § {paragraph_num}"
    if point_num:
        art_label += f" pkt {point_num}"
    parts.append(art_label)

    return " > ".join(parts)


def _context_to_chunk_fields(
    context: dict[str, tuple[str, str | None]],
) -> dict:
    """Convert context dict to chunk field values."""
    fields = {}
    mapping = {
        "part": ("part_num", "part_title"),
        "title": ("title_num", "title_name"),
        "section": ("section_num", "section_title"),
        "chapter": ("chapter_num", "chapter_title"),
    }
    for unit_type, (num_field, name_field) in mapping.items():
        if unit_type in context:
            num, name = context[unit_type]
            fields[num_field] = num
            fields[name_field] = name
    return fields


def chunk_article(
    act_title: str,
    context: dict[str, tuple[str, str | None]],
    article: LegalUnit,
) -> list[ChunkData]:
    """Chunk a single article into one or more ChunkData objects."""
    base_fields = _context_to_chunk_fields(context)
    base_fields["article_num"] = article.number

    full_text = _get_article_full_text(article)

    # Short article: one chunk
    if len(full_text) <= ARTICLE_SPLIT_THRESHOLD or not article.children:
        prefix = _build_hierarchy_prefix(act_title, context, article.number)
        embedding_text = f"{prefix}\n\n{full_text}"
        return [
            ChunkData(
                **base_fields,
                text_content=full_text,
                text_for_embedding=embedding_text,
                char_count=len(full_text),
            )
        ]

    # Longer article: split by paragraphs/points
    chunks = []
    for child in article.children:
        if child.unit_type == "paragraph":
            child_text = _get_unit_text(child)

            if len(child_text) <= PARAGRAPH_SPLIT_THRESHOLD or not child.children:
                prefix = _build_hierarchy_prefix(
                    act_title, context, article.number, paragraph_num=child.number
                )
                chunks.append(
                    ChunkData(
                        **base_fields,
                        paragraph_num=child.number,
                        text_content=child_text,
                        text_for_embedding=f"{prefix}\n\n{child_text}",
                        char_count=len(child_text),
                    )
                )
            else:
                # Split paragraph into points
                for point in child.children:
                    if point.unit_type == "point":
                        point_text = point.text
                        prefix = _build_hierarchy_prefix(
                            act_title,
                            context,
                            article.number,
                            paragraph_num=child.number,
                            point_num=point.number,
                        )
                        chunks.append(
                            ChunkData(
                                **base_fields,
                                paragraph_num=child.number,
                                point_num=point.number,
                                text_content=point_text,
                                text_for_embedding=f"{prefix}\n\n{point_text}",
                                char_count=len(point_text),
                            )
                        )

        elif child.unit_type == "point":
            point_text = child.text
            prefix = _build_hierarchy_prefix(
                act_title, context, article.number, point_num=child.number
            )
            chunks.append(
                ChunkData(
                    **base_fields,
                    point_num=child.number,
                    text_content=point_text,
                    text_for_embedding=f"{prefix}\n\n{point_text}",
                    char_count=len(point_text),
                )
            )

    # If no chunks were created from children, fall back to whole article
    if not chunks:
        prefix = _build_hierarchy_prefix(act_title, context, article.number)
        embedding_text = f"{prefix}\n\n{full_text}"
        return [
            ChunkData(
                **base_fields,
                text_content=full_text,
                text_for_embedding=embedding_text,
                char_count=len(full_text),
            )
        ]

    return chunks


def chunk_plain_text(
    act_title: str, text: str, chunk_size: int = 1500, overlap: int = 200
) -> list[ChunkData]:
    """Chunk plain text into overlapping pieces for embedding.

    Used when structured parsing isn't possible (PDF acts, unusual HTML formats).
    """
    chunks: list[ChunkData] = []
    # Split on paragraph boundaries (double newlines) first
    paragraphs = text.split("\n\n")

    current_chunk = ""
    chunk_idx = 0

    for para in paragraphs:
        if len(current_chunk) + len(para) > chunk_size and current_chunk:
            # Save current chunk
            text_for_embedding = f"{act_title}\n\n{current_chunk}"
            chunks.append(
                ChunkData(
                    article_num=f"fragment-{chunk_idx + 1}",
                    text_content=current_chunk.strip(),
                    text_for_embedding=text_for_embedding,
                    char_count=len(current_chunk),
                )
            )
            chunk_idx += 1
            # Keep overlap from the end of the current chunk
            words = current_chunk.split()
            overlap_words = words[-overlap // 5 :] if len(words) > overlap // 5 else words
            current_chunk = " ".join(overlap_words) + "\n\n" + para
        else:
            current_chunk = current_chunk + "\n\n" + para if current_chunk else para

    # Don't forget the last chunk
    if current_chunk.strip():
        text_for_embedding = f"{act_title}\n\n{current_chunk}"
        chunks.append(
            ChunkData(
                article_num=f"fragment-{chunk_idx + 1}",
                text_content=current_chunk.strip(),
                text_for_embedding=text_for_embedding,
                char_count=len(current_chunk),
            )
        )

    return chunks


def _get_article_full_text(article: LegalUnit) -> str:
    """Get concatenated text from article and all its children."""
    parts = []
    if article.text:
        parts.append(article.text)
    for child in article.children:
        parts.append(_get_unit_text(child))
    return "\n".join(parts)


def _get_unit_text(unit: LegalUnit) -> str:
    """Get text from a unit and its children."""
    parts = []
    if unit.text:
        parts.append(unit.text)
    for child in unit.children:
        parts.append(_get_unit_text(child))
    return "\n".join(parts)


def chunk_act(act_title: str, units: list[LegalUnit]) -> list[ChunkData]:
    """Chunk an entire act into embedding-ready chunks.

    Args:
        act_title: The act's title (e.g. "Kodeks cywilny")
        units: Parsed LegalUnit trees from html_parser

    Returns:
        List of ChunkData objects ready for embedding and storage.
    """
    articles_with_context = collect_articles_with_context(units)
    all_chunks = []

    for context, article in articles_with_context:
        chunks = chunk_article(act_title, context, article)
        all_chunks.extend(chunks)

    return all_chunks
