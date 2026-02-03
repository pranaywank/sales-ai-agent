#!/usr/bin/env python3
"""
Knowledge Base Indexer (Incremental)

This script indexes documents from the docs/ folder into ChromaDB for RAG retrieval.
It supports incremental indexing - only new or modified files are processed.

Supported file types:
- Markdown (.md)
- Text files (.txt)
- PDF files (.pdf)

Usage:
    python index_knowledge_base.py           # Incremental index (default)
    python index_knowledge_base.py --full    # Full re-index (delete and rebuild)

The indexed data is persisted in .chroma/ folder and used by lead_finder_agent.py
"""

import os
import sys
import json
import hashlib
from pathlib import Path
from typing import Optional

import chromadb

# Document processing
DOCS_FOLDER = os.path.join(os.path.dirname(__file__), "docs")
CHROMA_PERSIST_DIR = os.path.join(os.path.dirname(__file__), ".chroma")
COLLECTION_NAME = "adopt_ai_knowledge"
INDEX_STATE_FILE = os.path.join(os.path.dirname(__file__), ".chroma", "index_state.json")

# Chunk settings
CHUNK_SIZE = 1000  # characters
CHUNK_OVERLAP = 200  # characters

# Files to ignore
IGNORED_FILES = {"readme.md", "README.md"}


