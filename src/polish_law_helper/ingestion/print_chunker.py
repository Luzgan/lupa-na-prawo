"""Chunk Sejm print text into embedding-ready pieces."""

from dataclasses import dataclass

CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200


@dataclass
class PrintChunkData:
    """Ready-to-store print chunk."""

    chunk_index: int
    text_content: str
    text_for_embedding: str
    char_count: int


def chunk_print_text(print_title: str, text: str) -> list[PrintChunkData]:
    """Split print text into overlapping chunks of ~CHUNK_SIZE characters.

    Each chunk's text_for_embedding includes the print title as context.
    """
    if not text or not text.strip():
        return []

    text = text.strip()

    # If the whole text fits in one chunk, return it directly
    if len(text) <= CHUNK_SIZE:
        return [
            PrintChunkData(
                chunk_index=0,
                text_content=text,
                text_for_embedding=f"{print_title}\n\n{text}",
                char_count=len(text),
            )
        ]

    chunks: list[PrintChunkData] = []
    start = 0
    idx = 0

    while start < len(text):
        end = start + CHUNK_SIZE

        # If we're not at the very end, try to break at a newline or sentence boundary
        if end < len(text):
            # Look for a newline near the boundary
            newline_pos = text.rfind("\n", start + CHUNK_SIZE - 200, end + 100)
            if newline_pos > start:
                end = newline_pos
            else:
                # Look for a period followed by space
                period_pos = text.rfind(". ", start + CHUNK_SIZE - 200, end + 100)
                if period_pos > start:
                    end = period_pos + 1  # include the period

        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append(
                PrintChunkData(
                    chunk_index=idx,
                    text_content=chunk_text,
                    text_for_embedding=f"{print_title}\n\n{chunk_text}",
                    char_count=len(chunk_text),
                )
            )
            idx += 1

        # Move start forward, accounting for overlap
        start = end - CHUNK_OVERLAP
        if start <= (end - CHUNK_SIZE):
            # Prevent infinite loop if overlap is larger than progress
            start = end

    return chunks
