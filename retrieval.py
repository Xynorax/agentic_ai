"""Selective retrieval of past conversation content.

Indexes new messages (human and tool outputs) into a FAISS vector store as
they appear, then retrieves relevant chunks to inject into the agent context.
"""

import numpy as np
import faiss

from langchain_core.messages import HumanMessage, ToolMessage

from tool_pdf_parser import chunk_text, embed_texts

RETRIEVAL_TOP_K = 3


class MessageStore:
    """FAISS index over chunked message contents (cosine similarity)."""

    def __init__(self):
        self.index = None
        self.chunks = []

    def add(self, text):
        chunks = chunk_text(text)
        if not chunks:
            return
        vectors = np.array(embed_texts(chunks)).astype("float32")
        faiss.normalize_L2(vectors)
        if self.index is None:
            self.index = faiss.IndexFlatIP(vectors.shape[1])
        self.index.add(vectors)
        self.chunks.extend(chunks)

    def query(self, text, top_k=RETRIEVAL_TOP_K):
        if self.index is None or not self.chunks:
            return []
        query_vec = np.array([embed_texts([text])[0]]).astype("float32")
        faiss.normalize_L2(query_vec)
        _, idx = self.index.search(query_vec, min(top_k, len(self.chunks)))
        return [self.chunks[i] for i in idx[0] if 0 <= i < len(self.chunks)]


_store = MessageStore()
_seen_ids = set()


def _message_text(msg):
    if not isinstance(msg, (HumanMessage, ToolMessage)):
        return None
    content = msg.content
    if isinstance(content, str) and content.strip():
        return content
    return None


def index_messages(messages):
    """Embed and index any new human/tool messages not seen yet."""
    for msg in messages:
        msg_id = getattr(msg, "id", None)
        if msg_id is not None:
            if msg_id in _seen_ids:
                continue
            _seen_ids.add(msg_id)
        text = _message_text(msg)
        if text:
            _store.add(text)


def retrieve_context(query, top_k=RETRIEVAL_TOP_K):
    """Return the top_k chunks most relevant to the query."""
    if not query:
        return []
    return _store.query(query, top_k=top_k)
