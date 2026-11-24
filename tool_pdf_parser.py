import hashlib
import json
import os

import faiss
import numpy as np
from openai import OpenAI
from pypdf import PdfReader

from langchain_core.tools import tool

from safety import ALLOWED_KNOWLEDGE_ROOTS, SecurityError, validate_path
from Config import config

# ---------------------------------------------------------------------------
# Local (Ollama) configuration
# ---------------------------------------------------------------------------

EMBEDDING_MODEL = config["EMBEDDING_MODEL_NAME"]

PDF_LIBRARY_DIR = r"D:\Youssef\TUM\Books\chip_design"
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_index_cache")

_client = OpenAI(base_url=config["EMBEDDING_BASE_URL"], api_key=config["API_KEY"], timeout=6000.0)


def list_available_pdfs() -> list[str]:
    """Return the PDF filenames available in the configured book directory."""
    if not os.path.isdir(PDF_LIBRARY_DIR):
        return []
    return sorted(f for f in os.listdir(PDF_LIBRARY_DIR) if f.lower().endswith(".pdf"))


def resolve_pdf_path(pdf_name: str) -> str:
    """Accept a filename inside PDF_LIBRARY_DIR, or a validated full path."""
    path = os.path.expanduser(pdf_name)
    candidate = path if os.path.isabs(path) else os.path.join(PDF_LIBRARY_DIR, path)
    try:
        return validate_path(
            candidate,
            ALLOWED_KNOWLEDGE_ROOTS,
            allowed_suffixes={".pdf"},
            label="PDF path",
        )
    except (SecurityError, FileNotFoundError):
        available = ", ".join(list_available_pdfs()) or "(none found)"
        raise FileNotFoundError(
            f"PDF not found or not allowed: {candidate}. "
            f"Allowed folders: {', '.join(ALLOWED_KNOWLEDGE_ROOTS)}. "
            f"Available PDFs in {PDF_LIBRARY_DIR}: {available}"
        )


# ---------------------------------------------------------------------------
# 1. Extract raw text from the PDF file
# ---------------------------------------------------------------------------
def extract_text_from_pdf(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text += page_text + "\n"
    return text


# ---------------------------------------------------------------------------
# 2. Chunk text using a sliding window with overlap
# ---------------------------------------------------------------------------
def chunk_text(text: str, chunk_size: int = 300, overlap: int = 50) -> list[str]:
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size - overlap):
        chunk = " ".join(words[i : i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# 3. Local embeddings (Ollama via its OpenAI-compatible /v1/embeddings API)
# ---------------------------------------------------------------------------
def embed_texts(texts: list[str], batch_size: int = 16) -> list[list[float]]:
    vectors = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        response = _client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        data = response.data
        if all(getattr(item, "index", None) is not None for item in data):
            data = sorted(data, key=lambda item: item.index)
        if len(data) != len(batch):
            raise ValueError(
                f"Embedding count mismatch: sent {len(batch)}, got {len(data)}"
            )
        vectors.extend(item.embedding for item in data)
    return vectors


def _cache_paths(pdf_path: str):
    """Cache key includes the file's identity so edited PDFs are re-indexed."""
    stat = os.stat(pdf_path)
    key_source = (
        f"{os.path.abspath(pdf_path)}|{stat.st_mtime_ns}|{stat.st_size}|{EMBEDDING_MODEL}"
    )
    key = hashlib.md5(key_source.encode("utf-8")).hexdigest()
    return (
        os.path.join(CACHE_DIR, key + ".faiss"),
        os.path.join(CACHE_DIR, key + ".json"),
    )


# ---------------------------------------------------------------------------
# 4. Build / load the offline FAISS vector store
# ---------------------------------------------------------------------------
def build_vector_store(pdf_path: str):
    faiss_path, chunks_path = _cache_paths(pdf_path)

    if os.path.isfile(faiss_path) and os.path.isfile(chunks_path):
        print("[cache] Loading cached FAISS index...")
        with open(chunks_path, encoding="utf-8") as f:
            chunks = json.load(f)
        index = faiss.read_index(faiss_path)
        return index, chunks, _client

    print("[1/4] Extracting text from PDF...")
    text = extract_text_from_pdf(pdf_path)

    print("[2/4] Chunking text...")
    chunks = chunk_text(text, chunk_size=300, overlap=50)
    print(f"Generated {len(chunks)} text chunks.")
    if not chunks:
        raise ValueError("No extractable text found in the PDF (it may be image-only).")

    print(f"[3/4] Generating embeddings with local model '{EMBEDDING_MODEL}'...")
    embeddings = embed_texts(chunks)
    embeddings_np = np.array(embeddings).astype("float32")

    # Normalize vectors so inner product == cosine similarity
    faiss.normalize_L2(embeddings_np)

    index = faiss.IndexFlatIP(embeddings_np.shape[1])
    index.add(embeddings_np)

    os.makedirs(CACHE_DIR, exist_ok=True)
    faiss.write_index(index, faiss_path)
    with open(chunks_path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False)

    print("[4/4] FAISS vector index built successfully!")
    return index, chunks, _client


# ---------------------------------------------------------------------------
# 5. Runtime semantic retrieval
# ---------------------------------------------------------------------------
def retrieve_relevant_chunks(
    query: str, index, chunks, embeddings_model=None, top_k: int = 3
) -> list[str]:
    query_emb = np.array([embed_texts([query])[0]]).astype("float32")
    faiss.normalize_L2(query_emb)

    distances, indices = index.search(query_emb, min(top_k, len(chunks)))
    return [chunks[i] for i in indices[0] if 0 <= i < len(chunks)]


# ---------------------------------------------------------------------------
# 6. Agent-facing tool: one call = search a PDF book
# ---------------------------------------------------------------------------
@tool
def query_pdf(query: str, pdf_name: str, top_k: int = 3) -> str:
    """
    Search a PDF book for passages relevant to the query.

    Args:
        query: The question or topic to look for.
        pdf_name: A filename from D:\\Youssef\\TUM\\Books\\chip_design (e.g. "uvm primer.pdf"),
                  or a full Windows path to a PDF.
        top_k: Number of relevant passages to return (default 3).
    """
    try:
        pdf_path = resolve_pdf_path(pdf_name)
        index, chunks, client = build_vector_store(pdf_path)
        passages = retrieve_relevant_chunks(query, index, chunks, client, top_k=top_k)
        if not passages:
            return "No relevant passages found in the PDF."
        return "\n\n".join(f"[{i}] {p}" for i, p in enumerate(passages, 1))
    except Exception as exc:
        # Return errors to the agent instead of crashing the tool loop
        return f"Error searching PDF: {exc}"


if __name__ == "__main__":
    result = query_pdf("What is the Universal Verification Methodology?", "uvm primer.pdf", top_k=2)
    print(result)