def get_file_hash(filepath: str) -> str:
    """Generate a hash of file contents for change detection."""
    with open(filepath, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def load_index_state() -> dict:
    """Load the index state (which files have been indexed and their hashes)."""
    if os.path.exists(INDEX_STATE_FILE):
        try:
            with open(INDEX_STATE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_index_state(state: dict):
    """Save the index state."""
    os.makedirs(os.path.dirname(INDEX_STATE_FILE), exist_ok=True)
    with open(INDEX_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def load_text_file(filepath: str) -> str:
    """Load a text or markdown file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def load_pdf_file(filepath: str) -> str:
    """Load a PDF file and extract text."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(filepath)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
        return text
    except ImportError:
        print(f"   ⚠️ pypdf not installed, skipping PDF: {filepath}")
        return ""
    except Exception as e:
        print(f"   ⚠️ Error reading PDF {filepath}: {e}")
        return ""


def load_document(filepath: str) -> Optional[str]:
    """Load a document based on its file extension."""
    ext = Path(filepath).suffix.lower()
    
    if ext in [".md", ".txt"]:
        return load_text_file(filepath)
    elif ext == ".pdf":
        return load_pdf_file(filepath)
    else:
        return None


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks."""
    if len(text) <= chunk_size:
        return [text]
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + chunk_size
        
        # Try to break at a sentence or paragraph boundary
        if end < len(text):
            # Look for paragraph break
            para_break = text.rfind("\n\n", start, end)
            if para_break > start + chunk_size // 2:
                end = para_break
            else:
                # Look for sentence break
                for sep in [". ", ".\n", "! ", "? "]:
                    sent_break = text.rfind(sep, start, end)
                    if sent_break > start + chunk_size // 2:
                        end = sent_break + 1
                        break
        
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        
        start = end - overlap if end < len(text) else len(text)
    
    return chunks


def get_document_metadata(filepath: str) -> dict:
    """Extract metadata from document filepath."""
    rel_path = os.path.relpath(filepath, DOCS_FOLDER)
    parts = Path(rel_path).parts
    
    metadata = {
        "source": rel_path,
        "filename": Path(filepath).name,
        "file_type": Path(filepath).suffix.lower(),
    }
    
    # Extract category from folder structure (e.g., "capabilities", "use-cases")
    if len(parts) > 1:
        metadata["category"] = parts[0]
    
    return metadata


def get_all_documents() -> list[str]:
    """Get all document paths in the docs folder."""
    supported_extensions = {".md", ".txt", ".pdf"}
    documents = []
    
    for root, dirs, files in os.walk(DOCS_FOLDER):
        # Skip hidden directories
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        
        for file in files:
            # Skip ignored files (like README.md)
            if file in IGNORED_FILES:
                continue
            if Path(file).suffix.lower() in supported_extensions:
                documents.append(os.path.join(root, file))
    
    return documents


def delete_file_chunks(collection, file_hash: str):
    """Delete all chunks for a specific file from the collection."""
    try:
        # Get all IDs that start with this file hash
        results = collection.get(
            where={"source": {"$ne": ""}},  # Get all
            include=["metadatas"]
        )
        
        if results and results["ids"]:
            ids_to_delete = [
                id for id in results["ids"] 
                if id.startswith(file_hash)
            ]
            if ids_to_delete:
                collection.delete(ids=ids_to_delete)
                return len(ids_to_delete)
    except Exception as e:
        print(f"   ⚠️ Error deleting chunks: {e}")
    return 0


def index_documents(full_reindex: bool = False):
    """Main indexing function with incremental support."""
    print("📚 Adopt AI Knowledge Base Indexer")
    print("=" * 50)
    
    if full_reindex:
        print("   Mode: FULL RE-INDEX (deleting existing data)")
    else:
        print("   Mode: INCREMENTAL (only new/modified files)")
    
    # Check if docs folder exists
    if not os.path.exists(DOCS_FOLDER):
        print(f"\n❌ Docs folder not found: {DOCS_FOLDER}")
        print("   Create the folder and add your documents first.")
        return
    
    # Initialize ChromaDB with persistence
    print(f"\n🔧 Initializing ChromaDB...")
    print(f"   Persist directory: {CHROMA_PERSIST_DIR}")
    
    client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    
    # Handle full re-index
    if full_reindex:
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"   Deleted existing collection: {COLLECTION_NAME}")
        except ValueError:
            pass
        # Clear index state
        if os.path.exists(INDEX_STATE_FILE):
            os.remove(INDEX_STATE_FILE)
    
    # Create or get collection
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "Adopt AI knowledge base for sales outreach RAG"}
    )
    
    # Load previous index state
    index_state = {} if full_reindex else load_index_state()
    
    # Find all documents
    print(f"\n📂 Scanning for documents in: {DOCS_FOLDER}")
    documents = get_all_documents()
    
    if not documents:
        print("   ⚠️ No documents found!")
        print(f"   Add .md, .txt, or .pdf files to {DOCS_FOLDER}")
        return
    
    print(f"   Found {len(documents)} documents")
    
    # Determine which files need processing
    new_files = []
    modified_files = []
    unchanged_files = []
    current_file_hashes = {}
    
    for doc_path in documents:
        rel_path = os.path.relpath(doc_path, DOCS_FOLDER)
        current_hash = get_file_hash(doc_path)
        current_file_hashes[rel_path] = current_hash
        
        if rel_path not in index_state:
            new_files.append((doc_path, rel_path, current_hash))
        elif index_state[rel_path] != current_hash:
            modified_files.append((doc_path, rel_path, current_hash))
        else:
            unchanged_files.append(rel_path)
    
    # Find deleted files (in index but not on disk)
    deleted_files = [
        rel_path for rel_path in index_state.keys() 
        if rel_path not in current_file_hashes
    ]
    
    print(f"\n📊 Index Status:")
    print(f"   - New files: {len(new_files)}")
    print(f"   - Modified files: {len(modified_files)}")
    print(f"   - Unchanged files: {len(unchanged_files)}")
    print(f"   - Deleted files: {len(deleted_files)}")
    
    # Handle deleted files
    for rel_path in deleted_files:
        print(f"   🗑️ Removing deleted file from index: {rel_path}")
        old_hash = index_state[rel_path]
        delete_file_chunks(collection, old_hash)
        del index_state[rel_path]
    
    # Handle modified files (delete old chunks first)
    for doc_path, rel_path, current_hash in modified_files:
        print(f"   🔄 Re-indexing modified file: {rel_path}")
        old_hash = index_state.get(rel_path)
        if old_hash:
            delete_file_chunks(collection, old_hash)
    
    # Process new and modified files
    files_to_process = new_files + modified_files
    
    if not files_to_process:
        print("\n✅ No new or modified files to index!")
        print(f"   Total chunks in collection: {collection.count()}")
        return
    
    print(f"\n📝 Processing {len(files_to_process)} files...")
    
    all_chunks = []
    all_metadatas = []
    all_ids = []
    
    for doc_path, rel_path, file_hash in files_to_process:
        status = "NEW" if (doc_path, rel_path, file_hash) in new_files else "MODIFIED"
        print(f"   [{status}] {rel_path}")
        
        # Load document content
        content = load_document(doc_path)
        if not content or not content.strip():
            print(f"      ⚠️ Empty or unreadable, skipping")
            continue
        
        # Get metadata
        metadata = get_document_metadata(doc_path)
        
        # Chunk the document
        chunks = chunk_text(content)
        print(f"      → {len(chunks)} chunks")
        
        # Add chunks with metadata
        for i, chunk in enumerate(chunks):
            chunk_id = f"{file_hash}_{i}"
            chunk_metadata = {
                **metadata,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "file_hash": file_hash,
            }
            
            all_chunks.append(chunk)
            all_metadatas.append(chunk_metadata)
            all_ids.append(chunk_id)
        
        # Update index state
        index_state[rel_path] = file_hash
    
    if not all_chunks:
        print("\n⚠️ No content to index!")
        save_index_state(index_state)
        return
    
    # Add to ChromaDB
    print(f"\n🔄 Indexing {len(all_chunks)} chunks into ChromaDB...")
    
    # ChromaDB has a batch limit, process in batches
    batch_size = 100
    for i in range(0, len(all_chunks), batch_size):
        batch_end = min(i + batch_size, len(all_chunks))
        collection.add(
            documents=all_chunks[i:batch_end],
            metadatas=all_metadatas[i:batch_end],
            ids=all_ids[i:batch_end]
        )
        print(f"   Indexed chunks {i+1}-{batch_end}")
    
    # Save index state
    save_index_state(index_state)
    
    # Verify
    count = collection.count()
    print(f"\n✅ Indexing complete!")
    print(f"   Total chunks in collection: {count}")
    print(f"   Persisted to: {CHROMA_PERSIST_DIR}")
    
    # Show sample query
    print(f"\n🔍 Sample query test:")
    results = collection.query(
        query_texts=["What are Adopt AI's main capabilities?"],
        n_results=2
    )
    
    if results["documents"] and results["documents"][0]:
        print(f"   Query: 'What are Adopt AI's main capabilities?'")
        print(f"   Found {len(results['documents'][0])} relevant chunks")
        for i, (doc, meta) in enumerate(zip(results["documents"][0], results["metadatas"][0])):
            print(f"   [{i+1}] From: {meta.get('source', 'unknown')}")
            print(f"       Preview: {doc[:100]}...")
    else:
        print("   No results found (add more documents!)")


if __name__ == "__main__":
    # Check for --full flag
    full_reindex = "--full" in sys.argv
    index_documents(full_reindex=full_reindex)
